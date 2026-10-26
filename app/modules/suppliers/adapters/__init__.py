"""Compatibility adapter names, resolved lazily to the one live registry.

No separate implementations or import cycles through offer_engine.providers.
"""

__all__ = ('AdeoAdapter', 'RosskoAdapter', 'PartkomAdapter', 'ForumAdapter',
           'get_adapter')


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(name)
    from app.modules.offer_engine import providers
    lookup = {
        'AdeoAdapter': providers.Adeo,
        'RosskoAdapter': providers.Rossko,
        'PartkomAdapter': providers.PartKom,
        'ForumAdapter': providers.Forum,
        'get_adapter': providers.get_adapter,
    }
    return lookup[name]
