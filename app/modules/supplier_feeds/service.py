"""Manual supplier-feed import; version switch is atomic and cannot trigger orders."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import func, insert, select, update, delete, text
from sqlalchemy.exc import IntegrityError

from app.modules.catalog.models import Brand, Product
from app.modules.marketplace.models import SupplierAccount, SupplierDestination
from app.modules.suppliers.models import Supplier
from .models import FeedBatch, FeedBrandAlias, FeedOffer, FeedSource, FeedWarehouseMap, utc
from .parsers import FeedFormatError, FeedRow, read_feed

STORAGE = Path('media') / 'supplier_feed_uploads'
BATCH_SIZE = 5000

COPY_FIELDS = (
    "id", "batch_id", "identity_hash", "source_line", "supplier_sku",
    "brand", "brand_norm", "article", "article_norm", "description",
    "warehouse_code", "purchase_price", "stock_raw", "stock_min",
    "is_lower_bound", "minimum", "multiple", "lead_days",
)


def path_for(batch: FeedBatch) -> Path:
    if not batch.stored_name or '/' in batch.stored_name or '\\' in batch.stored_name or '..' in batch.stored_name:
        raise FeedFormatError('Invalid internal filename')
    return STORAGE / batch.stored_name


def _offer(row: FeedRow, batch_id: UUID) -> dict:
    return {
        'id': uuid4(), 'batch_id':batch_id, 'identity_hash':row.identity,
        'source_line':row.line, 'supplier_sku':row.sku, 'brand':row.brand,
        'brand_norm':row.brand_norm, 'article':row.article, 'article_norm':row.article_norm,
        'description':row.description, 'warehouse_code':row.warehouse,
        'purchase_price':row.purchase_price, 'stock_raw':row.stock_raw,
        'stock_min':row.stock_min, 'is_lower_bound':row.stock_is_lower_bound,
        'minimum':row.minimum, 'multiple':row.multiple, 'lead_days':row.lead_days,
    }


async def _copy_postgres(db, rows):
    """Native Psycopg 3 COPY to a transaction-scoped temporary staging table.

    Final insert is set-based with ON CONFLICT DO NOTHING, so a duplicate
    source identity cannot abort the whole import. No untrusted SQL identifiers.
    The connection is borrowed from the CURRENT SQLAlchemy transaction.
    """
    columns=", ".join(COPY_FIELDS)
    await db.execute(text("""CREATE TEMP TABLE IF NOT EXISTS feed_chunk_tmp
        (LIKE feed_offers INCLUDING DEFAULTS) ON COMMIT DELETE ROWS"""))
    async_connection=await db.connection()
    proxy=await async_connection.get_raw_connection()
    driver=proxy.driver_connection
    # Production DATABASE_URL uses postgresql+psycopg; the driver connection
    # is a psycopg.AsyncConnection with AsyncCursor.copy().
    async with driver.cursor() as cursor:
        async with cursor.copy(f"COPY feed_chunk_tmp ({columns}) FROM STDIN") as copy:
            for row in rows:
                await copy.write_row(tuple(row[key] for key in COPY_FIELDS))
    await db.execute(text(f"""INSERT INTO feed_offers ({columns})
        SELECT {columns} FROM feed_chunk_tmp WHERE TRUE
        ON CONFLICT (batch_id, identity_hash) DO NOTHING"""))
    await db.execute(text("TRUNCATE TABLE feed_chunk_tmp"))


async def _insert_batch(db, rows):
    engine = getattr(db, 'bind', None) or getattr(getattr(db, 'session', None), 'bind', None)
    dialect = engine.dialect.name if engine is not None else 'postgresql'
    if dialect == 'postgresql' and hasattr(db,'connection'):
        await _copy_postgres(db,rows)
        return
    if dialect == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert as d_insert
    else:
        from sqlalchemy.dialects.postgresql import insert as d_insert
    statement = d_insert(FeedOffer).on_conflict_do_nothing(index_elements=['batch_id','identity_hash'])
    await db.execute(statement,rows)


async def _match(db, batch_id: UUID):
    # Correlated exact match uses indexed brands.name_norm + unique product identity.
    # An alias is honored ONLY when approved by an operator.
    # FeedBatch is a separate scope; avoid reliance on UPDATE ... FROM portability.
    # Most supplier files have direct brands, and the alias lookup uses its unique index.
    batch_supplier = (select(FeedBatch.supplier_code).where(FeedBatch.id==batch_id).scalar_subquery())
    alias_id2 = (select(FeedBrandAlias.brand_id).where(
        FeedBrandAlias.supplier_code == batch_supplier,
        FeedBrandAlias.brand_norm == FeedOffer.brand_norm,
        FeedBrandAlias.approved.is_(True)).limit(1).correlate(FeedOffer).scalar_subquery())
    direct_id = (select(Brand.id).where(Brand.name_norm==FeedOffer.brand_norm)
                 .limit(1).correlate(FeedOffer).scalar_subquery())
    effective_brand_id = func.coalesce(direct_id,alias_id2)
    product_id = (select(Product.id).where(Product.brand_id==effective_brand_id,
        Product.article_norm==FeedOffer.article_norm, Product.status=='published').limit(1)
        .correlate(FeedOffer).scalar_subquery())
    # New versions must also link to safely materialized DRAFT products.
    # The former importer matched only published products, leaving new file
    # prices disconnected from thousands of draft cards created by v0.31.3.
    # Candidate identity and current Product identity must BOTH agree.
    from app.modules.catalog_bulk.models import CatalogCandidate
    linked_draft = (select(CatalogCandidate.product_id)
        .join(Product, Product.id == CatalogCandidate.product_id)
        .where(CatalogCandidate.brand_norm == FeedOffer.brand_norm,
               CatalogCandidate.article_norm == FeedOffer.article_norm,
               CatalogCandidate.status == 'materialized',
               Product.status.in_(('draft','published')),
               Product.brand_id == effective_brand_id,
               Product.article_norm == FeedOffer.article_norm)
        .limit(1).correlate(FeedOffer).scalar_subquery())
    product_id = func.coalesce(product_id, linked_draft)
    await db.execute(update(FeedOffer).where(FeedOffer.batch_id==batch_id).values(product_id=product_id))
    return int(await db.scalar(select(func.count()).select_from(FeedOffer).where(
        FeedOffer.batch_id==batch_id,FeedOffer.product_id.is_not(None))) or 0)


async def import_batch(db, batch_id: UUID):
    batch=await db.get(FeedBatch,batch_id)
    if batch is None or batch.status not in ('queued','failed','processing'):
        raise FeedFormatError('Feed batch is not queued')
    batch.status='processing'
    batch.errors_sample=''
    batch.rows_total=0
    batch.rows_valid=0
    batch.rows_rejected=0
    batch.rows_duplicates=0
    batch.rows_matched=0
    await db.execute(delete(FeedOffer).where(FeedOffer.batch_id==batch_id))
    await db.commit()
    buffer=[]
    chunk_count=0
    errors=[]
    try:
        for line,row,err in read_feed(batch.supplier_code,path_for(batch)):
            batch.rows_total+=1
            if err:
                batch.rows_rejected+=1
                if len(errors)<20:
                    errors.append(f'{line}: {err}')
            else:
                buffer.append(_offer(row,batch_id))
                batch.rows_valid+=1
            if len(buffer)>=BATCH_SIZE:
                await _insert_batch(db,buffer)
                buffer.clear()
                chunk_count += 1
                # Amortize database fsyncs while retaining 10k-row progress checkpoints.
                # A failed version NEVER becomes active and can be restarted safely.
                if chunk_count % 10 == 0:
                    await db.commit()
        if buffer:
            await _insert_batch(db,buffer)
        await db.commit()
        actual=int(await db.scalar(select(func.count()).select_from(FeedOffer).where(FeedOffer.batch_id==batch_id)) or 0)
        batch.rows_duplicates=batch.rows_valid-actual
        batch.rows_valid=actual
        batch.errors_sample='\n'.join(errors)
        if batch.rows_total==0 or actual==0 or batch.rows_rejected>max(100, int(batch.rows_total*0.03)) or batch.rows_duplicates>max(100,int(batch.rows_total*0.01)):
            batch.status='rejected'
        else:
            source=await db.get(FeedSource,batch.supplier_code)
            old=await db.get(FeedBatch,source.active_batch_id) if source and source.active_batch_id else None
            if old and old.rows_valid and actual < int(old.rows_valid*0.65):
                batch.status='rejected'
                batch.errors_sample+='\nNew file has fewer than 65% of previous version rows'
            else:
                batch.rows_matched=await _match(db,batch_id)
                batch.status='staged'
        batch.finished_at=utc()
        if batch.status=='staged':
            from .pipeline import queue_sync
            await queue_sync(db,batch_id)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        batch=await db.get(FeedBatch,batch_id)
        if batch:
            batch.status='failed'
            batch.finished_at=utc()
            batch.errors_sample=('\n'.join(errors)+'\n'+type(exc).__name__+': '+str(exc)[:180])[-3000:]
            await db.commit()
        raise
    return batch.status


def aware_utc(dt):
    from datetime import timezone
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def publish_batch(db, batch_id: UUID, generated_at):
    batch=await db.get(FeedBatch,batch_id)
    if not batch or batch.status!='staged':
        raise HTTPException(409,'Only fully staged feed can be published')
    if not generated_at or generated_at.tzinfo is None:
        raise HTTPException(422,'Supplier file as-of time must include timezone')
    if generated_at > utc()+timedelta(minutes=5) or generated_at < utc()-timedelta(days=7):
        raise HTTPException(422,'Supplier file date is outside permitted range')
    # Serialize publication for each supplier. Do not delete previous batches.
    source=await db.scalar(select(FeedSource).where(FeedSource.code==batch.supplier_code).with_for_update())
    if source is None:
        raise HTTPException(409,'Feed source missing')
    old=await db.get(FeedBatch,source.active_batch_id) if source.active_batch_id else None
    if old and old.id != batch.id and (old.created_at > batch.created_at or
        (old.generated_at is not None and aware_utc(old.generated_at) > generated_at)):
        raise HTTPException(409,'Newer imported feed is already active')
    batch.generated_at=generated_at
    batch.status='published'
    batch.published_at=utc()
    source.active_batch_id=batch.id
    source.updated_at=utc()
    await db.commit()
    return batch


async def validate_warehouse_map(db, supplier_code, warehouse_code, account_id, destination_id):
    source=await db.get(FeedSource,supplier_code)
    if not source:
        raise HTTPException(404,'Supplier feed does not exist')
    account=await db.get(SupplierAccount,account_id)
    destination=await db.get(SupplierDestination,destination_id)
    supplier=await db.get(Supplier,account.supplier_id) if account else None
    from app.modules.marketplace.models import FulfillmentNode
    node=await db.get(FulfillmentNode,destination.node_id) if destination else None
    if not node or not node.active or (account and node.merchant_id!=account.merchant_id):
        raise HTTPException(409,'Supplier receiving node belongs to a different merchant or is inactive')
    if not account or not destination or not supplier or supplier.code != supplier_code or destination.account_id!=account.id or not destination.verified or not destination.enabled or not account.enabled or not account.read_enabled:
        raise HTTPException(409,'Account/destination scope not verified or not enabled')
    if not warehouse_code or len(warehouse_code)>140:
        raise HTTPException(422,'Invalid supplier warehouse code')
    return True
