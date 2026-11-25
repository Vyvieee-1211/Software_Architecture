# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Concert ticketing backend (FastAPI + SQLAlchemy 2.0 + PostgreSQL) with a static vanilla-JS frontend. It is a university Software Architecture course project (INT3105). The booking flow is hold-then-pay: an order is a 12-minute hold, settled by a payment-gateway webhook into issued QR tickets or returned stock. Holds are placed asynchronously: `POST /orders` enqueues an `order_requests` row (202 + `Location`), background workers turn it into a held order. `docs/phase2-architecture.drawio` sketches a Redis-based variant; the implemented queue lives in PostgreSQL.

Code comments, docstrings, README, and all user-facing UI text are in Vietnamese. Keep new comments and UI strings in Vietnamese.

## Commands

The shell is Windows PowerShell; the virtualenv is `.venv` (Python 3.14).

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt   # install
docker compose up db                                             # PostgreSQL 17 on :5432 (user/pass/db: concert)
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload      # run API on :8000 (Swagger at /docs)
.\.venv\Scripts\python.exe -m scripts.seed                       # seed 2 concerts, 6 ticket types, 12,200 ticket rows, demo@example.com / password123

.\.venv\Scripts\python.exe -m pytest tests -q                    # all tests (SQLite in-memory; PostgreSQL suite skipped)
.\.venv\Scripts\python.exe -m pytest tests/test_order_service.py::test_cancel_twice_invalid   # single test
$env:TEST_DATABASE_URL="postgresql+psycopg://concert:concert@localhost:5432/concert_test"; .\.venv\Scripts\python.exe -m pytest tests/test_postgres_concurrency.py   # real concurrency tests (drops and recreates that DB)

docker compose up --build                                        # db + api in containers
docker compose exec api python -m scripts.seed
```

- Deployment: `render.yaml` (Render Blueprint, free web + PostgreSQL); guide in `docs/deploy-render.md`. The Dockerfile honours `PORT`, runs `scripts.seed` first when `SEED_ON_START=true`, and uses `--proxy-headers` (IP rate limits depend on it). `config.py` falls back to `RENDER_EXTERNAL_URL` for `PUBLIC_BASE_URL` and rewrites `postgres://`/`postgresql://` URLs to `postgresql+psycopg://`.

- `DATABASE_URL` defaults to PostgreSQL; set `DATABASE_URL=sqlite:///./concert.db` to run without Docker. `init_db` only does `create_all`; after any change to `models.py` reset the database with `docker compose down -v; docker compose up -d db` and re-seed.
- Scripts must be run as modules (`python -m scripts.seed`) from the repo root so that `app` is importable. `seed` is a no-op if any concert already exists.
- There is no linter, formatter, or type-checker configured.
- Locust is not in `requirements.txt`; install it separately. `loadtest/locustfile.py` drives hold → pay via the mock gateway, so the API must run with `PAYMENT_PROVIDER=mock`.
- Config is read from environment variables (`app/config.py`). A `.env` at the repo root is loaded by `config._load_dotenv` without overriding variables already set, so terminal/compose values win; `.env` is git-ignored, `.env.example` documents the keys.
- Do not add `Co-Authored-By` or "Generated with Claude" lines to commits or PRs.

## Architecture

Strict three layers, with dependencies pointing downward only:

- `app/api/` — routers, Pydantic schemas, FastAPI dependencies. Knows HTTP.
- `app/services/` — business rules. Must not import FastAPI or SQLAlchemy. Services receive a Unit of Work factory and raise plain exceptions from `app/services/errors.py`.
- `app/repositories/` — SQLAlchemy models, engine/session, one repository class per aggregate, `SqlAlchemyUnitOfWork`, and `resilience.py` (retry + circuit breaker).
- `app/integrations/` — adapters to external systems: `MockPaymentGateway`, `VNPayGateway`, built by `build_payment_gateways()` from `PAYMENT_PROVIDER`, which may list several (`vnpay,mock`; first = default). The customer picks one (`POST /orders/{id}/pay` body `{"provider"}`, list at `GET /payments/providers`); `PaymentService` holds `{name: gateway}` and every later step (webhook, return, refund) uses the provider stored on the `transactions` row, never the default. Adapters may use `httpx` (constructor takes a `transport` for tests); services never do.

`app/api/deps.py` is the only module that knows both FastAPI and SQLAlchemy.

### Transaction boundary

Two patterns coexist:

- Order and payment services own their transactions through the Unit of Work: `with self.uow_factory() as uow: ... uow.commit()`. The UoW rolls back and closes on exit. `deps.py` wraps each use case in `database_policy.run`, which retries the whole operation with a fresh session on transient failures (pool timeout, SQLite lock, PostgreSQL serialization/deadlock/lock timeout, `StaleDataError` from the `tickets.version` optimistic lock). Operations must therefore be safe to re-run from scratch.
- Auth, concert lookup and concert management still use `get_db` (one session per request, commit after the endpoint, `scope="function"` so the commit happens before the response is sent).

`get_current_user` opens and closes its own short session before the use case starts, so a request never holds two connections.

### Order request queue (Asynchronous Request-Reply + Queue-Based Load Leveling + Competing Consumers)

- `OrderRequestService.submit` does only cheap checks (ticket type exists, sale window, unlocked `remaining >= quantity`), inserts into `order_requests` (`ON CONFLICT DO NOTHING RETURNING id` on `(user_id, idempotency_key)`), and returns `(request, created)`. Same key → the same request is replayed whatever its status; a different payload → `IdempotencyKeyReused`.
- `process_next` claims the oldest `pending` row with `FOR UPDATE SKIP LOCKED`, calls `OrderService.place_in(uow, ..., key="request:{id}")` and `complete()` in the same transaction. Business errors roll back and are recorded with `fail()` in a second transaction; unexpected errors go through `record_crash()` and fail the request after `ORDER_REQUEST_MAX_ATTEMPTS`. Both are conditional on `status = 'pending'`.
- `OrderRequestWorker` (`app/order_worker.py`) runs `ORDER_WORKERS` threads per process, woken by `order_worker.wakeup` on submit and polling otherwise. Tests set `ORDER_WORKERS=0` and call `deps.get_order_request_service().drain()`; `tests/test_api.py::place_order` wraps POST → drain → GET status into a final `Outcome` for business-flow tests.
- `OrderService.place_order` still exists (synchronous, used by service and PostgreSQL tests); `place_in` is the commit-free core both paths share.

### Order state machine and inventory

`orders.status`: `held → paid | expired | cancelled | failed`, and `paid → refunded` (customer cancels a paid order, full refund, only until `REFUND_CUTOFF_HOURS` before the concert). Every transition is a single conditional `UPDATE ... WHERE status = '<from>' [AND expires_at > now]` in `OrderRepository`; callers proceed (issue tickets, return stock) only when `rowcount == 1`. Keep it that way — read-then-check-then-write is exactly the bug this replaced. `DELETE /orders/{id}` dispatches on the order's status: `held` goes to `OrderService.cancel_order`, `paid` to `PaymentService.refund_order`.

Inserts that rely on `ON CONFLICT DO NOTHING` (`create_idempotent`, `TransactionRepository.record`) must detect "inserted vs skipped" via `.returning(id)`, not `rowcount`: psycopg reports -1 for an INSERT with RETURNING, which silently made every idempotent hold on PostgreSQL skip stock reservation.

Inventory invariant: `ticket_types.remaining + sum(quantity of held/paid orders) == total_quantity`, and the `tickets` rows mirror it (`available` count == `remaining`). Reserving is `UPDATE ... WHERE remaining >= quantity`; `release_hold()` / `release_sold()` in `order_service.py` are the only places stock and ticket rows are returned (the latter also clears `qr_code`), and they must only run right after a successful transition.

Per-user hold limit (`MAX_HELD_TICKETS_PER_USER`) is enforced under a `SELECT ... FOR UPDATE` on the user row, which also serialises Idempotency-Key retries from the same user.

### Payments

`PaymentService` never calls the gateway inside a DB transaction: one UoW to validate the order, gateway call, another UoW to record. `gateway.refund(..., payment_raw=...)` receives the stored `raw_log` of the `payment/succeeded` event because VNPay needs its own `vnp_TransactionNo`/`vnp_PayDate` to refund. Webhook delivery differs per provider and is handled in `routers/payments.py`: `POST /payments/webhook/{provider}` (bare `POST /payments/webhook` = default gateway; used by mock), VNPay IPN `GET /payments/webhook` with the query string passed as `body` and a `{"RspCode"}` JSON reply; `GET /payments/return/{provider}` verifies the signed result VNPay appends to the return URL (`gateway.parse_return`), settles it through the same `_apply` path as the IPN (so sandbox works on localhost without a public IPN URL; a later IPN is a duplicate), then redirects to `PAYMENT_RETURN_URL/{order_id}` (or the bare `PAYMENT_RETURN_URL` if it cannot be read). Webhooks are settled in `_apply`: the `(provider, payment_id, type, status)` unique key on `transactions` makes duplicates no-ops; money that cannot be applied (hold expired, wrong amount) records `refund/pending` and triggers `gateway.refund`. `HoldSweeper` (`app/sweeper.py`) calls `expire_holds()` every `HOLD_SWEEP_INTERVAL_SECONDS`; it drains all overdue holds in batches (`OrderRepository.expire_overdue`: one `UPDATE ... WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED) RETURNING`, stock released in `ticket_type_id` order so parallel sweepers cannot deadlock). `OrderService.get_order` also expires an overdue hold on read (lazy expiry via `_expire`). Tests set the interval to 0 and call `expire_holds()` directly.

QR codes are signed strings from `TicketCodes` (`T<id>.<nonce>.<hmac>`), issued only when tickets become `sold`.

### Sale-opening protection (Waiting room → Rate limit → Load shedding)

- **Waiting room** (`WaitingRoomService`, table `waiting_room_entries`): active for `WAITING_ROOM_MINUTES` after each concert's `sale_open_time`, joinable from `WAITING_ROOM_LEAD_MINUTES` before. FIFO by entry `id`; position k is admitted once `INITIAL_BATCH + ADMIT_PER_MINUTE × minutes since open > k`, so admission is a pure function of time (no background job). Admitted users get a signed JWT pass (`X-Waiting-Room-Pass`, `WAITING_ROOM_PASS_MINUTES` from first admission) that `OrderRequestService.submit` verifies without a DB read; an Idempotency-Key replay needs no pass. `POST/GET /concerts/{id}/waiting-room`.
- **Rate limit** (`app/api/rate_limit.py`): in-memory token bucket per process, applied as dependencies `limit_per_user(name)` / `limit_per_ip(name)` from `deps.rate_limiters`; 429 `RateLimited` + `Retry-After`. `RATE_LIMIT_*_PER_MINUTE=0` disables (tests do; Locust registers every user from one IP, so pure performance runs should too).
- **Load shedding** (`app/api/load_shedding.py`): outermost ASGI middleware, 503 `ServerBusy` + `Retry-After` once `MAX_IN_FLIGHT_REQUESTS` are in flight; `/health`, `/payments`, `/mock-gateway` are exempt.

### Error mapping

Services raise subclasses of `BusinessError`. The handler in `app/main.py` maps them via `ERROR_STATUS` to `{"error": <ClassName>, "detail": <message>}`. When adding an error: define it in `errors.py`, add it to `ERROR_STATUS` (default 400), and add a message in `frontend/api.js`. `DatabaseUnavailable` maps to 503 with `Retry-After`; `RateLimited` (429) and `ServerBusy` (503) come from the API layer, not services. Auth failures in `get_current_user` are `HTTPException`s (`{"detail": ...}` only).

### Time

All datetimes are naive UTC. `OrderService`/`PaymentService` take an injectable `clock`. The frontend appends `Z` to timestamps without an offset and renders in `Asia/Ho_Chi_Minh`.

### Tests

- `tests/conftest.py` sets `DATABASE_URL=sqlite://` and `HOLD_SWEEP_INTERVAL_SECONDS=0` before `app` is imported, and provides `add_ticket_type()` which also generates the per-seat `tickets` rows (plain `TicketType(...)` inserts leave `hold()` with nothing to assign).
- `tests/test_order_service.py`: `OrderService` against `FakeUnitOfWork` and a settable clock, no DB.
- `tests/test_api.py`, `test_idempotency.py`, `test_booking_flow.py`, `test_order_queue.py`: `TestClient` on in-memory SQLite (`StaticPool`, one connection). The fixture monkeypatches `SessionLocal` in `app.repositories.database` and `app.api.deps`; any new module importing `SessionLocal` by name needs the same patch. `FOR UPDATE` is a no-op on SQLite, so these cannot prove concurrency safety.
- `tests/test_postgres_concurrency.py`: multi-threaded tests on a real PostgreSQL given by `TEST_DATABASE_URL` (oversell burst, duplicate cancel, hold-limit bypass, pay-vs-expire race, duplicate webhooks, competing queue workers). Skipped otherwise.

### Frontend

`frontend/` is a hash-routed SPA in plain ES modules, served by `app/main.py` (static mount at `/`, after the API routers) or by `node frontend/dev-server.cjs` via `python -m scripts.dev`. Booking is one click with no confirm dialog (cinema-style): "Đặt vé & thanh toán" submits the order request, polls it (`api.waitForOrder`); with several gateways enabled it lands on `#/orders/{id}` where a provider picker is shown, otherwise it calls `POST /orders/{id}/pay` and redirects the same tab to `payment_url`; the mock checkout page (`?order_id=` is only used for its return link) redirects back to `#/orders/{id}` after paying. If `pay` fails after a successful hold, the user lands on `#/orders/{id}` to retry. The order page has a status panel (no step tracker), a sticky order summary, a per-second hold countdown from `expires_at`, a "Thanh toán" button (same-tab redirect), polling of `GET /orders/{id}` every 3 s while the order is `held`, e-ticket cards with QR loaded as blobs (the QR endpoint needs the bearer token and must return a standalone SVG with `xmlns`, otherwise `<img>` renders nothing), and two cancel flows (held → cancel hold; paid → refund, only while `REFUND_CUTOFF_HOURS` remain). `HOLD_MINUTES`/`REFUND_CUTOFF_HOURS` are duplicated as display constants at the top of `app.js`.

## Repository state to be aware of

- `kaggle_runs/` holds Phase 1 load-test evidence from the old single-step flow; the README's section 9.2 numbers predate the hold-then-pay redesign.
- `*.db` files are git-ignored. The Dockerfile uses Python 3.14 while the README states 3.12.
