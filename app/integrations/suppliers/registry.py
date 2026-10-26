"""Single registry backed by the EXISTING provider instances, not a second stack.

Preserve monkeypatchable providers.PROVIDERS and its original singleton objects.
A new supplier requires an explicit code and an adapter implementing search.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Mapping, Any

SUPPLIER_CODES = ('rossko', 'adeo', 'partkom', 'forum-auto')


class UnsupportedSupplier(ValueError):
    """Safe, fixed public message without reflecting untrusted supplier input."""


@dataclass(frozen=True)
class SupplierRegistry:
    # A mapping is intentionally referenced, not copied. Legacy test doubles and
    # replacement adapters continue to be visible without a second registry.
    adapters: Mapping[str, Any]

    def get(self, code: str):
        if code not in SUPPLIER_CODES:
            raise UnsupportedSupplier('Unsupported supplier')
        adapter = self.adapters.get(code)
        if adapter is None or getattr(adapter, 'code', code) != code or not callable(getattr(adapter, 'search', None)):
            raise UnsupportedSupplier('Supplier read adapter unavailable')
        return adapter

    def codes(self) -> tuple[str, ...]:
        return SUPPLIER_CODES


def legacy_registry():
    # Delayed import is essential: providers imports the registry for get_adapter.
    from app.modules.offer_engine.providers import PROVIDERS
    return SupplierRegistry(PROVIDERS)
