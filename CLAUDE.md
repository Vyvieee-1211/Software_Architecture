# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Concert ticketing backend (FastAPI + SQLAlchemy 2.0 + PostgreSQL) with a static vanilla-JS frontend. It is a university Software Architecture course project (INT3105). The booking flow is hold-then-pay: an order is a 12-minute hold, settled by a payment-gateway webhook into issued QR tickets or returned stock. `docs/phase2-architecture.drawio` sketches further Phase 2 ideas (Redis queue, order worker) that are not implemented.

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

- `DATABASE_URL` defaults to PostgreSQL; set `DATABASE_URL=sqlite:///./concert.db` to run without Docker. `init_db` only does `create_all`; a database created from an older schema must be dropped and recreated.
- Scripts must be run as modules (`python -m scripts.seed`) from the repo root so that `app` is importable. `seed` is a no-op if any concert already exists.
- There is no linter, formatter, or type-checker configured.
- Locust is not in `requirements.txt`; install it separately. `loadtest/locustfile.py` drives hold → pay via the mock gateway, so the API must run with `PAYMENT_PROVIDER=mock`.
- Config is read from environment variables only (`app/config.py`); `.env.example` is documentation, nothing loads a `.env` file.
- Do not add `Co-Authored-By` or "Generated with Claude" lines to commits or PRs.

## Architecture

Strict three layers, with dependencies pointing downward only:

- `app/api/` — routers, Pydantic schemas, FastAPI dependencies. Knows HTTP.
- `app/services/` — business rules. Must not import FastAPI or SQLAlchemy. Services receive a Unit of Work factory and raise plain exceptions from `app/services/errors.py`.
- `app/repositories/` — SQLAlchemy models, engine/session, one repository class per aggregate, `SqlAlchemyUnitOfWork`, and `resilience.py` (retry + circuit breaker).
- `app/integrations/` — adapters to external systems; currently only `MockPaymentGateway`, selected by `build_payment_gateway()` from `PAYMENT_PROVIDER`.

`app/api/deps.py` is the only module that knows both FastAPI and SQLAlchemy.

### Transaction boundary

Two patterns coexist:

- Order and payment services own their transactions through the Unit of Work: `with self.uow_factory() as uow: ... uow.commit()`. The UoW rolls back and closes on exit. `deps.py` wraps each use case in `database_policy.run`, which retries the whole operation with a fresh session on transient failures (pool timeout, SQLite lock, PostgreSQL serialization/deadlock/lock timeout, `StaleDataError` from the `tickets.version` optimistic lock). Operations must therefore be safe to re-run from scratch.
- Auth, concert lookup and concert management still use `get_db` (one session per request, commit after the endpoint, `scope="function"` so the commit happens before the response is sent).

`get_current_user` opens and closes its own short session before the use case starts, so a request never holds two connections.

### Order state machine and inventory

`orders.status`: `held → paid | expired | cancelled | failed`. Every transition is a single conditional `UPDATE ... WHERE status = 'held' [AND expires_at > now]` in `OrderRepository`; callers proceed (issue tickets, return stock) only when `rowcount == 1`. Keep it that way — read-then-check-then-write is exactly the bug this replaced.

Inventory invariant: `ticket_types.remaining + sum(quantity of held/paid orders) == total_quantity`, and the `tickets` rows mirror it (`available` count == `remaining`). Reserving is `UPDATE ... WHERE remaining >= quantity`; `release_hold()` in `order_service.py` is the only place stock and ticket rows are returned, and it must only run right after a successful transition.

Per-user hold limit (`MAX_HELD_TICKETS_PER_USER`) is enforced under a `SELECT ... FOR UPDATE` on the user row, which also serialises Idempotency-Key retries from the same user.

### Payments

`PaymentService` never calls the gateway inside a DB transaction: one UoW to validate the order, gateway call, another UoW to record. Webhooks are settled in `_apply`: the `(provider, payment_id, type, status)` unique key on `transactions` makes duplicates no-ops; money that cannot be applied (hold expired, wrong amount) records `refund/pending` and triggers `gateway.refund`. `HoldSweeper` (`app/sweeper.py`) expires overdue holds every `HOLD_SWEEP_INTERVAL_SECONDS`; tests set it to 0 and call `expire_holds()` directly.

QR codes are signed strings from `TicketCodes` (`T<id>.<nonce>.<hmac>`), issued only when tickets become `sold`.

### Error mapping

Services raise subclasses of `BusinessError`. The handler in `app/main.py` maps them via `ERROR_STATUS` to `{"error": <ClassName>, "detail": <message>}`. When adding an error: define it in `errors.py`, add it to `ERROR_STATUS` (default 400), and add a message in `frontend/api.js`. `DatabaseUnavailable` maps to 503 with `Retry-After`. Auth failures in `get_current_user` are `HTTPException`s (`{"detail": ...}` only).

### Time

All datetimes are naive UTC. `OrderService`/`PaymentService` take an injectable `clock`. The frontend appends `Z` to timestamps without an offset and renders in `Asia/Ho_Chi_Minh`.

### Tests

- `tests/conftest.py` sets `DATABASE_URL=sqlite://` and `HOLD_SWEEP_INTERVAL_SECONDS=0` before `app` is imported, and provides `add_ticket_type()` which also generates the per-seat `tickets` rows (plain `TicketType(...)` inserts leave `hold()` with nothing to assign).
- `tests/test_order_service.py`: `OrderService` against `FakeUnitOfWork` and a settable clock, no DB.
- `tests/test_api.py`, `test_idempotency.py`, `test_booking_flow.py`: `TestClient` on in-memory SQLite (`StaticPool`, one connection). The fixture monkeypatches `SessionLocal` in `app.repositories.database` and `app.api.deps`; any new module importing `SessionLocal` by name needs the same patch. `FOR UPDATE` is a no-op on SQLite, so these cannot prove concurrency safety.
- `tests/test_postgres_concurrency.py`: multi-threaded tests on a real PostgreSQL given by `TEST_DATABASE_URL` (oversell burst, duplicate cancel, hold-limit bypass, pay-vs-expire race, duplicate webhooks). Skipped otherwise.

### Frontend

`frontend/` is a hash-routed SPA in plain ES modules, served by `app/main.py` (static mount at `/`, after the API routers) or by `node frontend/dev-server.cjs` via `python -m scripts.dev`. It still models the pre-hold flow (`confirmed` status, no payment step) and needs updating to `held`/`paid`, `POST /orders/{id}/pay` and the tickets page.

## Repository state to be aware of

- `kaggle_runs/` holds Phase 1 load-test evidence from the old single-step flow; the README's section 9.2 numbers predate the hold-then-pay redesign.
- `*.db` files are git-ignored. The Dockerfile uses Python 3.14 while the README states 3.12.
