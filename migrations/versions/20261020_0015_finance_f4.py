"""F4 isolated simulated SBP payment lifecycle, receipt plans, and reconciliation.

No changes to live orders, SBP payments, refunds, providers, procurement, F1/F2/F3.
"""
from alembic import op
revision='20261020_0015'
down_revision='20261020_0014'
branch_labels=None
depends_on=None

def upgrade():
    op.execute("\nCREATE TABLE finance_f4_sim_payments (\n\tid UUID NOT NULL, \n\tsim_order_ref VARCHAR(160) NOT NULL, \n\tidempotency_key VARCHAR(160) NOT NULL, \n\trequest_sha256 VARCHAR(64) NOT NULL, \n\tsale_model VARCHAR(8) NOT NULL, \n\tamount NUMERIC(20, 2) NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\tprovider VARCHAR(12) NOT NULL, \n\tstatus VARCHAR(12) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_finance_f4_sim_payments PRIMARY KEY (id), \n\tCONSTRAINT uq_f4_sim_order UNIQUE (sim_order_ref), \n\tCONSTRAINT uq_f4_payment_key UNIQUE (idempotency_key), \n\tCONSTRAINT ck_finance_f4_sim_payments_sim_order_only CHECK (sim_order_ref LIKE 'SIM:ORDER:%%'), \n\tCONSTRAINT ck_finance_f4_sim_payments_rub_only CHECK (currency = 'RUB'), \n\tCONSTRAINT ck_finance_f4_sim_payments_mock_only CHECK (provider = 'MOCK_SBP'), \n\tCONSTRAINT ck_finance_f4_sim_payments_sale_model CHECK (sale_model IN ('OWN','AGENCY')), \n\tCONSTRAINT ck_finance_f4_sim_payments_status CHECK (status IN ('CREATED','PENDING','SUCCEEDED','CANCELED')), \n\tCONSTRAINT ck_finance_f4_sim_payments_amount_positive CHECK (amount > 0)\n)\n\n")
    op.execute('\nCREATE TABLE finance_f4_sim_events (\n\tid UUID NOT NULL, \n\tpayment_id UUID NOT NULL, \n\tevent_key VARCHAR(160) NOT NULL, \n\tdigest VARCHAR(64) NOT NULL, \n\tnew_status VARCHAR(12) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_finance_f4_sim_events PRIMARY KEY (id), \n\tCONSTRAINT uq_f4_event_key UNIQUE (payment_id, event_key), \n\tCONSTRAINT fk_f4_sim_payment FOREIGN KEY(payment_id) REFERENCES finance_f4_sim_payments (id) ON DELETE RESTRICT\n)\n\n')
    op.execute("\nCREATE TABLE finance_f4_sim_refunds (\n\tid UUID NOT NULL, \n\tpayment_id UUID NOT NULL, \n\trequest_key VARCHAR(160) NOT NULL, \n\tamount NUMERIC(20, 2) NOT NULL, \n\tstatus VARCHAR(12) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_finance_f4_sim_refunds PRIMARY KEY (id), \n\tCONSTRAINT uq_f4_refund_key UNIQUE (request_key), \n\tCONSTRAINT ck_finance_f4_sim_refunds_amount_positive CHECK (amount > 0), \n\tCONSTRAINT ck_finance_f4_sim_refunds_status CHECK (status IN ('REQUESTED','SUCCEEDED','REJECTED')), \n\tCONSTRAINT fk_f4_refund_payment FOREIGN KEY(payment_id) REFERENCES finance_f4_sim_payments (id) ON DELETE RESTRICT\n)\n\n")
    op.execute("\nCREATE TABLE finance_f4_sim_receipt_plans (\n\tid UUID NOT NULL, \n\tpayment_id UUID NOT NULL, \n\trequest_key VARCHAR(160) NOT NULL, \n\tkind VARCHAR(12) NOT NULL, \n\tamount NUMERIC(20, 2) NOT NULL, \n\tstate VARCHAR(16) NOT NULL, \n\tvat_note VARCHAR(120) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_finance_f4_sim_receipt_plans PRIMARY KEY (id), \n\tCONSTRAINT uq_f4_receipt_key UNIQUE (request_key), \n\tCONSTRAINT ck_finance_f4_sim_receipt_plans_kind CHECK (kind IN ('PREPAYMENT','OFFSET','REFUND')), \n\tCONSTRAINT ck_finance_f4_sim_receipt_plans_preview_only CHECK (state = 'PREVIEW_ONLY'), \n\tCONSTRAINT ck_finance_f4_sim_receipt_plans_amount_positive CHECK (amount > 0), \n\tCONSTRAINT fk_f4_receipt_payment FOREIGN KEY(payment_id) REFERENCES finance_f4_sim_payments (id) ON DELETE RESTRICT\n)\n\n")
    op.execute("\nCREATE TABLE finance_f4_sim_bank_lines (\n\tid UUID NOT NULL, \n\texternal_ref VARCHAR(160) NOT NULL, \n\tdirection VARCHAR(3) NOT NULL, \n\tamount NUMERIC(20, 2) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_finance_f4_sim_bank_lines PRIMARY KEY (id), \n\tCONSTRAINT uq_f4_bank_external_ref UNIQUE (external_ref), \n\tCONSTRAINT ck_finance_f4_sim_bank_lines_sim_bank_only CHECK (external_ref LIKE 'SIM:BANK:%%'), \n\tCONSTRAINT ck_finance_f4_sim_bank_lines_direction CHECK (direction IN ('IN','OUT')), \n\tCONSTRAINT ck_finance_f4_sim_bank_lines_positive CHECK (amount > 0)\n)\n\n")
    op.execute('\nCREATE TABLE finance_f4_sim_bank_matches (\n\tid UUID NOT NULL, \n\tbank_line_id UUID NOT NULL, \n\tpayment_id UUID, \n\trefund_id UUID, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_finance_f4_sim_bank_matches PRIMARY KEY (id), \n\tCONSTRAINT uq_f4_match_one_bank_line UNIQUE (bank_line_id), \n\tCONSTRAINT uq_f4_match_one_payment UNIQUE (payment_id), \n\tCONSTRAINT uq_f4_match_one_refund UNIQUE (refund_id), \n\tCONSTRAINT ck_finance_f4_sim_bank_matches_exactly_one_source CHECK ((payment_id IS NOT NULL AND refund_id IS NULL) OR (payment_id IS NULL AND refund_id IS NOT NULL)), \n\tCONSTRAINT fk_f4_match_line FOREIGN KEY(bank_line_id) REFERENCES finance_f4_sim_bank_lines (id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_f4_match_payment FOREIGN KEY(payment_id) REFERENCES finance_f4_sim_payments (id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_f4_match_refund FOREIGN KEY(refund_id) REFERENCES finance_f4_sim_refunds (id) ON DELETE RESTRICT\n)\n\n')
    op.execute(r"""
CREATE OR REPLACE FUNCTION finance_f4_sim_immutable() RETURNS TRIGGER LANGUAGE plpgsql AS $f4$
BEGIN
    RAISE EXCEPTION 'F4 audit rows are immutable; create a compensating operation';
END $f4$;
""")
    op.execute("CREATE TRIGGER fin_f4_immutable_events BEFORE UPDATE OR DELETE ON finance_f4_sim_events FOR EACH ROW EXECUTE FUNCTION finance_f4_sim_immutable()")
    op.execute("CREATE TRIGGER fin_f4_immutable_receipt_plans BEFORE UPDATE OR DELETE ON finance_f4_sim_receipt_plans FOR EACH ROW EXECUTE FUNCTION finance_f4_sim_immutable()")
    op.execute("CREATE TRIGGER fin_f4_immutable_bank_lines BEFORE UPDATE OR DELETE ON finance_f4_sim_bank_lines FOR EACH ROW EXECUTE FUNCTION finance_f4_sim_immutable()")
    op.execute("CREATE TRIGGER fin_f4_immutable_bank_matches BEFORE UPDATE OR DELETE ON finance_f4_sim_bank_matches FOR EACH ROW EXECUTE FUNCTION finance_f4_sim_immutable()")
    op.execute(r"""
CREATE OR REPLACE FUNCTION finance_f4_guard_status() RETURNS TRIGGER LANGUAGE plpgsql AS $f4$
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'F4 simulated payment/refund deletion forbidden'; END IF;
    IF TG_TABLE_NAME='finance_f4_sim_payments' THEN
        IF (to_jsonb(NEW)-'status') <> (to_jsonb(OLD)-'status') THEN
            RAISE EXCEPTION 'F4 payment identity and amount are immutable';
        END IF;
        IF NOT ((OLD.status IN ('CREATED','PENDING') AND NEW.status IN ('PENDING','SUCCEEDED','CANCELED')) OR NEW.status=OLD.status) THEN
            RAISE EXCEPTION 'F4 payment status regression forbidden';
        END IF;
    ELSIF TG_TABLE_NAME='finance_f4_sim_refunds' THEN
        IF (to_jsonb(NEW)-'status') <> (to_jsonb(OLD)-'status') THEN
            RAISE EXCEPTION 'F4 refund identity and amount are immutable';
        END IF;
        IF NOT ((OLD.status='REQUESTED' AND NEW.status IN ('SUCCEEDED','REJECTED')) OR NEW.status=OLD.status) THEN
            RAISE EXCEPTION 'F4 refund status regression forbidden';
        END IF;
    END IF;
    RETURN NEW;
END $f4$;
""")
    op.execute("CREATE TRIGGER fin_f4_guard_payment BEFORE UPDATE OR DELETE ON finance_f4_sim_payments FOR EACH ROW EXECUTE FUNCTION finance_f4_guard_status()")
    op.execute("CREATE TRIGGER fin_f4_guard_refund BEFORE UPDATE OR DELETE ON finance_f4_sim_refunds FOR EACH ROW EXECUTE FUNCTION finance_f4_guard_status()")

def downgrade():
    op.execute('DROP TRIGGER IF EXISTS fin_f4_guard_payment ON finance_f4_sim_payments')
    op.execute('DROP TRIGGER IF EXISTS fin_f4_guard_refund ON finance_f4_sim_refunds')
    op.execute('DROP TRIGGER IF EXISTS fin_f4_immutable_events ON finance_f4_sim_events')
    op.execute('DROP TRIGGER IF EXISTS fin_f4_immutable_receipt_plans ON finance_f4_sim_receipt_plans')
    op.execute('DROP TRIGGER IF EXISTS fin_f4_immutable_bank_lines ON finance_f4_sim_bank_lines')
    op.execute('DROP TRIGGER IF EXISTS fin_f4_immutable_bank_matches ON finance_f4_sim_bank_matches')
    op.execute('DROP FUNCTION IF EXISTS finance_f4_guard_status()')
    op.execute('DROP FUNCTION IF EXISTS finance_f4_sim_immutable()')
    op.drop_table('finance_f4_sim_bank_matches')
    op.drop_table('finance_f4_sim_bank_lines')
    op.drop_table('finance_f4_sim_receipt_plans')
    op.drop_table('finance_f4_sim_refunds')
    op.drop_table('finance_f4_sim_events')
    op.drop_table('finance_f4_sim_payments')
