"""Supplier Gateway v0.41 regression: synthetic providers only, NO external APIs."""
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.routing import iter_route_contexts

from app.integrations.suppliers.capabilities import capabilities_matrix, features
from app.integrations.suppliers.diagnostics import matrix, readiness
from app.integrations.suppliers.factory import (SupplierBindingError,
    SupplierFactory, SupplierWriteForbidden)
from app.integrations.suppliers.registry import (SUPPLIER_CODES, SupplierRegistry,
    UnsupportedSupplier, legacy_registry)
from app.modules.offer_engine.contracts import Offer
from app.modules.offer_engine.providers import PROVIDERS
from app.modules.marketplace.purchase_gateway import PurchaseIntent


class FakeProvider:
    code = 'rossko'
    def __init__(self):
        self.calls = []
    async def search(self, brand, article, config=None):
        self.calls.append((brand, article, getattr(config, 'supplier_account_scope', None)))
        return [Offer('rossko','stock-one',brand,article,Decimal('100.00'),8,1,'live-id', 'WH1')]


def scope():
    sid, aid, did, nid, mid = (uuid4() for _ in range(5))
    supplier = SimpleNamespace(code='rossko', id=sid)
    account = SimpleNamespace(id=aid, supplier_id=sid, merchant_id=mid,
        credential_profile='SAFE', enabled=True, read_enabled=True)
    destination = SimpleNamespace(id=did, account_id=aid, node_id=nid,
        enabled=True, verified=True)
    node = SimpleNamespace(id=nid, merchant_id=mid, active=True,
        receiving_contact_name='WAREHOUSE PRIVATE NAME',
        receiving_contact_phone='+70000000001')
    return supplier, account, destination, node


def bind(monkeypatch, factory=None):
    from app.modules.marketplace import account_credentials
    from app.integrations.suppliers import factory as factory_module
    sup, acc, dest, node = scope()
    monkeypatch.setattr(factory_module, 'get_settings', lambda: None, raising=False)
    # for_search resolves settings using local import, not factory_module global.
    from app.core import config
    monkeypatch.setattr(config, 'get_settings', lambda: SimpleNamespace(supplier_api_enabled=True))
    monkeypatch.setattr(account_credentials, 'ready', lambda code, a, d: True)
    monkeypatch.setattr(account_credentials, 'for_account', lambda code,a,d:
        SimpleNamespace(supplier_account_scope=f'{a.id}:{d.id}',
                        rossko_key1='PRIVATE API SECRET', rossko_key2='PRIVATE API SECRET'))
    return (factory or SupplierFactory({'rossko': FakeProvider()})).for_search(
        'rossko', supplier=sup, account=acc, destination=dest, node=node)


def test_single_registry_reuses_existing_transport_objects():
    registry = legacy_registry()
    for code in SUPPLIER_CODES:
        assert registry.get(code) is PROVIDERS[code]
    with pytest.raises(UnsupportedSupplier):
        registry.get('unknown')


def test_matrix_inherits_existing_supplier_purchase_truth():
    data = matrix()
    assert data['outbound_supplier_writes_allowed'] is False
    assert len(data['suppliers']) == 4
    assert data['suppliers'][0]['code'] == 'rossko'
    for item in data['suppliers']:
        assert item['search'] and item['stock_check']
        assert item['gateway_dispatch'] is False
        assert item['purchase_preview'] is True
    assert features('rossko').contract_live_create is True
    assert features('adeo').contract_live_create is False
    assert features('forum-auto').contract_cancel is False
    with pytest.raises(UnsupportedSupplier):
        features('missing')


def test_reject_unknown_supplier_and_bad_adapter():
    with pytest.raises(UnsupportedSupplier):
        SupplierFactory({}).for_search('rossko')
    with pytest.raises(UnsupportedSupplier):
        SupplierRegistry({'rossko': SimpleNamespace(code='adeo', search=lambda:None)}).get('rossko')


@pytest.mark.asyncio
async def test_account_bound_search_exact_scope_and_never_secrets_in_repr(monkeypatch):
    provider = FakeProvider()
    bound = bind(monkeypatch, SupplierFactory({'rossko': provider}))
    assert bound.scoped
    assert str(bound.account_id) in bound.cache_scope
    assert 'PRIVATE' not in repr(bound)
    assert 'WAREHOUSE PRIVATE' not in repr(bound)
    result = await bound.search('TRIALLI', 'CF705')
    assert result[0].price == Decimal('100.00')
    assert provider.calls == [('TRIALLI','CF705',bound.cache_scope)]
    offer = await bound.check_stock('stock-one','TRIALLI','CF705',3)
    assert offer.qty == 8


@pytest.mark.asyncio
async def test_never_dispatch_or_cancel_from_read_gateway(monkeypatch):
    bound = bind(monkeypatch)
    with pytest.raises(SupplierWriteForbidden):
        await bound.create_order('unknown-attempt')
    with pytest.raises(SupplierWriteForbidden):
        await bound.cancel_order('external-id')


@pytest.mark.parametrize('change', [
    ('account','enabled',False),('account','read_enabled',False),
    ('destination','verified',False),('destination','enabled',False),
    ('node','active',False),('destination','account_id',uuid4()),
    ('node','merchant_id',uuid4()),('supplier','code','adeo'),
])
def test_fail_closed_route_binding(monkeypatch,change):
    from app.modules.marketplace import account_credentials
    from app.core import config
    monkeypatch.setattr(config, 'get_settings', lambda: SimpleNamespace(supplier_api_enabled=True))
    monkeypatch.setattr(account_credentials,'ready',lambda *args:True)
    monkeypatch.setattr(account_credentials,'for_account',lambda code,a,d:SimpleNamespace(supplier_account_scope=f'{a.id}:{d.id}'))
    s,a,d,n=scope()
    entities = {'supplier':s,'account':a,'destination':d,'node':n}
    setattr(entities[change[0]],change[1],change[2])
    with pytest.raises(SupplierBindingError):
        SupplierFactory({'rossko':FakeProvider()}).for_search('rossko',supplier=s,account=a,destination=d,node=n)


def test_legacy_scope_never_accepts_partial_account():
    f = SupplierFactory({'rossko':FakeProvider()})
    assert f.for_search('rossko').cache_scope == 'legacy:default'
    with pytest.raises(SupplierBindingError):
        f.for_search('rossko',account=scope()[1])


@pytest.mark.asyncio
async def test_invalid_offer_identity_not_published():
    class Foreign:
        async def search(self, brand,article):
            return [Offer('adeo','wrong',brand,article,Decimal('50.00'),1,1,'token')]
    bound=SupplierFactory({'rossko':Foreign()}).for_search('rossko')
    with pytest.raises(SupplierBindingError,match='identity'):
        await bound.search('TRIALLI','CF705')


@pytest.mark.asyncio
async def test_read_gateway_rechecks_minimum_multiple_and_exact_sku():
    class Records:
        async def search(self, brand,article):
            return [Offer('rossko','x','TRIALLI','CF705',Decimal('100.00'),5,1,'t',minimum=2,multiple=2)]
    b=SupplierFactory({'rossko':Records()}).for_search('rossko')
    from app.modules.offer_engine.supplier_errors import UpstreamBusinessError
    with pytest.raises(UpstreamBusinessError):
        await b.check_stock('x','TRIALLI','CF705',3)
    with pytest.raises(UpstreamBusinessError):
        await b.check_stock('x','TRIALLI','CF706',2)
    assert (await b.check_stock('x','TRIALLI','CF705',4)).qty == 5


def test_purchase_preview_courier_only_but_not_authorization(monkeypatch):
    b=bind(monkeypatch)
    intent=PurchaseIntent('rossko',str(uuid4()),'ref-123456789','TRIALLI','CF705',1,
        Decimal('100.00'),Decimal('100.00'),'stock-one',warehouse='WH1',
        delivery_mode='courier',courier_route_verified=True)
    plan=b.preview_purchase(intent)
    assert plan.dispatch_authorized is False
    with pytest.raises(ValueError):
        b.preview_purchase(replace(intent,delivery_mode='pickup'))


def test_admin_gateway_routes_are_registered_without_live_supplier_calls():
    from app.main import app
    paths={r.path for r in iter_route_contexts(app.routes)}
    assert '/api/v1/admin/marketplace/supplier-integrations/capabilities' in paths
    assert '/api/v1/admin/marketplace/supplier-accounts/{account_id}/integration-readiness' in paths
