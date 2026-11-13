"""HTTP contention probes on isolated fixtures in the benchmark's fresh SQLite DB.

The load generator signs tokens for existing fixture users using the benchmark's
JWT_SECRET. These probes assess transactions and authorization, not login speed.
No production database, application code or original load fixture is changed.
"""
import argparse
import asyncio
import json
import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import jwt


SCHEMA_VERSION = 1


def _open(db_path, readonly=True):
    if readonly:
        uri = db_path.resolve().as_uri() + "?mode=ro"
        return sqlite3.connect(uri, uri=True, timeout=1)
    return sqlite3.connect(db_path, timeout=10)


def _snapshot(db_path, ticket_type_id):
    # One SELECT sees inventory and committed orders in the same DB snapshot.
    with closing(_open(db_path)) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("""
            SELECT t.id AS ticket_type_id, t.total_quantity, t.remaining,
                COALESCE(SUM(CASE WHEN o.status='confirmed' THEN o.quantity ELSE 0 END),0) AS confirmed_quantity,
                COALESCE(SUM(CASE WHEN o.status='cancelled' THEN o.quantity ELSE 0 END),0) AS cancelled_quantity,
                COUNT(o.id) AS order_count,
                COALESCE(SUM(CASE WHEN o.status='confirmed' THEN 1 ELSE 0 END),0) AS confirmed_orders,
                COALESCE(SUM(CASE WHEN o.status='cancelled' THEN 1 ELSE 0 END),0) AS cancelled_orders,
                COALESCE(SUM(CASE WHEN o.status NOT IN ('confirmed','cancelled') OR o.quantity <= 0 THEN 1 ELSE 0 END),0) AS invalid_orders
            FROM ticket_types t LEFT JOIN orders o ON o.ticket_type_id=t.id
            WHERE t.id=? GROUP BY t.id
        """, (ticket_type_id,)).fetchone()
    if row is None:
        raise RuntimeError(f"Probe ticket type {ticket_type_id} is missing")
    result = dict(row)
    result["invariant_ok"] = (
        0 <= result["remaining"] <= result["total_quantity"]
        and result["remaining"] + result["confirmed_quantity"] == result["total_quantity"]
        and result["invalid_orders"] == 0
    )
    return result


def _fixture(db_path, name, capacity):
    # Only the isolated benchmark database is passed here. Run before a batch.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with closing(_open(db_path, readonly=False)) as db:
        with db:
            concert_id = db.execute("""
                INSERT INTO concerts (name, artist, venue, start_time, sale_open_time)
                VALUES (?,?,?,?,?)
            """, (f"NFR probe: {name}", "Concurrency test", "Isolated benchmark fixture",
                  (now + timedelta(days=30)).isoformat(), (now - timedelta(days=1)).isoformat())).lastrowid
            ticket_type_id = db.execute("""
                INSERT INTO ticket_types (concert_id, name, price, total_quantity, remaining)
                VALUES (?,?,?,?,?)
            """, (concert_id, "Probe", 1000, capacity, capacity)).lastrowid
    return {"concert_id": concert_id, "ticket_type_id": ticket_type_id, "total_quantity": capacity}


def _accounts(db_path, manifest, concurrency):
    emails = [a["email"] for a in manifest.get("probe_accounts", manifest.get("accounts", []))][:concurrency]
    if len(emails) < concurrency:
        raise RuntimeError(f"Need {concurrency} fixture accounts; manifest has {len(emails)}")
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        raise RuntimeError("JWT_SECRET missing; cannot sign benchmark fixture tokens")
    with closing(_open(db_path)) as db:
        rows = db.execute("SELECT id,email FROM users WHERE email IN (" + ",".join("?" for _ in emails) + ")", emails).fetchall()
    ids = {email: user_id for user_id, email in rows}
    if set(emails) != set(ids):
        raise RuntimeError("Some manifest accounts are absent from the benchmark DB")
    now = datetime.now(timezone.utc)
    return [{"user_id": ids[email], "token": jwt.encode(
        {"sub": str(ids[email]), "iat": now, "exp": now + timedelta(hours=1)}, secret, algorithm="HS256")}
        for email in emails]


def _buy(account, ticket_type_id):
    return {"method": "POST", "path": "/orders", "account": account,
            "json": {"ticket_type_id": ticket_type_id, "quantity": 1}, "operation": "buy"}


def _cancel(account, order_id):
    return {"method": "DELETE", "path": f"/orders/{order_id}", "account": account,
            "order_id": order_id, "operation": "cancel"}


async def _request(client, specification, origin, phase="contention"):
    started = time.perf_counter()
    account = specification["account"]
    result = {"method": specification["method"], "path": specification["path"],
              "user_id": account["user_id"], "operation": specification["operation"],
              "phase": phase, "start_offset_ms": round((started-origin)*1000, 4)}
    if "json" in specification:
        result["request_body"] = specification["json"]
    if "order_id" in specification:
        result["order_id"] = specification["order_id"]
    try:
        response = await client.request(specification["method"], specification["path"],
                                        json=specification.get("json"),
                                        headers={"Authorization": f"Bearer {account['token']}"})
        result["status"] = response.status_code
        try:
            result["body"] = response.json()
        except ValueError:
            result["body"] = None
            result["response_excerpt"] = response.text[:500]
    except Exception as exc:
        result.update(status=0, body=None, error=f"{type(exc).__name__}: {exc}")
    ended = time.perf_counter()
    result.update(elapsed_ms=round((ended-started)*1000, 4), end_offset_ms=round((ended-origin)*1000, 4))
    return result


def _sale_ok(result, ticket_type_id):
    body = result.get("body")
    return (result.get("status") == 201 and isinstance(body, dict)
            and isinstance(body.get("id"), int) and not isinstance(body.get("id"), bool)
            and body.get("user_id") == result["user_id"] and body.get("ticket_type_id") == ticket_type_id
            and body.get("quantity") == 1 and body.get("status") == "confirmed")


def _sold_out(result):
    return result.get("status") == 409 and isinstance(result.get("body"), dict) and result["body"].get("error") == "SoldOut"


def _cancel_ok(result, ticket_type_id):
    body = result.get("body")
    return (result.get("status") == 200 and isinstance(body, dict) and body.get("id") == result.get("order_id")
            and body.get("user_id") == result["user_id"] and body.get("ticket_type_id") == ticket_type_id
            and body.get("quantity") == 1 and body.get("status") == "cancelled")


def _already_cancelled(result):
    return result.get("status") == 409 and isinstance(result.get("body"), dict) and result["body"].get("error") == "InvalidState"


async def _batch(client, db_path, ticket_type_id, specifications):
    origin = time.perf_counter()
    release = asyncio.Event()
    done = asyncio.Event()
    samples = []
    sample_errors = []

    async def worker(specification):
        await release.wait()
        return await _request(client, specification, origin)

    async def sample():
        while not done.is_set():
            try:
                value = _snapshot(db_path, ticket_type_id)
                value["elapsed_ms"] = round((time.perf_counter()-origin)*1000, 4)
                samples.append(value)
            except Exception as exc:
                sample_errors.append(f"{type(exc).__name__}: {exc}")
            try:
                await asyncio.wait_for(done.wait(), timeout=0.1)
            except asyncio.TimeoutError:
                pass

    tasks = [asyncio.create_task(worker(s)) for s in specifications]
    sampler = asyncio.create_task(sample())
    await asyncio.sleep(0)
    release.set()
    try:
        results = await asyncio.gather(*tasks)
    finally:
        done.set()
        await sampler
    events = [(r["start_offset_ms"], 1) for r in results] + [(r["end_offset_ms"], -1) for r in results]
    active = peak = 0
    for _, change in sorted(events):
        active += change
        peak = max(peak, active)
    spread = max(r["start_offset_ms"] for r in results) - min(r["start_offset_ms"] for r in results) if results else None
    return {"requests": results, "inventory_samples": samples, "sample_errors": sample_errors,
            "peak_inflight": peak, "start_spread_ms": round(spread, 4) if spread is not None else None,
            "elapsed_ms": round((time.perf_counter()-origin)*1000, 4),
            "overlap_note": "Client requests overlap; this is not proof that all server transactions execute in parallel."}


async def _settle(db_path, ticket_type_id, minimum_created, minimum_cancelled=0):
    deadline = time.perf_counter() + 6
    previous = None
    stable = 0
    last = None
    while time.perf_counter() < deadline:
        last = _snapshot(db_path, ticket_type_id)
        same = last == previous
        stable = stable+1 if same else 1
        previous = last
        # Successful responses must have visible committed records. FastAPI may
        # complete a yield dependency immediately after sending the response.
        if stable >= 3 and last["order_count"] >= minimum_created and last["cancelled_orders"] >= minimum_cancelled:
            return last, True
        await asyncio.sleep(0.2)
    return last, False


def _finish_case(case):
    tid = case["fixture"]["ticket_type_id"]
    reasons = case.setdefault("reasons", [])
    if case.get("sample_errors"):
        reasons.append("Read-only inventory sampling raised an infrastructure error")
    bad_samples = [s for s in case.get("inventory_samples", []) if not s["invariant_ok"]]
    if bad_samples:
        reasons.append(f"Inventory invariant violated in {len(bad_samples)} committed snapshots")
    if not case.get("inventory_after", {}).get("invariant_ok"):
        reasons.append("Final inventory violates remaining bounds or remaining+confirmed=total")
    if not case.get("settled", False):
        reasons.append("Successful HTTP writes were not confirmed as committed within the settling allowance")
    if case.get("peak_inflight", 0) < 2:
        reasons.append("No overlapping requests were observed; concurrency was not exercised")
    if case.get("start_spread_ms") is None or case["start_spread_ms"] > 1000:
        reasons.append("Requests were not released in a sufficiently tight burst (spread >1000 ms)")
    case["successful_sales"] = sum(_sale_ok(r, tid) for r in case.get("setup_requests", []) + case.get("requests", []))
    case["successful_cancellations"] = sum(_cancel_ok(r, tid) for r in case.get("requests", []))
    case["status"] = "FAIL" if reasons else "PASS"
    return case


async def _burst(client, db_path, accounts):
    capacity = min(20, max(1, len(accounts)//5))
    fixture = _fixture(db_path, "oversell burst", capacity)
    tid = fixture["ticket_type_id"]
    case = {"name": "oversell_burst", "fixture": fixture, "reasons": [], "inventory_before": _snapshot(db_path, tid)}
    case.update(await _batch(client, db_path, tid, [_buy(a, tid) for a in accounts]))
    successful = sum(_sale_ok(r, tid) for r in case["requests"])
    sold_out = sum(_sold_out(r) for r in case["requests"])
    case["inventory_after"], case["settled"] = await _settle(db_path, tid, successful)
    if any(not (_sale_ok(r, tid) or _sold_out(r)) for r in case["requests"]):
        case["reasons"].append("Purchase burst had unexpected HTTP/transport errors or invalid bodies")
    if successful != capacity:
        case["reasons"].append(f"Expected {capacity} successful one-ticket sales; observed {successful}")
    if sold_out != len(accounts)-capacity:
        case["reasons"].append("Unsuccessful excess buyers were not all rejected with 409 SoldOut")
    after = case["inventory_after"]
    if after["remaining"] != 0 or after["confirmed_quantity"] != capacity or after["order_count"] != capacity:
        case["reasons"].append("Committed sales did not exactly exhaust the isolated finite stock")
    return _finish_case(case)


async def _setup_orders(client, db_path, accounts, fixture):
    tid = fixture["ticket_type_id"]
    rows = []
    orders = []
    origin = time.perf_counter()
    for account in accounts:
        response = await _request(client, _buy(account, tid), origin, phase="setup")
        rows.append(response)
        if _sale_ok(response, tid):
            orders.append((account, response["body"]["id"]))
    _, settled = await _settle(db_path, tid, len(orders))
    return rows, orders, settled


async def _unique_cancellations(client, db_path, accounts):
    selected = accounts[:min(20, len(accounts))]
    fixture = _fixture(db_path, "distinct order cancellation", len(selected))
    tid = fixture["ticket_type_id"]
    setup, orders, setup_settled = await _setup_orders(client, db_path, selected, fixture)
    case = {"name": "distinct_order_cancellations", "fixture": fixture, "setup_requests": setup,
            "inventory_before": _snapshot(db_path, tid), "reasons": []}
    if not setup_settled or len(orders) != len(selected):
        case["reasons"].append("Could not create and commit all distinct cancellation fixtures")
    if len(orders) < 2:
        case.update(requests=[], inventory_samples=[], sample_errors=[], peak_inflight=0, start_spread_ms=None)
    else:
        case.update(await _batch(client, db_path, tid, [_cancel(a, oid) for a, oid in orders]))
    cancelled = sum(_cancel_ok(r, tid) for r in case["requests"])
    case["inventory_after"], case["settled"] = await _settle(db_path, tid, len(orders), cancelled)
    if cancelled != len(orders):
        case["reasons"].append("A distinct-order cancellation returned an error or invalid response")
    after = case["inventory_after"]
    if after["remaining"] != fixture["total_quantity"] or after["confirmed_orders"] != 0 or after["cancelled_orders"] != len(orders):
        case["reasons"].append("Distinct-order cancellations did not restore inventory exactly once per order")
    return _finish_case(case)


async def _duplicate_cancellation(client, db_path, accounts, trial):
    fixture = _fixture(db_path, f"duplicate cancellation {trial}", 3)
    tid = fixture["ticket_type_id"]
    setup, orders, setup_settled = await _setup_orders(client, db_path, [accounts[(trial-1)%len(accounts)]], fixture)
    case = {"name": f"duplicate_cancellation_trial_{trial}", "fixture": fixture,
            "setup_requests": setup, "inventory_before": _snapshot(db_path, tid), "reasons": []}
    count = min(10, len(accounts))
    if not setup_settled or len(orders) != 1:
        case["reasons"].append("Could not create and commit the duplicate-cancellation fixture")
        case.update(requests=[], inventory_samples=[], sample_errors=[], peak_inflight=0, start_spread_ms=None)
    else:
        account, order_id = orders[0]
        case.update(await _batch(client, db_path, tid, [_cancel(account, order_id) for _ in range(count)]))
    successes = sum(_cancel_ok(r, tid) for r in case["requests"])
    conflicts = sum(_already_cancelled(r) for r in case["requests"])
    case["inventory_after"], case["settled"] = await _settle(db_path, tid, len(orders), int(successes > 0))
    if successes != 1 or conflicts != count-1:
        case["reasons"].append(f"Same-order cancellation expected one 200 and {count-1} InvalidState conflicts; got {successes} successes and {conflicts} conflicts")
    after = case["inventory_after"]
    if after["remaining"] != fixture["total_quantity"] or after["cancelled_orders"] != 1 or after["confirmed_orders"] != 0:
        case["reasons"].append("Duplicate cancellation did not return exactly one order's inventory")
    return _finish_case(case)


async def _mixed(client, db_path, accounts):
    prebooked = min(10, max(1, len(accounts)//5))
    fixture = _fixture(db_path, "mixed buy and cancel", prebooked*2)
    tid = fixture["ticket_type_id"]
    setup, orders, setup_settled = await _setup_orders(client, db_path, accounts[:prebooked], fixture)
    case = {"name": "mixed_buy_cancel", "fixture": fixture, "setup_requests": setup,
            "inventory_before": _snapshot(db_path, tid), "reasons": []}
    if not setup_settled or len(orders) != prebooked:
        case["reasons"].append("Could not create and commit all mixed-contention fixtures")
    specifications = [_buy(a, tid) for a in accounts]
    specifications.extend(_cancel(a, oid) for a, oid in orders)
    # Interleave cancellations and purchases rather than queuing every buy first.
    purchase_specs = specifications[:len(accounts)]
    cancel_specs = specifications[len(accounts):]
    interleaved = []
    for i, buy in enumerate(purchase_specs):
        interleaved.append(buy)
        if i < len(cancel_specs):
            interleaved.append(cancel_specs[i])
    case.update(await _batch(client, db_path, tid, interleaved))
    new_sales = sum(_sale_ok(r, tid) for r in case["requests"] if r["operation"] == "buy")
    cancellations = sum(_cancel_ok(r, tid) for r in case["requests"] if r["operation"] == "cancel")
    case["inventory_after"], case["settled"] = await _settle(db_path, tid, len(orders)+new_sales, cancellations)
    for response in case["requests"]:
        accepted = (_sale_ok(response, tid) or _sold_out(response)) if response["operation"] == "buy" else _cancel_ok(response, tid)
        if not accepted:
            case["reasons"].append("Mixed contention produced unexpected HTTP/transport errors or invalid bodies")
            break
    if cancellations != len(orders) or new_sales == 0:
        case["reasons"].append("Mixed contention did not exercise both successful purchases and cancellations")
    after = case["inventory_after"]
    if after["order_count"] != len(orders)+new_sales or after["cancelled_orders"] != len(orders):
        case["reasons"].append("Mixed contention HTTP successes do not match committed order records")
    return _finish_case(case)


def _verdict(cases, label):
    if not cases:
        return {"status": "INCONCLUSIVE", "successful_sales": 0, "reasons": [f"No {label} probes completed"], "observed": {}}
    reasons = [f"{c['name']}: {reason}" for c in cases for reason in c.get("reasons", [])]
    if any(c.get("status") == "FAIL" for c in cases):
        status = "FAIL"
    elif any(c.get("status") != "PASS" for c in cases):
        status = "INCONCLUSIVE"
    else:
        status = "PASS"
    return {"status": status, "successful_sales": sum(c.get("successful_sales", 0) for c in cases),
            "reasons": reasons, "observed": {"cases": len(cases), "max_peak_inflight": max(c.get("peak_inflight", 0) for c in cases),
            "successful_cancellations": sum(c.get("successful_cancellations", 0) for c in cases),
            "invariant_violations": sum(not s.get("invariant_ok", False) for c in cases
                                         for s in c.get("inventory_samples", []) + ([c["inventory_after"]] if c.get("inventory_after") else []))}}


async def _run_async(base_url, run_dir, concurrency):
    db_path = run_dir / "concert.db"
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    if not db_path.is_file() or concurrency < 2:
        raise RuntimeError("Need an existing fresh benchmark database and >=2 concurrent requests")
    accounts = _accounts(db_path, manifest, concurrency)
    cases = []
    limits = httpx.Limits(max_connections=concurrency+10, max_keepalive_connections=concurrency+10)
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=30, limits=limits, trust_env=False) as client:
        for name, operation in [
            ("oversell_burst", lambda: _burst(client, db_path, accounts)),
            ("distinct_order_cancellations", lambda: _unique_cancellations(client, db_path, accounts)),
            *[(f"duplicate_cancellation_trial_{i}", lambda i=i: _duplicate_cancellation(client, db_path, accounts, i)) for i in range(1, 6)],
            ("mixed_buy_cancel", lambda: _mixed(client, db_path, accounts)),
        ]:
            try:
                cases.append(await operation())
            except Exception as exc:
                cases.append({"name": name, "status": "FAIL", "successful_sales": 0,
                              "reasons": [f"Infrastructure exception: {type(exc).__name__}: {exc}"]})
    return cases


def run_suite(base_url, run_dir, concurrency=100):
    """Run isolated probes while the API is online; persist every result to JSON."""
    run_dir = Path(run_dir).resolve()
    result = {"schema_version": SCHEMA_VERSION, "requested_concurrency": concurrency,
              "auth_mode": "Locally signed HS256 tokens for existing benchmark fixture users; login excluded",
              "notes": ["Fixtures have separate concert and ticket-type IDs; baseline inventory is not modified",
                        "No HTTP retries; transport errors, unexpected 4xx and 5xx fail the corresponding probe",
                        "PASS records the finite observed cases; repeated runs increase race coverage"],
              "cases": [], "prerequisite_errors": []}
    try:
        result["cases"] = asyncio.run(_run_async(base_url, run_dir, concurrency))
    except Exception as exc:
        result["prerequisite_errors"].append(f"{type(exc).__name__}: {exc}")
    result["nfr04"] = _verdict([c for c in result["cases"] if c["name"] == "oversell_burst"], "no-overselling")
    # Service/pool failures do not by themselves prove overselling. If no
    # inventory violation was observed, an incomplete purchase burst is inconclusive.
    if result["nfr04"]["status"] == "FAIL":
        burst_cases = [c for c in result["cases"] if c["name"] == "oversell_burst"]
        oversold = any(
            c.get("successful_sales", 0) > c.get("fixture", {}).get("total_quantity", float("inf"))
            or any(not snap.get("invariant_ok", False) for snap in
                   c.get("inventory_samples", []) + ([c["inventory_after"]] if c.get("inventory_after") else []))
            for c in burst_cases)
        if not oversold:
            result["nfr04"]["status"] = "INCONCLUSIVE"
            result["nfr04"]["reasons"].append("No overselling violation observed, but the purchase contention probe did not complete successfully; availability errors cannot establish no-overselling.")
    result["nfr05"] = _verdict([c for c in result["cases"] if c["name"] != "oversell_burst"], "concurrent updates")
    for nfr in ("nfr04", "nfr05"):
        result[nfr]["reasons"].extend(result["prerequisite_errors"])
        if result["prerequisite_errors"]:
            result[nfr]["status"] = "INCONCLUSIVE"
    (run_dir / "concurrency.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--users", type=int, default=100)
    args = parser.parse_args()
    result = run_suite(args.host, args.run_dir, args.users)
    print(json.dumps({key: result[key] for key in ("nfr04", "nfr05")}, indent=2, ensure_ascii=False))
    raise SystemExit(0 if all(result[key]["status"] == "PASS" for key in ("nfr04", "nfr05")) else 1)
