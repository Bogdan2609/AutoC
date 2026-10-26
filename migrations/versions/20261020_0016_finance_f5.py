"""F5 SHADOW tax register previews; no updates to live commerce or tax profile tables."""
from alembic import op
import sqlalchemy as sa
revision='20261020_0016'
down_revision='20261020_0015'
branch_labels=None
depends_on=None


def uid():return sa.Uuid(as_uuid=True)

def upgrade():
    op.create_table('finance_f5_sim_books',
      sa.Column('id',uid(),primary_key=True),
      sa.Column('book_ref',sa.String(160),nullable=False),
      sa.Column('actor_role',sa.String(10),nullable=False),
      sa.Column('tax_profile_status',sa.String(16),nullable=False),
      sa.Column('period_start',sa.Date(),nullable=False),
      sa.Column('period_end',sa.Date(),nullable=False),
      sa.Column('currency',sa.String(3),nullable=False),
      sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
      sa.UniqueConstraint('book_ref',name='uq_f5_book_ref'),
      sa.CheckConstraint("book_ref LIKE 'SIM:F5:BOOK:%'",name='ck_finance_f5_sim_books_sim_only'),
      sa.CheckConstraint("actor_role IN ('PLATFORM','SELLER')",name='ck_finance_f5_sim_books_actor_role'),
      sa.CheckConstraint("tax_profile_status='UNRESOLVED'",name='ck_finance_f5_sim_books_unresolved'),
      sa.CheckConstraint('period_start<=period_end',name='ck_finance_f5_sim_books_period'),
      sa.CheckConstraint("currency='RUB'",name='ck_finance_f5_sim_books_currency'),
    )
    op.create_table('finance_f5_sim_documents',
      sa.Column('id',uid(),primary_key=True),
      sa.Column('document_ref',sa.String(160),nullable=False),
      sa.Column('document_date',sa.Date(),nullable=False),
      sa.Column('kind',sa.String(14),nullable=False),
      sa.Column('memo',sa.String(300),nullable=False),
      sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
      sa.UniqueConstraint('document_ref',name='uq_f5_document_ref'),
      sa.CheckConstraint("document_ref LIKE 'SIM:F5:DOC:%'",name='ck_finance_f5_sim_documents_sim_only'),
      sa.CheckConstraint("kind IN ('SALE','PURCHASE','AGENT_REPORT','ADVANCE','REFUND','EXPENSE')",name='ck_finance_f5_sim_documents_kind'),
    )
    op.create_table('finance_f5_sim_rows',
      sa.Column('id',uid(),primary_key=True),
      sa.Column('book_id',uid(),sa.ForeignKey('finance_f5_sim_books.id',name='fk_f5_row_book',ondelete='RESTRICT'),nullable=False),
      sa.Column('document_id',uid(),sa.ForeignKey('finance_f5_sim_documents.id',name='fk_f5_row_document',ondelete='RESTRICT'),nullable=False),
      sa.Column('request_key',sa.String(160),nullable=False),
      sa.Column('event_kind',sa.String(24),nullable=False),
      sa.Column('operation_date',sa.Date(),nullable=False),
      sa.Column('gross_amount',sa.Numeric(20,2),nullable=False),
      sa.Column('vat_scheme',sa.String(16),nullable=False),
      sa.Column('tax_class',sa.String(20),nullable=False),
      sa.Column('vat_amount',sa.Numeric(20,2),nullable=True),
      sa.Column('net_amount',sa.Numeric(20,2),nullable=True),
      sa.Column('correction_of_id',uid(),sa.ForeignKey('finance_f5_sim_rows.id',name='fk_f5_row_corrects',ondelete='RESTRICT'),nullable=True),
      sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
      sa.UniqueConstraint('request_key',name='uq_f5_row_key'),
      sa.UniqueConstraint('book_id','document_id','event_kind',name='uq_f5_book_doc_event'),
      sa.CheckConstraint("request_key LIKE 'SIM:F5:ROW:%'",name='ck_finance_f5_sim_rows_sim_only'),
      sa.CheckConstraint("event_kind IN ('OWN_SALE','AGENCY_SELLER_SALE','AGENCY_PLATFORM_FEE','PURCHASE','ADVANCE','REFUND','EXPENSE')",name='ck_finance_f5_sim_rows_event_kind'),
      sa.CheckConstraint("vat_scheme IN ('UNRESOLVED','STANDARD','USN_5','USN_7','EXEMPT')",name='ck_finance_f5_sim_rows_vat_scheme'),
      sa.CheckConstraint("tax_class IN ('STANDARD_GOODS','REDUCED_10','ZERO','EXEMPT')",name='ck_finance_f5_sim_rows_tax_class'),
      sa.CheckConstraint('gross_amount>0',name='ck_finance_f5_sim_rows_positive'),
    )
    op.execute(r"""
CREATE OR REPLACE FUNCTION finance_f5_sim_immutable() RETURNS TRIGGER LANGUAGE plpgsql AS $f5$
BEGIN
    RAISE EXCEPTION 'F5 shadow tax register records are immutable; write separate adjustment document';
END $f5$;
""")
    for table,trigger in [('finance_f5_sim_books','fin_f5_immutable_books'),('finance_f5_sim_documents','fin_f5_immutable_docs'),('finance_f5_sim_rows','fin_f5_immutable_rows')]:
        op.execute(f'CREATE TRIGGER {trigger} BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION finance_f5_sim_immutable()')


def downgrade():
    for table,trigger in [('finance_f5_sim_books','fin_f5_immutable_books'),('finance_f5_sim_documents','fin_f5_immutable_docs'),('finance_f5_sim_rows','fin_f5_immutable_rows')]:
        op.execute(f'DROP TRIGGER IF EXISTS {trigger} ON {table}')
    op.execute('DROP FUNCTION IF EXISTS finance_f5_sim_immutable()')
    op.drop_table('finance_f5_sim_rows')
    op.drop_table('finance_f5_sim_documents')
    op.drop_table('finance_f5_sim_books')
