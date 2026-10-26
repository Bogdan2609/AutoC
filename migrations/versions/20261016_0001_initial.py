"""Catalog, suppliers, and pricing foundation.

Revision ID: 20261016_0001
Revises:
Create Date: 2026-10-16
"""
from alembic import op
import sqlalchemy as sa

revision = "20261016_0001"
down_revision = None
branch_labels = None
depends_on = None

UUID = sa.Uuid(as_uuid=True)


def upgrade() -> None:
    op.create_table("brands",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("name_norm", sa.String(160), nullable=False, unique=True),
        sa.Column("slug", sa.String(180), nullable=False, unique=True))
    op.create_table("suppliers",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("code", sa.String(40), unique=True, nullable=False),
        sa.Column("name", sa.String(160), nullable=False))
    op.create_table("price_policies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), unique=True, nullable=False),
        sa.Column("markup_percent", sa.Numeric(7, 2), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("markup_percent >= 0 AND markup_percent <= 1000", name="ck_price_policies_markup_bounds"))
    op.create_table("products",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("brand_id", UUID, sa.ForeignKey("brands.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("article", sa.String(110), nullable=False),
        sa.Column("article_norm", sa.String(110), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("slug", sa.String(200), unique=True, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("normalizer_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("brand_id", "article_norm", name="uq_products_brand_article_norm"),
        sa.CheckConstraint("status IN ('draft','review','published','archived')", name="ck_products_product_status"))
    op.create_index("ix_products_article_norm", "products", ["article_norm"])
    op.create_table("product_slug_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", UUID, sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("old_slug", sa.String(200), nullable=False, unique=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("supplier_items",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("supplier_id", UUID, sa.ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("product_id", UUID, sa.ForeignKey("products.id", ondelete="SET NULL")),
        sa.Column("external_key", sa.String(150), nullable=False),
        sa.Column("raw_brand", sa.String(160), nullable=False),
        sa.Column("raw_article", sa.String(110), nullable=False),
        sa.Column("match_status", sa.String(20), nullable=False),
        sa.UniqueConstraint("supplier_id", "external_key", name="uq_supplier_external_key"))
    op.create_table("supplier_offer_snapshots",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("supplier_item_id", UUID, sa.ForeignKey("supplier_items.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("purchase_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("tax_status", sa.String(20), nullable=False),
        sa.Column("quantity", sa.Integer()),
        sa.Column("lead_time_days", sa.Integer()),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("purchase_price >= 0", name="ck_supplier_offer_snapshots_non_negative_price"),
        sa.CheckConstraint("quantity IS NULL OR quantity >= 0", name="ck_supplier_offer_snapshots_non_negative_stock"))
    op.create_index("ix_supplier_offer_snapshots_item_observed", "supplier_offer_snapshots", ["supplier_item_id", "observed_at"])
    op.create_index("uq_active_price_policy", "price_policies", ["active"], unique=True, postgresql_where=sa.text("active = true"), sqlite_where=sa.text("active = 1"))


def downgrade() -> None:
    op.drop_index("uq_active_price_policy", table_name="price_policies")
    op.drop_index("ix_supplier_offer_snapshots_item_observed", table_name="supplier_offer_snapshots")
    op.drop_table("supplier_offer_snapshots")
    op.drop_table("supplier_items")
    op.drop_table("product_slug_history")
    op.drop_index("ix_products_article_norm", table_name="products")
    op.drop_table("products")
    op.drop_table("price_policies")
    op.drop_table("suppliers")
    op.drop_table("brands")
