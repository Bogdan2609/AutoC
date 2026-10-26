"""Regression: PostgreSQL identifier names are limited to 63 bytes."""

from pathlib import Path

from app.core.db import Base
from app.modules.accounts import models as _accounts  # noqa: F401
from app.modules.catalog import models as _catalog  # noqa: F401
from app.modules.commerce import models as _commerce  # noqa: F401
from app.modules.marketplace import models as _marketplace  # noqa: F401
from app.modules.offer_engine import models as _offer_engine  # noqa: F401
from app.modules.payments import models as _payments  # noqa: F401
from app.modules.pricing import models as _pricing  # noqa: F401
from app.modules.suppliers import models as _suppliers  # noqa: F401


def test_postgresql_constraint_identifier_byte_limits():
    names = []
    for table in Base.metadata.tables.values():
        names.extend(constraint.name for constraint in table.constraints if constraint.name)
        names.extend(index.name for index in table.indexes if index.name)
    excessive = [name for name in names if len(name.encode("utf-8")) > 63]
    assert not excessive, f"PostgreSQL identifier > 63 bytes: {excessive}"


def test_offer_snapshot_account_fk_has_short_name_in_model_and_migration():
    from app.modules.suppliers.models import OfferSnapshot

    field = OfferSnapshot.__table__.c.supplier_account_id
    fks = list(field.foreign_keys)
    assert len(fks) == 1
    assert fks[0].constraint.name == "fk_offer_snapshots_supplier_account"
    migration = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "20261017_0004_marketplace_foundation.py"
    assert "fk_offer_snapshots_supplier_account" in migration.read_text(encoding="utf-8")
