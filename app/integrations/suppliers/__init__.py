"""Safe supplier gateway v0.41: read adapters and capabilities, never writes."""
from .factory import SupplierFactory, SupplierBinding, SupplierBindingError
from .registry import SUPPLIER_CODES, SupplierRegistry, UnsupportedSupplier

__all__ = ('SupplierFactory', 'SupplierBinding', 'SupplierBindingError',
           'SupplierRegistry', 'SUPPLIER_CODES', 'UnsupportedSupplier')
