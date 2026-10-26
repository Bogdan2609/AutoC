"""Lightweight static regression checks for admin v0.2 styling (no DB or external API)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'admin-ui' / 'src'


def test_designer_tokens_are_present_in_both_themes():
    theme = (ROOT / 'global.css').read_text(encoding='utf-8')
    required = (
        '--background', '--foreground', '--card', '--card-foreground',
        '--popover', '--popover-foreground', '--primary', '--primary-foreground',
        '--secondary', '--secondary-foreground', '--muted', '--muted-foreground',
        '--accent', '--accent-foreground', '--destructive', '--border',
        '--input', '--ring', '--sidebar', '--sidebar-primary', '--sidebar-ring',
        '--chart-1', '--chart-2', '--chart-3', '--chart-4', '--chart-5',
    )
    light, dark = theme.split('.dark {', maxsplit=1)
    for token in required:
        assert token + ':' in light, token + ' missing light'
        assert token + ':' in dark, token + ' missing dark'
    assert '--radius: 0.875rem;' in light
    assert '--background: oklch(1 0 0);' in light
    assert '--background: oklch(0.141 0.005 285.823);' in dark


def test_admin_uses_shadcn_components_and_existing_backend():
    app = (ROOT / 'App.tsx').read_text(encoding='utf-8')
    dashboard = (ROOT / 'pages' / 'Dashboard.tsx').read_text(encoding='utf-8')
    css = (ROOT / 'index.css').read_text(encoding='utf-8')
    assert 'components/ui/button' in app
    assert 'components/ui/card' in app
    assert 'components/ui/separator' in app
    assert 'components/ui/badge' in dashboard
    assert "'/operator'" in app or 'href="/operator"' in app
    assert 'Ctrl K' in app
    assert 'WORKSPACE}/summary' in dashboard
    assert '@import "./global.css"' in css
    assert '--color-sidebar' in css
