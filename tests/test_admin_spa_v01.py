"""Minimal regressions for SPA serving; no supplier calls or DB are involved."""
import asyncio
from pathlib import Path
import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse
from app.presentation import admin_spa


def test_new_admin_route_exists():
    from app.main import app
    assert any(getattr(route, 'path', '') == '/admin' for route in app.routes)
    assert any(getattr(route, 'path', '') == '/operator' for route in app.routes)


def test_admin_missing_build_is_explicit_503(tmp_path, monkeypatch):
    monkeypatch.setattr(admin_spa, 'DIST', tmp_path)
    with pytest.raises(HTTPException) as error:
        asyncio.run(admin_spa.admin_page())
    assert error.value.status_code == 503


def test_spa_index_is_served_without_secrets(tmp_path, monkeypatch):
    (tmp_path / 'index.html').write_text('<h1>Autocodex</h1>', encoding='utf8')
    monkeypatch.setattr(admin_spa, 'DIST', tmp_path)
    result = asyncio.run(admin_spa.admin_page(route='orders/123'))
    assert isinstance(result, FileResponse)
    assert result.headers['cache-control'] == 'no-store'
    assert result.headers['x-robots-tag'] == 'noindex, nofollow'
    assert "'unsafe-inline'" not in result.headers['content-security-policy']
    with pytest.raises(HTTPException) as error:
        asyncio.run(admin_spa.admin_page(route='assets/something.js'))
    assert error.value.status_code == 404


def test_admin_html_is_not_generated_using_api_token():
    source = Path(admin_spa.__file__).read_text(encoding='utf8')
    assert 'get_settings().admin_api_token' not in source
    assert 'X-Admin-Token' not in source
