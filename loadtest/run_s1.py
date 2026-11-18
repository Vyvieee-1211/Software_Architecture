"""Chạy riêng S1 ba lượt với một API worker và pool 20 + 10; mỗi lượt một DB sạch."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from loadtest.run import ROOT


def save(folder, state):
    (folder / "progress.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# S1 — một API worker, pool 20 + 10", "",
             "3.000 user, tăng 100 user/giây, steady 120 giây; một master + bốn Locust worker.",
             "Mỗi lượt một DB mới; API chạy trên Windows như baseline, timeout pool giữ 2 giây.",
             "", "| Lượt | Trạng thái | HTTP request | HTTP lỗi | Tỷ lệ lỗi |", "|---:|---|---:|---:|---:|"]
    for row in state["rounds"]:
        ratio = f"{row['failure_ratio']:.2%}" if "failure_ratio" in row else ""
        lines.append(f"| {row['round']} | {row['status']} | {row.get('http_requests', '')} | {row.get('http_failures', '')} | {ratio} |")
    (folder / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    folder = args.output.resolve()
    folder.mkdir(parents=True, exist_ok=False)
    base = os.environ["DATABASE_URL"]
    state = {"status": "RUNNING", "api_workers": 1, "pool_size": 20, "max_overflow": 10,
             "pool_timeout_seconds": 2, "rounds": []}
    save(folder, state)
    for index in range(1, 4):
        stamp = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%Y%m%d_%H%M%S")
        database = f"loadtest_s1_w1_p20_{stamp}_{index}"
        record = {"round": index, "status": "RUNNING", "started_at": time.time(), "database": database}
        state["rounds"].append(record)
        save(folder, state)
        try:
            with psycopg.connect(base.replace("postgresql+psycopg://", "postgresql://"), autocommit=True, connect_timeout=10) as conn:
                conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
            url = make_url(base).set(database=database).render_as_string(hide_password=False)
            env = dict(os.environ, DATABASE_URL=url, LOADTEST_DATABASE_URL=url,
                       DB_POOL_SIZE="20", DB_MAX_OVERFLOW="10", DB_POOL_TIMEOUT_SECONDS="2",
                       PAYMENT_WEBHOOK_SECRET="dev-payment-secret-change-me", PYTHONIOENCODING="utf-8")
            env.pop("LOADTEST_API_CONTAINER", None)
            command = [sys.executable, "-m", "loadtest.run", "S1", "--start-api", "--prepare",
                       "--db-pool-size", "20", "--locust-workers", "4",
                       "--api-port", "8004", "--host", "http://localhost:8004", "--output", str(folder / f"r{index}")]
            print(f"START S1 {index}/3 | API=1 pool=20+10", flush=True)
            with (folder / f"r{index}.log").open("w", encoding="utf-8") as log:
                result = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            record["exit_code"] = result.returncode
            paths = sorted((folder / f"r{index}").glob("*/nfr_results.json"))
            if paths:
                report = json.loads(paths[-1].read_text(encoding="utf-8"))
                record.update({key: report[key] for key in ("status", "http_requests", "http_failures", "failure_ratio", "inventory_ok", "metrics")})
                record["report"] = str(paths[-1].relative_to(folder))
            else:
                record["status"] = "ERROR"
        except Exception as exc:
            record["status"] = "ERROR"
            record["error"] = type(exc).__name__
        record["finished_at"] = time.time()
        save(folder, state)
        print(f"END S1 {index}/3: {record['status']}", flush=True)
    state["status"] = "FINISHED"
    save(folder, state)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
