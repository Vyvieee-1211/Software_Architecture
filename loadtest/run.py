"""Chạy S0–S10 theo mục 2 notebook; không xóa DB và không tự dừng dịch vụ đang dùng."""
import argparse
import csv
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILES = {
    "S0": ("smoke", 10, 10, 60), "S1": ("browse", 3000, 100, 120),
    "S2": ("flash", 3000, 200, 60), "S3": ("funnel", 1000, 50, 600),
    "S4": ("abandon", 500, 50, 160), "S5": ("retry", 500, 50, 120),
    "S6": ("contention", 200, 20, 180), "S7": ("replay", 300, 30, 120),
    "S8": ("soak", 300, 30, 1800), "S9": ("scalability", 1000, 50, 600),
    "S10": ("fault", 1000, 50, 600),
}


def launch(command, env, log):
    return subprocess.Popen(command, cwd=ROOT, env=dict(env, PYTHONIOENCODING="utf-8"), stdout=log, stderr=subprocess.STDOUT,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def inventory(url, output):
    """Đọc trong một snapshot, không sửa dữ liệu; kiểm tra cả liên kết vé/đơn và QR."""
    import psycopg
    query = """
        SELECT tt.id, tt.total_quantity, tt.remaining,
          COALESCE((SELECT SUM(quantity) FROM orders WHERE ticket_type_id=tt.id AND status IN ('held','paid')),0),
          (SELECT COUNT(*) FROM tickets WHERE ticket_type_id=tt.id AND status='available'),
          (SELECT COUNT(*) FROM tickets WHERE ticket_type_id=tt.id AND status IN ('held','sold')),
          (SELECT COUNT(*) FROM tickets t LEFT JOIN orders o ON o.id=t.order_id WHERE t.ticket_type_id=tt.id AND
            ((t.status='available' AND (t.order_id IS NOT NULL OR t.qr_code IS NOT NULL)) OR
             (t.status='held' AND (o.id IS NULL OR o.status!='held' OR t.qr_code IS NOT NULL)) OR
             (t.status='sold' AND (o.id IS NULL OR o.status!='paid' OR t.qr_code IS NULL)) OR
             t.status NOT IN ('available','held','sold') OR
             (t.order_id IS NOT NULL AND o.ticket_type_id!=t.ticket_type_id))),
          (SELECT COUNT(*) FROM orders o WHERE o.ticket_type_id=tt.id AND o.status IN ('held','paid')
             AND o.quantity != (SELECT COUNT(*) FROM tickets t WHERE t.order_id=o.id))
        FROM ticket_types tt ORDER BY tt.id
    """
    with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://")) as conn:
        rows = conn.execute(query).fetchall()
    checks = []
    with (output / "inventory_check.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["type_id", "total", "remaining", "active", "available", "reserved", "bad_tickets", "bad_orders", "ok"])
        for tid, total, remaining, active, available, reserved, bad_tickets, bad_orders in rows:
            ok = 0 <= remaining <= total and remaining + active == total and available == remaining and reserved == active and bad_tickets == bad_orders == 0
            writer.writerow([tid, total, remaining, active, available, reserved, bad_tickets, bad_orders, ok])
            checks.append(ok)
    return bool(checks) and all(checks)


def report(output, scenario, codes, database_url):
    counters = Counter()
    summaries = [json.loads(path.read_text(encoding="utf-8")) for path in output.glob("summary_*.json")]
    for summary in summaries:
        counters.update(summary["counters"])
    samples = defaultdict(list)
    failures = requests = 0
    for path in output.glob("requests_*.csv"):
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if row["phase"] != "steady":
                    continue
                samples[f"{row['method']} {row['name']}"].append(float(row["ms"]))
                if row["method"] not in {"QUEUE", "VERIFY", "SWEEPER"}:
                    requests += 1
                    failures += bool(row["error"])
    inventory_ok = None
    inventory_error = None
    if database_url:
        try:
            inventory_ok = inventory(database_url, output)
        except Exception as exc:
            # Không lưu URL kết nối có thể chứa mật khẩu vào báo cáo.
            inventory_error = type(exc).__name__
    metrics = {name: {"count": len(values), "p50_ms": percentile(values, .5),
                      "p95_ms": percentile(values, .95), "p99_ms": percentile(values, .99)}
               for name, values in samples.items()}
    limit = 0 if scenario in {"S0", "S1"} else .01
    failed = any(codes) or (scenario != "S10" and failures / max(requests, 1) > limit) or inventory_ok is False or any(
        counters[k] for k in ("verify_fail", "script_error", "setup_failed", "request_timeout", "setup_timeout"))
    latency_ok = all(v["p95_ms"] < 300 for k, v in metrics.items() if k.startswith("GET /concerts"))
    latency_ok &= all(v["p95_ms"] < 1000 and v["p99_ms"] < 2000 for k, v in metrics.items() if k.startswith("POST /orders ["))
    failed |= not latency_ok
    verified = counters["verify_ok"] > 0 if scenario in {"S0", "S5", "S6", "S7"} else True
    complete = bool(requests and summaries and all(s["steady_started"] for s in summaries)) and verified
    if scenario != "S1":
        complete &= inventory_ok is True
    if scenario == "S4":
        complete &= counters["expired"] > 0 and counters["expired"] == counters["held"]
    metadata_path = output / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
    expected_users = metadata.get("users", 1)
    if scenario == "S6":
        complete &= counters["cancel_held_cases"] == expected_users and counters["refund_paid_cases"] == expected_users
        complete &= counters["expiry_race_cases"] == min(expected_users, int(os.getenv("EXPIRY_RACE_USERS", "100")))
    if scenario == "S7":
        complete &= counters["replay_cases"] == expected_users
    if scenario == "S2":
        complete &= counters["flash_quantity"] == 500
    if scenario in {"S3", "S8", "S9", "S10"}:
        complete &= counters["held"] > 0 and counters["paid"] > 0
    # Soak, mở rộng và chịu lỗi cần đối chiếu tài nguyên/log, không suy ra PASS chỉ từ HTTP.
    from loadtest.assessment import assess
    assessment = assess(output, scenario, metadata, database_url, counters)
    failed |= any(value is False for value in assessment["checks"].values())
    complete &= all(value is True for value in assessment["checks"].values())
    if scenario == "S9":
        complete = False  # Kết luận mở rộng thuộc báo cáo tổng hợp ba cấu hình, ba lượt.
    result = {"scenario": scenario, "status": "FAIL" if failed else "PASS" if complete else "INCONCLUSIVE",
              "http_requests": requests, "http_failures": failures, "failure_ratio": failures / max(requests, 1),
              "counters": dict(counters), "inventory_ok": inventory_ok, "inventory_error": inventory_error,
              "metrics": metrics,
              "scenario_checks": assessment,
              "scope": "Đánh giá tự động một phần; xem README để đối chiếu đầy đủ tiêu chí notebook.",
              "note": "Đối chiếu scenario_checks.json; thiếu bằng chứng không được tính PASS. Kết luận S9 nằm trong báo cáo ma trận tổng hợp."}
    (output / "nfr_results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=PROFILES)
    parser.add_argument("--host", default="http://localhost:8000")
    parser.add_argument("--users", type=int)
    parser.add_argument("--rate", type=float)
    parser.add_argument("--seconds", type=float, help="Thời gian steady, không gồm đăng nhập/ramp-up")
    parser.add_argument("--locust-workers", type=int, default=1)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--output", type=Path, default=ROOT / "loadtest/results")
    parser.add_argument("--start-api", action="store_true", help="Chạy API riêng theo DATABASE_URL hiện tại")
    parser.add_argument("--prepare", action="store_true", help="Tạo concert/vé riêng trong DATABASE_URL trước mỗi lượt")
    parser.add_argument("--workload", choices=["flash", "funnel"], help="S9: chọn workload S2 hoặc S3")
    parser.add_argument("--db-pool-size", type=int, default=5)
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--inject-db-outage", action="store_true", help="S10: dừng DB Docker 10s ở giây steady 180")
    parser.add_argument("--outage-at", type=float, default=180)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    workload, users, rate, seconds = PROFILES[args.scenario]
    if args.workload:
        if args.scenario != "S9":
            parser.error("--workload chỉ dùng cho S9")
        workload = args.workload
        if workload == "flash":
            _, users, rate, seconds = PROFILES["S2"]
    users = args.users if args.users is not None else users
    rate = args.rate if args.rate is not None else rate
    seconds = args.seconds if args.seconds is not None else seconds
    if min(users, rate, seconds, args.locust_workers, args.repeat, args.db_pool_size) <= 0:
        parser.error("Các giá trị tải và số lần chạy phải > 0")
    if args.inject_db_outage and (args.scenario != "S10" or seconds <= args.outage_at + 25):
        parser.error("Chỉ gây lỗi ở S10, cần thời gian quan sát phục hồi ít nhất 25s sau thời điểm gây lỗi")
    plan = {"scenario": args.scenario, "workload": workload, "users": users, "spawn_rate": rate,
            "steady_seconds": seconds, "locust_workers": args.locust_workers, "repeat": args.repeat,
            "api_workers": 1 if args.start_api else None, "db_pool_size": args.db_pool_size,
            "inject_db_outage": args.inject_db_outage}
    if args.dry_run:
        print(json.dumps(plan, indent=2))
        return 0
    env = dict(os.environ, SCENARIO=workload, STEADY_SECONDS=str(seconds), PAYMENT_PROVIDER="mock",
               LOADTEST_WORKERS=str(args.locust_workers))
    if args.scenario in {"S4", "S6"}:
        env.update(HOLD_MINUTES="1", HOLD_SWEEP_INTERVAL_SECONDS="5")
    status = 0
    for iteration in range(1, args.repeat + 1):
        output = args.output.resolve() / f"{args.scenario}_{time.strftime('%Y%m%d_%H%M%S')}_{iteration}"
        output.mkdir(parents=True, exist_ok=False)
        env["LOADTEST_OUTPUT"] = str(output)
        if args.prepare:
            from loadtest.prepare import prepare
            url = env.get("DATABASE_URL", "")
            if not url.startswith("postgresql"):
                parser.error("--prepare cần DATABASE_URL PostgreSQL thử nghiệm")
            env.update(prepare(url, 500 if workload == "flash" else 10000,
                               users=0 if workload == "browse" else math.ceil(users / args.locust_workers) * args.locust_workers,
                               accounts_path=output / "auth_accounts.json" if workload != "browse" else None))
            env.setdefault("LOADTEST_DATABASE_URL", url)
        metadata = dict(plan, iteration=iteration, platform=platform.platform(), python=sys.version,
                        host=args.host, source_hashes={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in (ROOT / "loadtest").glob("*.py")})
        metadata["git_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        metadata["api_platform"] = "Linux (Docker)" if env.get("LOADTEST_API_CONTAINER") else platform.platform()
        metadata["api_container"] = env.get("LOADTEST_API_CONTAINER")
        metadata["db_container"] = env.get("LOADTEST_DB_CONTAINER")
        metadata["db_max_overflow"] = int(env.get("DB_MAX_OVERFLOW", "10"))
        metadata["db_pool_timeout_seconds"] = float(env.get("DB_POOL_TIMEOUT_SECONDS", "2"))
        metadata["fixture"] = {key: env.get(key) for key in ("CONCERT_ID", "TICKET_TYPE_ID", "HOLD_MINUTES", "HOLD_SWEEP_INTERVAL_SECONDS")}
        metadata["auth_setup"] = "Tài khoản seed trước pha đo; đăng nhập API thật, bcrypt chuẩn; tối đa 2 login đồng thời/worker."
        (output / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        processes, logs = [], []
        api = None
        done = threading.Event()
        monitor = None
        base = [sys.executable, "-m", "locust", "-f", "loadtest/locustfile.py", "--host", args.host]
        try:
            if args.start_api:
                if not env.get("DATABASE_URL", "").startswith("postgresql"):
                    raise ValueError("--start-api cần DATABASE_URL PostgreSQL thử nghiệm đã seed")
                api_env = dict(env, DB_POOL_SIZE=str(args.db_pool_size))
                api_log = (output / "api.log").open("w", encoding="utf-8")
                logs.append(api_log)
                api = launch([sys.executable, "-m", "uvicorn", "loadtest.instrumented:app", "--workers", "1",
                              "--port", str(args.api_port)], api_env, api_log)
                import requests
                for _ in range(60):
                    if api.poll() is not None:
                        raise RuntimeError("API không khởi động; xem api.log")
                    try:
                        if requests.get(args.host + "/concerts", timeout=1).status_code == 200:
                            break
                    except requests.RequestException:
                        pass
                    time.sleep(1)
                else:
                    raise RuntimeError("API không sẵn sàng sau 60s")
            # Monitor dùng docker stats nếu có; lỗi được lưu, không giả số liệu.
            from loadtest.monitoring import monitor_services
            args.monitor_database_url = env.get("LOADTEST_DATABASE_URL", "")
            args.monitor_ticket_type = env.get("TICKET_TYPE_ID")
            args.monitor_steady_seconds = seconds
            monitor = threading.Thread(target=monitor_services, args=(output, done, args, api.pid if api else None), daemon=True)
            monitor.start()
            master_args = ["--headless", "-u", str(users), "-r", str(rate), "--stop-timeout", "10",
                           "--csv", str(output / "locust"), "--csv-full-history", "--html", str(output / "report.html")]
            if args.scenario == "S10":
                master_args += ["--exit-code-on-error", "0"]
            if args.locust_workers > 1:
                master_args += ["--master", "--expect-workers", str(args.locust_workers), "--expect-workers-max-wait", "60"]
            log = (output / "locust.log").open("w", encoding="utf-8")
            logs.append(log)
            master = launch(base + master_args, env, log)
            processes.append(master)
            if args.locust_workers > 1:
                for index in range(args.locust_workers):
                    worker_log = (output / f"worker_{index}.log").open("w", encoding="utf-8")
                    logs.append(worker_log)
                    processes.append(launch(base + ["--worker"], env, worker_log))
            codes = [master.wait(timeout=seconds + float(env.get("SETUP_TIMEOUT_SECONDS", "600")) + 120)]
            for worker in processes[1:]:
                codes.append(worker.wait(timeout=30))
            if workload in {"funnel", "soak", "fault"} and env.get("LOADTEST_DATABASE_URL"):
                drain_database(env["LOADTEST_DATABASE_URL"], output, timeout=900)
            done.set()
            if monitor:
                monitor.join(timeout=120)
            result = report(output, args.scenario, codes, env.get("LOADTEST_DATABASE_URL"))
            print(f"{args.scenario}: {result['status']} | {output}")
            status |= result["status"] == "FAIL"
        finally:
            done.set()
            if monitor:
                monitor.join(timeout=30)
            for process in processes + ([api] if api else []):
                if process.poll() is None:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                    else:
                        process.terminate()
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
            for log in logs:
                log.close()
    return int(status)


def drain_database(url, output, timeout=900):
    """Chờ queue rỗng và đơn bỏ hết hạn; không cộng thời gian này vào pha steady."""
    import psycopg
    deadline = time.monotonic() + timeout
    result = {"started_at": time.time(), "complete": False}
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://"), connect_timeout=3) as conn:
                pending, held = conn.execute("SELECT (SELECT COUNT(*) FROM order_requests WHERE status='pending'), (SELECT COUNT(*) FROM orders WHERE status='held')").fetchone()
            result.update(pending=pending, held=held)
            if pending == held == 0:
                result["complete"] = True
                break
        except Exception as exc:
            result["error"] = type(exc).__name__
        time.sleep(2)
    result["finished_at"] = time.time()
    (output / "drain.json").write_text(json.dumps(result, indent=2), encoding="utf-8")



if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
