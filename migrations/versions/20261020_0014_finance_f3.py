"""F3 shadow-only double-entry journals. Additive, no existing commercial data modified."""
from alembic import op
revision='20261020_0014'
down_revision='20261020_0013'
branch_labels=None
depends_on=None

def upgrade():
    op.execute("\nCREATE TABLE finance_ledger_books (\n\tid UUID NOT NULL, \n\tcode VARCHAR(80) NOT NULL, \n\tname VARCHAR(200) NOT NULL, \n\tlegal_entity_id UUID, \n\tcurrency VARCHAR(3) NOT NULL, \n\tstate VARCHAR(16) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_finance_ledger_books PRIMARY KEY (id), \n\tCONSTRAINT uq_f3_book_code UNIQUE (code), \n\tCONSTRAINT ck_finance_ledger_books_shadow_only CHECK (state = 'shadow'), \n\tCONSTRAINT ck_finance_ledger_books_rub_only CHECK (currency = 'RUB'), \n\tCONSTRAINT fk_finance_ledger_books_legal_entity_id_finance_legal_entities FOREIGN KEY(legal_entity_id) REFERENCES finance_legal_entities (id) ON DELETE RESTRICT\n)\n\n" )
    op.execute("\nCREATE TABLE finance_ledger_accounts (\n\tid UUID NOT NULL, \n\tbook_id UUID NOT NULL, \n\tcode VARCHAR(80) NOT NULL, \n\ttitle VARCHAR(200) NOT NULL, \n\tkind VARCHAR(16) NOT NULL, \n\tsettlement_role VARCHAR(20) NOT NULL, \n\tcounterparty_entity_id UUID, \n\tactive BOOLEAN NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_finance_ledger_accounts PRIMARY KEY (id), \n\tCONSTRAINT uq_f3_account_code UNIQUE (book_id, code), \n\tCONSTRAINT ck_finance_ledger_accounts_kind CHECK (kind IN ('ASSET','LIABILITY','EQUITY','INCOME','EXPENSE')), \n\tCONSTRAINT ck_finance_ledger_accounts_settlement_role CHECK (settlement_role IN ('NONE','SELLER_PAYABLE','SUPPLIER_PAYABLE','RECEIVABLE')), \n\tCONSTRAINT ck_finance_ledger_accounts_settlement_kind CHECK ((settlement_role='NONE' OR (settlement_role IN ('SELLER_PAYABLE','SUPPLIER_PAYABLE') AND kind='LIABILITY') OR (settlement_role='RECEIVABLE' AND kind='ASSET'))), \n\tCONSTRAINT fk_finance_ledger_accounts_book_id_finance_ledger_books FOREIGN KEY(book_id) REFERENCES finance_ledger_books (id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_f3_account_counterparty FOREIGN KEY(counterparty_entity_id) REFERENCES finance_legal_entities (id) ON DELETE RESTRICT\n)\n\n" )
    op.execute('CREATE INDEX ix_f3_account_book_kind ON finance_ledger_accounts (book_id, kind)' )
    op.execute("\nCREATE TABLE finance_ledger_entries (\n\tid UUID NOT NULL, \n\tbook_id UUID NOT NULL, \n\tidempotency_key VARCHAR(160) NOT NULL, \n\trequest_sha256 VARCHAR(64) NOT NULL, \n\tsource_ref VARCHAR(160) NOT NULL, \n\tevent_kind VARCHAR(60) NOT NULL, \n\tmemo VARCHAR(300) NOT NULL, \n\tsealed BOOLEAN NOT NULL, \n\toccurred_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\treverses_entry_id UUID, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_finance_ledger_entries PRIMARY KEY (id), \n\tCONSTRAINT uq_f3_entry_idempotency UNIQUE (book_id, idempotency_key), \n\tCONSTRAINT uq_f3_entry_source UNIQUE (book_id, source_ref, event_kind), \n\tCONSTRAINT uq_f3_entry_single_reverse UNIQUE (reverses_entry_id), \n\tCONSTRAINT ck_finance_ledger_entries_sim_ref_only CHECK (source_ref LIKE 'SIM:%%'), \n\tCONSTRAINT ck_finance_ledger_entries_hash_length CHECK (length(request_sha256) = 64), \n\tCONSTRAINT fk_finance_ledger_entries_book_id_finance_ledger_books FOREIGN KEY(book_id) REFERENCES finance_ledger_books (id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_f3_entry_reversal FOREIGN KEY(reverses_entry_id) REFERENCES finance_ledger_entries (id) ON DELETE RESTRICT\n)\n\n" )
    op.execute('CREATE INDEX ix_f3_entry_book_date ON finance_ledger_entries (book_id, occurred_at)' )
    op.execute("\nCREATE TABLE finance_ledger_lines (\n\tid UUID NOT NULL, \n\tentry_id UUID NOT NULL, \n\taccount_id UUID NOT NULL, \n\tside VARCHAR(6) NOT NULL, \n\tamount NUMERIC(20, 2) NOT NULL, \n\tCONSTRAINT pk_finance_ledger_lines PRIMARY KEY (id), \n\tCONSTRAINT ck_finance_ledger_lines_side CHECK (side IN ('DEBIT','CREDIT')), \n\tCONSTRAINT ck_finance_ledger_lines_positive CHECK (amount > 0), \n\tCONSTRAINT fk_finance_ledger_lines_entry_id_finance_ledger_entries FOREIGN KEY(entry_id) REFERENCES finance_ledger_entries (id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_finance_ledger_lines_account_id_finance_ledger_accounts FOREIGN KEY(account_id) REFERENCES finance_ledger_accounts (id) ON DELETE RESTRICT\n)\n\n" )
    op.execute('CREATE INDEX ix_f3_line_account ON finance_ledger_lines (account_id)' )
    op.execute(r"""
CREATE OR REPLACE FUNCTION finance_f3_validate_entry() RETURNS TRIGGER LANGUAGE plpgsql AS $f3$
DECLARE entry_uuid UUID; entry_book UUID; entry_sealed BOOLEAN;
        line_count BIGINT; debit_total NUMERIC; credit_total NUMERIC; wrong_book BIGINT;
BEGIN
    entry_uuid := CASE WHEN TG_TABLE_NAME = 'finance_ledger_entries' THEN (to_jsonb(NEW)->>'id')::uuid ELSE (to_jsonb(NEW)->>'entry_id')::uuid END;
    SELECT book_id,sealed INTO entry_book,entry_sealed FROM finance_ledger_entries WHERE id=entry_uuid;
    IF NOT FOUND THEN RAISE EXCEPTION 'F3 journal entry does not exist'; END IF;
    SELECT count(*), COALESCE(SUM(l.amount) FILTER (WHERE l.side='DEBIT'),0),
           COALESCE(SUM(l.amount) FILTER (WHERE l.side='CREDIT'),0),
           count(*) FILTER (WHERE a.book_id IS DISTINCT FROM entry_book)
    INTO line_count,debit_total,credit_total,wrong_book
    FROM finance_ledger_lines l LEFT JOIN finance_ledger_accounts a ON a.id=l.account_id
    WHERE l.entry_id=entry_uuid;
    IF NOT entry_sealed OR line_count<2 OR debit_total<=0 OR credit_total<=0 OR debit_total<>credit_total OR wrong_book<>0 THEN
       RAISE EXCEPTION 'F3 unsealed, unbalanced, or cross-book journal entry: %',entry_uuid;
    END IF;
    RETURN NULL;
END $f3$;
""")
    op.execute(r"""CREATE CONSTRAINT TRIGGER fin_f3_valid_entry AFTER INSERT OR UPDATE ON finance_ledger_entries
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION finance_f3_validate_entry();
""")
    op.execute(r"""CREATE CONSTRAINT TRIGGER fin_f3_valid_line AFTER INSERT ON finance_ledger_lines
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION finance_f3_validate_entry();
""")
    op.execute(r"""
CREATE OR REPLACE FUNCTION finance_f3_guard_entry() RETURNS TRIGGER LANGUAGE plpgsql AS $f3$
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.sealed = false AND NEW.sealed = true AND
       (to_jsonb(NEW)-'sealed')=(to_jsonb(OLD)-'sealed') THEN RETURN NEW; END IF;
    RAISE EXCEPTION 'F3 journal entries are immutable; use reversal';
END $f3$;
""")
    op.execute(r"""CREATE TRIGGER fin_f3_no_change_entry BEFORE UPDATE OR DELETE ON finance_ledger_entries
 FOR EACH ROW EXECUTE FUNCTION finance_f3_guard_entry();
""")
    op.execute(r"""CREATE OR REPLACE FUNCTION finance_f3_guard_line() RETURNS TRIGGER LANGUAGE plpgsql AS $f3$
DECLARE closed BOOLEAN;
BEGIN
    IF TG_OP = 'INSERT' THEN
       SELECT sealed INTO closed FROM finance_ledger_entries WHERE id=NEW.entry_id FOR UPDATE;
       IF closed IS FALSE THEN RETURN NEW; END IF;
    END IF;
    RAISE EXCEPTION 'F3 journal lines are immutable after posting';
END $f3$;
""")
    op.execute(r"""CREATE TRIGGER fin_f3_no_change_line BEFORE INSERT OR UPDATE OR DELETE ON finance_ledger_lines
 FOR EACH ROW EXECUTE FUNCTION finance_f3_guard_line();
""")

def downgrade():
    op.execute('DROP TRIGGER IF EXISTS fin_f3_no_change_line ON finance_ledger_lines')
    op.execute('DROP TRIGGER IF EXISTS fin_f3_no_change_entry ON finance_ledger_entries')
    op.execute('DROP TRIGGER IF EXISTS fin_f3_valid_line ON finance_ledger_lines')
    op.execute('DROP TRIGGER IF EXISTS fin_f3_valid_entry ON finance_ledger_entries')
    op.execute('DROP FUNCTION IF EXISTS finance_f3_guard_line()')
    op.execute('DROP FUNCTION IF EXISTS finance_f3_guard_entry()')
    op.execute('DROP FUNCTION IF EXISTS finance_f3_validate_entry()')
    op.drop_table('finance_ledger_lines')
    op.drop_table('finance_ledger_entries')
    op.drop_table('finance_ledger_accounts')
    op.drop_table('finance_ledger_books')
