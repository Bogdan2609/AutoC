"""AutoC v0.41.2 offline release gate; not financial or supplier-write GO."""
import release_gate_v039 as base
base.RELEASE = '0.41.2'
base.REQUIRED_FILES += (
    'app/integrations/suppliers/factory.py',
    'app/integrations/suppliers/order_readback.py',
    'app/integrations/suppliers/order_transport.py',
    'tests/test_supplier_order_readback_v0412.py',
    'docs/AUTOCODEX_V0412_READBACK_RU.md',
)
if __name__ == '__main__':
    raise SystemExit(base.main())
