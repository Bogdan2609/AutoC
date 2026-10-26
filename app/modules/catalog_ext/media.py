"""Safe, immutable media blobs with explicit operator approval.

- Accepts JPEG, PNG, WEBP, PDFs only, verifying real image bytes with Pillow.
- Removes image metadata, outputs normalized WEBP and a WEBP thumbnail.
- PDF served as attachment; never inline HTML/SVG/remote fetching.
- Mounted persistent media root (Docker named volume), not app source tree.
"""
import hashlib
import io
import os
import uuid
from pathlib import Path
import anyio

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_session
from app.core.security import require_admin
from app.modules.catalog.models import Product, Brand
from .models import ProductMedia

admin=APIRouter(prefix='/api/v1/backoffice/catalog-v021',tags=['catalog-media-admin'],dependencies=[Depends(require_admin)])
public=APIRouter(prefix='/api/v1/catalog-v021',tags=['catalog-media'])
MAX_IMAGE=10*1024*1024
MAX_PDF=16*1024*1024
MAX_PIXELS=30_000_000
Image.MAX_IMAGE_PIXELS=MAX_PIXELS
_IMAGE_LIMITER=anyio.CapacityLimiter(2)


def root():
    return Path(os.environ.get('CATALOG_MEDIA_ROOT','/srv/app/media')).resolve()


def forbidden(code,detail):
    raise HTTPException(code,detail)


def path_for(key,variant):
    if len(key)!=36 or str(uuid.UUID(key))!=key:forbidden(404,'Media not found')
    if variant not in ('original','thumb'):forbidden(404,'Media variant not found')
    return root()/key[:2]/key/(variant+'.bin')


def normalize_image(raw):
    try:
        with Image.open(io.BytesIO(raw)) as im:
            if im.format not in ('JPEG','PNG','WEBP'):
                forbidden(415,'Image format not allowed')
            if im.width<=0 or im.height<=0 or im.width*im.height>MAX_PIXELS:
                forbidden(413,'Image pixel count too large')
            im=ImageOps.exif_transpose(im)
            if im.mode not in ('RGB','RGBA'):
                im=im.convert('RGBA' if 'A' in im.getbands() else 'RGB')
            # Remux safely: no EXIF/GPS, no metadata, no arbitrary embedded bytes.
            clean=im.copy()
            original=io.BytesIO();clean.save(original,'WEBP',quality=90,method=5)
            thumb=clean.copy();thumb.thumbnail((480,480),Image.Resampling.LANCZOS)
            preview=io.BytesIO();thumb.save(preview,'WEBP',quality=78,method=4)
            return original.getvalue(),preview.getvalue(),im.width,im.height
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        forbidden(415,'Invalid or unsafe image content')


def validate_pdf(raw):
    """Conservative structure check. Not a substitute for antivirus scanning."""
    try:
        reader=PdfReader(io.BytesIO(raw), strict=True)
        count=len(reader.pages)
        if not 1<=count<=300:
            forbidden(415,'PDF page count is not allowed')
        root=reader.trailer['/Root']
        if '/OpenAction' in root or '/AA' in root or '/Names' in root or '/AcroForm' in root:
            forbidden(415,'Active or embedded PDF content is not accepted')
    except HTTPException:
        raise
    except Exception:
        forbidden(415,'Invalid PDF structure')


async def read_limited(upload,limit):
    chunks=[];size=0
    while True:
        chunk=await upload.read(min(65536,limit-size+1))
        if not chunk:break
        size+=len(chunk)
        if size>limit:forbidden(413,'File exceeds upload limit')
        chunks.append(chunk)
    if not chunks:forbidden(422,'Empty file')
    return b''.join(chunks)


def safe_name(name):
    return (name or 'upload').replace('\\','/').split('/')[-1][:180].replace('\x00','_') or 'upload'


@admin.post('/media',status_code=201)
async def upload_media(kind:str=Form(...),product_id:uuid.UUID|None=Form(None),brand_id:uuid.UUID|None=Form(None),
                       position:int=Form(0,ge=0,le=100000),alt_text:str|None=Form(None,max_length=250),
                       upload:UploadFile=File(...),db:AsyncSession=Depends(get_session)):
    if kind not in ('image','logo','document'):forbidden(422,'Unsupported media kind')
    if (product_id is None)==(brand_id is None):forbidden(422,'Select exactly one product or brand')
    if kind=='logo' and not brand_id:forbidden(422,'Brand logo must belong to a brand')
    if kind=='document' and not product_id:forbidden(422,'Documents must belong to a product')
    if product_id and not await db.get(Product,product_id):forbidden(404,'Product not found')
    if brand_id and not await db.get(Brand,brand_id):forbidden(404,'Brand not found')
    name=safe_name(upload.filename)
    if kind=='document':
        raw=await read_limited(upload,MAX_PDF)
        if not raw.startswith(b'%PDF-') or b'%%EOF' not in raw[-2048:]:
            forbidden(415,'Only valid-looking PDF documents are supported')
        await anyio.to_thread.run_sync(validate_pdf,raw,limiter=_IMAGE_LIMITER)
        data=raw;thumb=None;mime='application/pdf';width=height=None
    else:
        raw=await read_limited(upload,MAX_IMAGE)
        data,thumb,width,height=await anyio.to_thread.run_sync(normalize_image,raw,limiter=_IMAGE_LIMITER)
        mime='image/webp'
    media_id=uuid.uuid4();key=str(media_id);folder=path_for(key,'original').parent
    # Atomic per-file writes before committing DB row; on error clean up.
    try:
        folder.mkdir(parents=True,exist_ok=False)
        original=path_for(key,'original')
        with original.open('xb') as fh:fh.write(data)
        if thumb is not None:
            with path_for(key,'thumb').open('xb') as fh:fh.write(thumb)
        row=ProductMedia(id=media_id,product_id=product_id,brand_id=brand_id,kind=kind,mime_type=mime,
            original_filename=name,storage_key=key,sha256=hashlib.sha256(data).hexdigest(),size_bytes=len(data),
            width=width,height=height,position=position,alt_text=alt_text,status='draft')
        db.add(row)
        await db.commit()
    except Exception:
        await db.rollback()
        for f in (folder/'original.bin',folder/'thumb.bin'):
            try:f.unlink(missing_ok=True)
            except OSError:pass
        try:folder.rmdir()
        except OSError:pass
        raise
    return info(row)


def info(row):
    return {'id':str(row.id),'product_id':str(row.product_id) if row.product_id else None,
            'brand_id':str(row.brand_id) if row.brand_id else None,'kind':row.kind,
            'status':row.status,'position':row.position,'mime_type':row.mime_type,
            'filename':row.original_filename,'size_bytes':row.size_bytes,
            'sha256':row.sha256,'width':row.width,'height':row.height,
            'alt_text':row.alt_text,
            'url':f'/api/v1/catalog-v021/media/{row.id}/file',
            'thumb_url':f'/api/v1/catalog-v021/media/{row.id}/file?variant=thumb' if row.kind!='document' else None}


@admin.patch('/media/{media_id}')
async def update_media(media_id:uuid.UUID,status:str|None=None,position:int|None=None,
                       alt_text:str|None=None,db:AsyncSession=Depends(get_session)):
    row=await db.get(ProductMedia,media_id)
    if not row:forbidden(404,'Media not found')
    if status is not None:
        if status not in ('draft','approved','rejected'):forbidden(422,'Invalid status')
        row.status=status
    if position is not None:
        if not 0<=position<=100000:forbidden(422,'Invalid position')
        row.position=position
    if alt_text is not None:
        if len(alt_text)>250:forbidden(422,'Alt text too long')
        row.alt_text=alt_text
    await db.commit();return info(row)


@admin.delete('/media/{media_id}')
async def delete_media(media_id:uuid.UUID,db:AsyncSession=Depends(get_session)):
    row=await db.get(ProductMedia,media_id)
    if not row:forbidden(404,'Media not found')
    # Physical blob retained intentionally; a separate audited purge will be added.
    row.status='rejected';await db.commit()
    return {'removed_from_public':True,'bytes_retained':True}


@admin.get('/media/{media_id}')
async def operator_media(media_id:uuid.UUID,db:AsyncSession=Depends(get_session)):
    row=await db.get(ProductMedia,media_id)
    if not row:forbidden(404,'Media not found')
    return info(row)


@public.get('/products/{product_id}/media')
async def public_product_media(product_id:uuid.UUID,db:AsyncSession=Depends(get_session)):
    product=await db.get(Product,product_id)
    if not product or product.status!='published':forbidden(404,'Product not found')
    rows=(await db.scalars(select(ProductMedia).where(ProductMedia.product_id==product_id,
        ProductMedia.status=='approved').order_by(ProductMedia.position,ProductMedia.id))).all()
    return {'product_id':str(product_id),'items':[info(row) for row in rows]}


@public.get('/brands/{brand_id}/media')
async def public_brand_media(brand_id:uuid.UUID,db:AsyncSession=Depends(get_session)):
    brand=await db.get(Brand,brand_id)
    if not brand:forbidden(404,'Brand not found')
    rows=(await db.scalars(select(ProductMedia).where(ProductMedia.brand_id==brand_id,
        ProductMedia.status=='approved').order_by(ProductMedia.position,ProductMedia.id))).all()
    return {'brand_id':str(brand_id),'items':[info(row) for row in rows]}


@public.get('/media/{media_id}/file')
async def serve_media(media_id:uuid.UUID,variant:str='original',db:AsyncSession=Depends(get_session)):
    row=await db.get(ProductMedia,media_id)
    if not row or row.status!='approved':forbidden(404,'Media not found')
    if row.product_id:
        product=await db.get(Product,row.product_id)
        if not product or product.status!='published':forbidden(404,'Media not available')
    if variant not in ('original','thumb') or (row.kind=='document' and variant=='thumb'):
        forbidden(404,'Variant unavailable')
    filepath=path_for(row.storage_key,variant)
    if not filepath.is_file():forbidden(404,'Media bytes unavailable')
    attachment=row.kind=='document'
    response=FileResponse(filepath,media_type='application/pdf' if attachment else 'image/webp',
        filename=row.original_filename if attachment else None,
        content_disposition_type='attachment' if attachment else 'inline')
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Cache-Control']='private, max-age=60' if row.status!='approved' else 'public, max-age=120'
    return response
