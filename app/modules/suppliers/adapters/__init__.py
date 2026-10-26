"""Legacy compatibility import path: no parallel implementation."""
from app.modules.offer_engine.providers import (
    Adeo as AdeoAdapter, Rossko as RosskoAdapter,
    PartKom as PartkomAdapter, Forum as ForumAdapter, get_adapter,
)
