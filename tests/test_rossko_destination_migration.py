"""Safe nullable migration: never rewrites existing stored supplier receiving addresses."""
import importlib.util
from pathlib import Path
from sqlalchemy import create_engine, inspect, text
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_existing_destination_addresses_survive_nullable_requisite_migration():
    filename = Path(__file__).parents[1] / 'migrations/versions/20261022_0031_rossko_destination_requisite.py'
    spec = importlib.util.spec_from_file_location('rossko0031', filename)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as db:
        db.execute(text('CREATE TABLE supplier_destinations ('
                        'id TEXT PRIMARY KEY, account_id TEXT NOT NULL, '
                        'external_address_id TEXT, external_delivery_id TEXT)'))
        db.execute(text("INSERT INTO supplier_destinations VALUES ('D1','A1','ADDR-FROM-DB','000000002')"))
        mig.op = Operations(MigrationContext.configure(db))
        mig.upgrade()
        fields = {c['name']: c for c in inspect(db).get_columns('supplier_destinations')}
        assert fields['external_requisite_id']['nullable']
        row = db.execute(text('SELECT account_id, external_address_id, external_delivery_id, external_requisite_id FROM supplier_destinations')).one()
        assert tuple(row) == ('A1','ADDR-FROM-DB','000000002',None)
    engine.dispose()
