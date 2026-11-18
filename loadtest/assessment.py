"""Đối chiếu bằng chứng S0–S10 với tiêu chí notebook, ghi rõ phần chưa đủ dữ liệu."""
import csv
import json
import math
import re
from collections import Counter, defaultdict
from statistics import median


def rows(path):
    if path.exists():
        with path.open(encoding="utf-8", newline="") as handle:
            yield from csv.DictReader(handle)


def p95(values):
    return sorted(values)[max(0, math.ceil(len(values) * .95) - 1)]


def assess(output, scenario, metadata, database_url, counters=None):
    checks, evidence = {}, {}
    start_file = output / "steady_start.json"
    start = json.loads(start_file.read_text(encoding="utf-8"))["timestamp"] if start_file.exists() else None
    duration = metadata.get("steady_seconds", 0)
    expected = metadata.get("users", 0)
    counters = counters or {}
    if scenario in {"S3", "S8", "S10"} or scenario == "S9" and metadata.get("workload") == "funnel":
        held, paid = counters.get("held", 0), counters.get("paid", 0)
        evidence["paid_per_successful_hold_ratio"] = paid / held if held else None
        checks["paid_ratio_approximately_72_percent"] = abs(paid / held - .72) <= .05 if held >= 100 else None
    if scenario in {"S3", "S8", "S10"} or scenario == "S9" and metadata.get("workload") == "funnel":
        drain_file = output / "drain.json"
        checks["queue_and_abandoned_holds_drained"] = json.loads(drain_file.read_text(encoding="utf-8"))["complete"] if drain_file.exists() else None
    if scenario == "S4":
        stock = [r for r in rows(output / "remaining.csv") if r.get("first_hold_at")]
        restored = next((r for r in stock if int(r["remaining"]) == int(r["initial_remaining"])
                         and int(r["expired_orders"]) > 0 and int(r["pending_requests"]) == 0), None)
        if restored:
            elapsed = float(restored["timestamp"]) - float(restored["first_hold_at"])
            lag = float(restored["timestamp"]) - float(restored["last_expires_at"])
            evidence.update(stock_recovered_seconds_from_first_hold=elapsed, stock_recovered_seconds_after_last_expiry=lag)
            checks["remaining_restored_after_expiry_under_90s"] = 0 <= lag < 90
        else:
            checks["remaining_restored_after_expiry_under_90s"] = None
    if scenario == "S8":
        first, last = defaultdict(list), defaultdict(list)
        for path in output.glob("requests_*.csv"):
            for row in rows(path):
                if row["phase"] != "steady" or row["method"] not in {"GET", "POST", "DELETE"} or start is None:
                    continue
                key = f"{row['method']} {row['name']}"
                stamp = float(row["timestamp"])
                if stamp <= start + 300:
                    first[key].append(float(row["ms"]))
                if stamp >= start + duration - 300:
                    last[key].append(float(row["ms"]))
        comparison = {key: {"first_p95_ms": p95(values), "last_p95_ms": p95(last[key]),
                            "ratio": p95(last[key]) / max(p95(values), .001)}
                      for key, values in first.items() if last[key]}
        evidence["latency_first_last_5m"] = comparison
        checks["endpoint_p95_drift_at_most_20_percent"] = all(v["ratio"] <= 1.2 for v in comparison.values()) if comparison else None
        resources = [r for r in rows(output / "resources.csv") if r["phase"] == "steady" and float(r.get("api_rss_bytes") or 0) > 0]
        first_rss = [float(r["api_rss_bytes"]) for r in resources if start and float(r["timestamp"]) <= start + 300]
        last_rss = [float(r["api_rss_bytes"]) for r in resources if start and float(r["timestamp"]) >= start + duration - 300]
        if first_rss and last_rss:
            ratio = median(last_rss) / median(first_rss)
            evidence["api_rss_median_ratio_last_first"] = ratio
            # Notebook không định lượng RAM ổn định: chỉ báo xu hướng, không tự suy ra leak.
            checks["api_rss_observed_stable"] = True if ratio <= 1.2 else None
        else:
            checks["api_rss_observed_stable"] = None
        activity = [r for r in rows(output / "pg_activity.csv") if r["phase"] == "steady" and r["connections"]]
        first_connections = [int(r["connections"]) for r in activity if start and float(r["timestamp"]) <= start + 300]
        last_connections = [int(r["connections"]) for r in activity if start and float(r["timestamp"]) >= start + duration - 300]
        if first_connections and last_connections:
            evidence["pg_connections_first_last_median"] = [median(first_connections), median(last_connections)]
            checks["pg_connections_observed_bounded"] = True if median(last_connections) <= median(first_connections) + 2 else None
        else:
            checks["pg_connections_observed_bounded"] = None
    if scenario == "S10":
        log_path = output / "fault.log"
        log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        marks = {name: float(stamp) for name, stamp in re.findall(r"^(stop_requested|stop_completed|start_completed|db_ready) ([\d.]+)$", log, re.M)}
        evidence["fault_timestamps"] = marks
        recovery = []
        unavailable = []
        outside_count = outside_errors = invalid_during_outage = 0
        for path in output.glob("requests_*.csv"):
            for row in rows(path):
                stamp = float(row["timestamp"])
                if row["method"] not in {"GET", "POST", "DELETE"}:
                    continue
                if row["phase"] != "steady":
                    continue
                during = "stop_requested" in marks and marks["stop_requested"] <= stamp <= marks.get("db_ready", float("inf")) + 15
                if during:
                    if row["error"] and not (row["status"] == "503" and row.get("retry_after")):
                        invalid_during_outage += 1
                else:
                    outside_count += 1
                    outside_errors += bool(row["error"])
                if "stop_requested" in marks and stamp >= marks["stop_requested"] and row["status"] == "503":
                    unavailable.append(row)
                if "db_ready" in marks and stamp >= marks["db_ready"] and row["status"] in {"200", "201", "202"} and not row["error"]:
                    recovery.append(stamp)
        checks["fault_was_injected"] = all(k in marks for k in ("stop_completed", "start_completed", "db_ready"))
        checks["503_has_retry_after"] = all(r.get("retry_after") for r in unavailable) if unavailable else None
        checks["no_timeout_or_unexpected_status_during_outage"] = invalid_during_outage == 0
        checks["normal_window_error_ratio_at_most_1_percent"] = outside_errors / max(outside_count, 1) <= .01
        evidence.update(outage_unexpected_errors=invalid_during_outage, normal_window_errors=outside_errors, normal_window_requests=outside_count)
        if recovery:
            delay = min(recovery) - marks["db_ready"]
            evidence["recovery_seconds_after_db_ready"] = delay
            checks["recovery_under_15s"] = delay < 15
        else:
            checks["recovery_under_15s"] = None
        api_log = (output / "api.log").read_text(encoding="utf-8", errors="replace") if (output / "api.log").exists() else ""
        checks["circuit_open_and_closed_logged"] = "db_circuit state=open" in api_log and "db_circuit state=closed" in api_log
    if database_url and scenario in {"S2", "S5", "S6", "S7"}:
        import psycopg
        try:
            with psycopg.connect(database_url.replace("postgresql+psycopg://", "postgresql://"), connect_timeout=5) as conn:
                duplicate_events = conn.execute("""SELECT COUNT(*) FROM (SELECT provider,payment_id,type,status
                    FROM transactions GROUP BY provider,payment_id,type,status HAVING COUNT(*)>1) d""").fetchone()[0]
                checks["no_duplicate_transaction_events"] = duplicate_events == 0
                if scenario == "S2":
                    held, paid = conn.execute("SELECT COALESCE(SUM(quantity) FILTER (WHERE status='held'),0), COALESCE(SUM(quantity) FILTER (WHERE status='paid'),0) FROM orders").fetchone()
                    evidence.update(flash_held_quantity=int(held), flash_paid_quantity=int(paid))
                    checks["flash_exactly_500_held_no_paid"] = held == 500 and paid == 0
                if scenario == "S5":
                    duplicates = conn.execute("""SELECT COUNT(*) FROM (SELECT user_id,idempotency_key FROM orders
                        WHERE idempotency_key IS NOT NULL GROUP BY user_id,idempotency_key HAVING COUNT(*)>1) d""").fetchone()[0]
                    request_duplicates = conn.execute("""SELECT COUNT(*) FROM (SELECT order_id FROM order_requests
                        WHERE order_id IS NOT NULL GROUP BY order_id HAVING COUNT(*)>1) d""").fetchone()[0]
                    checks["idempotency_one_order_per_key_and_request"] = duplicates == request_duplicates == 0
                if scenario == "S6":
                    api_log = (output / "api.log").read_text(encoding="utf-8", errors="replace") if (output / "api.log").exists() else ""
                    calls = Counter(re.findall(r"loadtest_mock_refund payment_id=(\S+)", api_log))
                    refunds = conn.execute("""SELECT payment_id, COUNT(*) FILTER (WHERE status='succeeded'),
                        COUNT(*) FILTER (WHERE status='pending') FROM transactions WHERE type='refund' GROUP BY payment_id""").fetchall()
                    evidence["mock_refund_call_count"] = sum(calls.values())
                    checks["each_refund_gateway_called_once"] = all(calls[payment_id] == 1 and succeeded == pending == 1
                                                                 for payment_id, succeeded, pending in refunds) if refunds else None
                    checks["no_extra_refund_gateway_calls"] = set(calls) == {r[0] for r in refunds} if calls else None
                if scenario == "S7":
                    paid, quantity = conn.execute("SELECT COUNT(*), COALESCE(SUM(quantity),0) FROM orders WHERE status='paid'").fetchone()
                    sold = conn.execute("SELECT COUNT(*) FROM tickets WHERE status='sold'").fetchone()[0]
                    evidence.update(replay_paid_orders=paid, replay_paid_quantity=int(quantity), replay_sold_tickets=sold)
                    checks["300_payments_and_sold_quantity_match"] = paid == expected and sold == quantity and paid > 0
        except psycopg.Error as exc:
            evidence["database_audit_error"] = type(exc).__name__
            checks["database_audit_available"] = None
    result = {"checks": checks, "evidence": evidence,
              "conventions": "RAM: so trung vị 5 phút đầu/cuối, tăng <=20% là ổn định quan sát; kết nối: chênh <=2. Đây là quy ước đo, không chứng minh tuyệt đối không leak."}
    (output / "scenario_checks.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
