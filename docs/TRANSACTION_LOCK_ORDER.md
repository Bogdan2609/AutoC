# Transaction locking contract — AutoC v0.40

**Revision:** draft; code baseline `bc6266025c63d4c7ac9247ce9e1708c8d1620d90`. Applies to FastAPI async SQLAlchemy + PostgreSQL. **Not a replacement for PostgreSQL concurrency tests.**

## Ordering and acquisition

1. `User` lock for checkout idempotency (before lookup of Order by `(user_id,idempotency_key)`). This lock must never be held through an external money or supplier WRITE. The provider POST occurs only after a committed buyer order/payment intent.
2. `Order` lock before `Payment` lock for settlement, including competing provider attempts on the same buyer order; each payment has one `provider_id` and a unique succeeded-per-order index.
3. `OrderLine` / `SaleLineExecution`, then `Procurement` for fulfillment transitions. A per-order payment verification precedes a staff-supplied state change.
4. `SupplierPurchaseAttempt` / destination-scoped checks and a durable `dispatch_unknown` commit **before** supplier GetCheckout. Never redo GetCheckout after timeout; reconcile via read-only supplier status.
5. Own-stock `InventoryBalance` mutation must use atomic SQL predicates and movement idempotency keys. Never assume uncommitted stock holds survive a canceled order.
6. Refund state transitions lock provider payment evidence as required by refund reconciliation. Only one external refund POST is allowed across uncertain outcomes.

**Implementation note:** This describes existing isolated transaction families; **it is not a proven single global total order**. New code that acquires locks in multiple families must document and test its lock graph. Avoid holding row locks during remote READ when possible, and NEVER retry a whole transaction if it may have already crossed an external irreversible WRITE.

## Failure policies

- `40P01` (deadlock), `40001` (serialization) may be retried only for provably local, side-effect-free units of work; roll back first. Never rerun a supplier order or payment/refund external POST.
- `RedisLease` is an advisory coalescing facility. Financial correctness relies on immutable bank references, bank GET verification, database locks/uniqueness, and reconciliation; lease expiry cannot authorize another payment.
- Missing `provider_id` payments become durable manual-review alerts; do not issue a second create to discover the missing bank reference.
- Any `SupplierPurchaseAttempt` in `validating`, `dispatch_unknown` or `manual_review` requires inspection before intervention. An apparently stale `validating` may still have an active worker. Automated recovery is not safe without durable fencing/evidence.

## Must-pass PostgreSQL integration tests

- Two checkout requests (same customer/key and same/different payload) using separate DB transactions.
- Two concurrent webhooks on one payment and on two distinct provider IDs for one order.
- Client refresh racing a webhook, and refund reconciliation racing operator replacement.
- Worker crash just before and immediately after durable `dispatch_unknown` commit; no second GetCheckout.
- Deadlock regression for mixed fulfillment/refund/purchase administrative writers.

These tests need a **disposable local test database**. Repo `tests/conftest.py` drops and recreates schema when `TEST_DATABASE_URL` is set; never point it at a production or user database.
