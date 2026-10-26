"""AutoC 0.41 static preflight; never authorizes or triggers real purchases."""
import release_gate_v039 as previous

previous.RELEASE = '0.41.0'
previous.REQUIRED_FILES += (
    'app/integrations/suppliers/factory.py',
    'app/integrations/suppliers/registry.py',
    'app/integrations/suppliers/capabilities.py',
    'app/integrations/suppliers/diagnostics.py',
    'tests/test_supplier_factory_v041.py',
    'docs/AUTOCODEX_V041_SUPPLIER_GATEWAY_RU.md',
)

if __name__ == '__main__':
    raise SystemExit(previous.main())
