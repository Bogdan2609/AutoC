"""Typed supplier binding: a supplier + independent account + verified courier node.

The caller still owns quotas, durable job lease, DB transaction and revalidation.
This gateway NEVER sends a supplier order. Missing/unsupported write methods are
not silently emulated; no automatic fallback across accounts or destinations.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Mapping
from uuid import UUID

from .capabilities import SupplierFeatures, features
from .registry import SupplierRegistry, UnsupportedSupplier, legacy_registry


class SupplierBindingError(ValueError):
    """Fail-closed supplier/account/destination error, no secrets or PII."""


class SupplierWriteForbidden(PermissionError):
    """The read gateway is not an authorization boundary for an outbound write."""


@dataclass(frozen=True)
class SupplierBinding:
    code: str
    supplier_id: UUID | None
    account_id: UUID | None
    destination_id: UUID | None
    node_id: UUID | None
    merchant_id: UUID | None
    credential_profile: str | None
    capabilities: SupplierFeatures
    adapter: Any = field(repr=False, compare=False)
    config: Any = field(repr=False, compare=False)

    @property
    def scoped(self) -> bool:
        return self.account_id is not None

    @property
    def cache_scope(self) -> str:
        # UUID-only; never store credentials in cache keys.
        return (f'{self.account_id}:{self.destination_id}' if self.scoped
                else 'legacy:default')

    async def search(self, brand: str, article: str):
        # Legacy read contracts accept an optional config argument. Existing
        # test doubles may only accept two positional arguments, intentionally.
        if not self.capabilities.search:
            raise SupplierBindingError('Supplier search unsupported')
        rows = (await self.adapter.search(brand, article, self.config)
                if self.scoped else await self.adapter.search(brand, article))
        if not isinstance(rows, list) or len(rows) > 250:
            raise SupplierBindingError('Supplier response invalid or too large')
        # Do not leak one supplier's offers into another supplier's snapshot.
        if any(getattr(row, 'code', None) != self.code for row in rows):
            raise SupplierBindingError('Supplier response identity mismatch')
        return rows

    async def check_stock(self, key: str, brand: str, article: str, quantity: int):
        """Re-query exact SKU; no reservation, no supplier write, no price hold."""
        from app.modules.catalog.normalize import normalize_article, normalize_brand
        from app.modules.offer_engine.supplier_errors import UpstreamBusinessError
        if type(quantity) is not int or quantity < 1 or not all((key, brand, article)):
            raise SupplierBindingError('Invalid supplier offer reference')
        if not self.capabilities.stock_check:
            raise SupplierBindingError('Supplier stock verification unsupported')
        # check_stock performs a network read on the *same* bound account.
        checker = getattr(self.adapter, 'check_stock', None)
        if callable(checker):
            offer = (await checker(key, brand, article, quantity, self.config)
                     if self.scoped else await checker(key, brand, article, quantity))
        else:
            rows = await self.search(brand, article)
            offer = next((x for x in rows if x.key == key and
                          normalize_brand(x.brand) == normalize_brand(brand) and
                          normalize_article(x.article) == normalize_article(article)), None)
        if (offer is None or getattr(offer, 'code', None) != self.code or
                getattr(offer, 'key', None) != key or
                normalize_brand(offer.brand) != normalize_brand(brand) or
                normalize_article(offer.article) != normalize_article(article) or
                offer.qty is None or offer.qty < quantity or
                quantity < offer.minimum or offer.multiple < 1 or quantity % offer.multiple):
            raise UpstreamBusinessError('Selected supplier offer unavailable')
        return offer

    def preview_purchase(self, intent):
        """Pure preview, not payment or procurement approval; never POSTs."""
        if not self.scoped:
            raise SupplierBindingError('Account-scoped supplier purchase preview required')
        if intent.supplier != self.code or not self.capabilities.purchase_preview:
            raise SupplierBindingError('Supplier purchase preview unsupported')
        from app.modules.marketplace.purchase_gateway import build_purchase_plan
        plan = build_purchase_plan(intent)  # separately requires verified courier
        if plan.dispatch_authorized:
            raise SupplierWriteForbidden('Supplier preview cannot authorize dispatch')
        return plan

    async def create_order(self, *args, **kwargs):
        raise SupplierWriteForbidden('Read gateway never dispatches a supplier order')

    async def cancel_order(self, *args, **kwargs):
        raise SupplierWriteForbidden('Supplier cancellation contract not authorized')


class SupplierFactory:
    def __init__(self, registry: SupplierRegistry | Mapping[str, Any] | None = None):
        if registry is None:
            registry = legacy_registry()
        self.registry = registry if isinstance(registry, SupplierRegistry) else SupplierRegistry(registry)

    def for_search(self, code: str, *, supplier=None, account=None,
                   destination=None, node=None) -> SupplierBinding:
        adapter = self.registry.get(code)
        feature_set = features(code)
        if account is None:
            # The caller must already have excluded any configured independent
            # account; this is only a transport binding, not DB authorization.
            if any(x is not None for x in (supplier, destination, node)):
                raise SupplierBindingError('Legacy read scope cannot include account entities')
            return SupplierBinding(code, None, None, None, None, None, None,
                                   feature_set, adapter, None)
        if any(x is None for x in (supplier, destination, node)):
            raise SupplierBindingError('Supplier account needs a destination and node')
        # Recheck before a supplier HTTP read even if enqueue previously checked.
        if (getattr(supplier, 'code', None) != code or
                account.supplier_id != supplier.id or destination.account_id != account.id or
                destination.node_id != node.id or node.merchant_id != account.merchant_id or
                not all((account.enabled, account.read_enabled, destination.enabled,
                         destination.verified, node.active))):
            raise SupplierBindingError('Supplier account or receiving route not verified')
        from app.core.config import get_settings
        from app.modules.marketplace.account_credentials import ready, for_account
        if not get_settings().supplier_api_enabled or not ready(code, account, destination):
            raise SupplierBindingError('Supplier account credentials not ready')
        config = for_account(code, account, destination)
        scope = f'{account.id}:{destination.id}'
        if getattr(config, 'supplier_account_scope', '') != scope:
            raise SupplierBindingError('Supplier transport scope mismatch')
        return SupplierBinding(code, supplier.id, account.id, destination.id, node.id,
                               account.merchant_id, account.credential_profile,
                               feature_set, adapter, config)
