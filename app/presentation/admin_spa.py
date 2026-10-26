"""Read-only shell for the separately compiled admin-ui Vite SPA.

No backend token is embedded into HTML or assets. Data access stays behind the
existing require_admin dependency on every API route.
"""
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

DIST = Path(__file__).resolve().parents[2] / 'admin-dist'
router = APIRouter(include_in_schema=False)

HEADERS = {
    'Cache-Control': 'no-store',
    'X-Robots-Tag': 'noindex, nofollow',
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'same-origin',
    'Content-Security-Policy': (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; img-src 'self' data:; font-src 'self'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ),
}

@router.get('/admin')
@router.get('/admin/{route:path}')
async def admin_page(route: str = ''):
    index = DIST / 'index.html'
    if not index.is_file():
        raise HTTPException(status_code=503, detail='Admin frontend is not built')
    # SPA routes only. Do not expose arbitrary files from dist via this route.
    # Built JS and CSS are served separately by the /admin/assets mount.
    if route and (route.startswith('assets/') or '.' in route.split('/')[-1]):
        raise HTTPException(status_code=404, detail='Not found')
    return FileResponse(index, media_type='text/html; charset=utf-8', headers=HEADERS)
