"""Run Alembic data promotion on a scratch DB without touching any customer data."""
import importlib.util
import uuid
from pathlib import Path
from unittest.mock import patch
import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool
from app.core.db import Base
from app.modules.catalog.models import Brand, Product
from app.modules.catalog_ext.models import Category as CanonicalCategory, ProductCategory as CanonicalProductCategory
from app.modules.marketplace.models import Category as LegacyCategory, ProductCategory as LegacyProductCategory


def migration():
    path=Path(__file__).resolve().parents[1]/'migrations/versions/20261022_0026_unify_legacy_categories.py'
    spec=importlib.util.spec_from_file_location('legacy_category_migration_v0317',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_legacy_category_tree_is_promoted_without_mutating_legacy_rows():
    engine=sa.create_engine('sqlite+pysqlite:///:memory:',poolclass=StaticPool)
    Base.metadata.create_all(engine)
    root, child=uuid.uuid4(),uuid.uuid4()
    brand_id,product_id=uuid.uuid4(),uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(sa.insert(Brand.__table__).values(id=brand_id,name='MIGRATEBRAND',name_norm='MIGRATEBRAND',slug='migratebrand'))
        conn.execute(sa.insert(Product.__table__).values(id=product_id,brand_id=brand_id,name='Migrate',article='12345',article_norm='12345',status='draft',slug='migration-example',seo_noindex=True))
        conn.execute(sa.insert(LegacyCategory.__table__).values(id=root,name='Root Migrate',slug='root-migrate'))
        conn.execute(sa.insert(LegacyCategory.__table__).values(id=child,parent_id=root,name='Child Migrate',slug='child-migrate'))
        conn.execute(sa.insert(LegacyProductCategory.__table__).values(id=uuid.uuid4(),product_id=product_id,category_id=child))
        m=migration()
        with patch.object(m.op,'get_bind',return_value=conn):
            m.upgrade()
        actual=conn.execute(sa.select(CanonicalCategory.__table__)).mappings().all()
        assert {uuid.UUID(str(x['id'])) for x in actual}=={root,child}
        assert uuid.UUID(str(next(x for x in actual if uuid.UUID(str(x['id']))==child)['parent_id']))==root
        member=conn.execute(sa.select(CanonicalProductCategory.__table__)).mappings().one()
        assert tuple(uuid.UUID(str(member[k])) for k in ('product_id','category_id'))==(product_id,child)
        assert member['is_primary'] is False
        assert conn.execute(sa.select(sa.func.count()).select_from(LegacyProductCategory.__table__)).scalar()==1
    engine.dispose()


def test_migration_rejects_semantically_conflicting_slug():
    engine=sa.create_engine('sqlite+pysqlite:///:memory:',poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(sa.insert(LegacyCategory.__table__).values(id=uuid.uuid4(),name='Old Meaning',slug='same-slug'))
        conn.execute(sa.insert(CanonicalCategory.__table__).values(id=uuid.uuid4(),name='Different Meaning',slug='same-slug',active=True,position=0))
        m=migration()
        with patch.object(m.op,'get_bind',return_value=conn):
            with pytest.raises(RuntimeError,match='slug collision'):
                m.upgrade()
    engine.dispose()
