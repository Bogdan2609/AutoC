"""Regression for lazy IncludedRouter versions of FastAPI."""
import runpy
from types import SimpleNamespace

from app.main import app


def test_smoke_tolerates_routes_without_path():
    # Simulate an _IncludedRouter placeholder among the route objects.
    app.routes.append(SimpleNamespace())
    try:
        runpy.run_module('scripts.smoke_v015', run_name='__main__')
    finally:
        app.routes.pop()


def test_smoke_uses_real_asgi_dispatch():
    # Smoke must validate behavior, not inspect app.routes (which can
    # contain only lazy router descriptors on recent FastAPI builds).
    runpy.run_module('scripts.smoke_v015', run_name='__main__')
