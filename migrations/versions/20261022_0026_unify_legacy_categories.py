"""Promote legacy marketplace categories into the canonical catalog, losslessly.

Old tables remain unchanged for auditing. New legacy API writes already target
canonical tables. If slug collisions mean different concepts, STOP rather than
silently reclassifying published goods.
"""
from alembic import op
import sqlalchemy as sa
import uuid

revision = '20261022_0026'
down_revision = '20261022_0025'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    meta = sa.MetaData()
    old_c = sa.Table('categories', meta, autoload_with=conn)
    old_pc = sa.Table('product_categories', meta, autoload_with=conn)
    new_c = sa.Table('catalog_categories', meta, autoload_with=conn)
    new_pc = sa.Table('catalog_product_categories', meta, autoload_with=conn)
    legacy = {x.id: x for x in conn.execute(sa.select(old_c)).mappings()}
    canonical = {x.slug: x for x in conn.execute(sa.select(new_c)).mappings()}
    canonical_by_id = {x.id: x for x in canonical.values()}
    translated = {}
    pending = dict(legacy)
    while pending:
        advanced = False
        for cid, row in list(pending.items()):
            parent = row['parent_id']
            if parent is not None and parent not in translated:
                if parent not in legacy:
                    raise RuntimeError(f'Legacy category {cid} has missing parent {parent}')
                continue
            target_parent = translated.get(parent)
            old_target = canonical.get(row['slug'])
            if old_target is not None:
                if (old_target['name'].strip().casefold() != row['name'].strip().casefold() or
                    old_target['parent_id'] != target_parent):
                    raise RuntimeError(f'Category slug collision requiring manual review: {row["slug"]}')
                target_id = old_target['id']
            else:
                if cid in canonical_by_id:
                    raise RuntimeError(f'Category UUID collision requiring manual review: {cid}')
                conn.execute(sa.insert(new_c).values(id=cid, parent_id=target_parent,
                    name=row['name'], slug=row['slug'], position=0, active=True))
                target_id = cid
                new_row = dict(id=cid,parent_id=target_parent,name=row['name'],slug=row['slug'])
                canonical[row['slug']] = new_row
                canonical_by_id[cid] = new_row
            translated[cid] = target_id
            pending.pop(cid)
            advanced = True
        if not advanced:
            raise RuntimeError('Legacy category tree contains a cycle')
    existing = {(x.product_id, x.category_id) for x in conn.execute(
        sa.select(new_pc.c.product_id,new_pc.c.category_id))}
    for row in conn.execute(sa.select(old_pc)).mappings():
        pair=(row['product_id'],translated[row['category_id']])
        if pair not in existing:
            new_id = uuid.uuid4()
            if conn.dialect.name == "sqlite":
                new_id = new_id.hex  # reflection loses Uuid conversion on SQLite CHAR(32)
            conn.execute(sa.insert(new_pc).values(id=new_id,product_id=pair[0],category_id=pair[1],is_primary=False))
            existing.add(pair)


def downgrade():
    # Historical categories have been copied; automatic deletion would erase
    # operator changes created after migration. Retain both sets for audit.
    pass
