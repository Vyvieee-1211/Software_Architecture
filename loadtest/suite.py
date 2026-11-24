"""Chạy tuần tự S0–S10, giữ kết quả từng lượt và tiếp tục khi một lượt FAIL."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import psycopg
from psycopg import sql

from loadtest.run import ROOT


def jobs():
    for index in range(0, 9):
        for repeat in range(1, 4):
            yield f"S{index}_r{repeat}", ["--db-pool-size", "20"]
    for pool in (5, 10, 20):
        for workload in ("flash", "funnel"):
            for repeat in range(1, 4):
                yield f"S9_{workload}_p{pool}_r{repeat}", [
                    "--workload", workload, "--db-pool-size", str(pool)]
    for repeat in range(1, 4):
        yield f"S10_r{repeat}", ["--db-pool-size", "20", "--inject-db-outage"]


def write_state(folder, state):
    from loadtest.scalability import write_scalability
    write_scalability(folder, state)
    temporary = folder / "progress.tmp"
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(folder / "progress.json")
    lines = ["# Kết quả chạy S0–S10", "", "Mỗi cấu hình chạy ba lượt; API, Locust và DB cùng máy.",
             "Một API worker, pool 20 + 10. S9 thử pool 5, 10 và 20 cho hai workload, mỗi cấu hình ba lượt.",
             "Mỗi lượt dùng DB mới và seed riêng.",
             "", "| Kịch bản | Trạng thái | HTTP requests | HTTP lỗi | Kho |", "|---|---|---:|---:|---|"]
    for job in state["jobs"]:
        result = job.get("result", {})
        lines.append(f"| {job['name']} | {job['status']} | {result.get('http_requests', '')} | "
                     f"{result.get('http_failures', '')} | {result.get('inventory_ok', '')} |")
    lines += ["", "INCONCLUSIVE nghĩa là thiếu bằng chứng để kết luận đạt đầy đủ tiêu chí.",
              "Chi tiết từng lượt nằm trong các thư mục cùng tên kịch bản."]
    (folder / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--locust-workers", type=int, default=4)
    parser.add_argument("--api-port", type=int, default=8004)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    folder = args.output.resolve()
    plan = list(jobs())
    folder.mkdir(parents=True, exist_ok=args.resume)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    base_url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    if args.resume:
        state = json.loads((folder / "progress.json").read_text(encoding="utf-8"))
        if [(job["name"], job["arguments"]) for job in state["jobs"]] != plan:
            parser.error("Thư mục này dùng kế hoạch cũ. Hãy chọn thư mục mới cho bộ chạy một API worker.")
        state["status"] = "RUNNING"
    else:
        state = {"started_at": time.time(), "status": "RUNNING", "jobs": [
            {"name": name, "arguments": extra, "status": "PENDING"} for name, extra in plan]}
    write_state(folder, state)
    for index, job in enumerate(state["jobs"], 1):
        if job["status"] not in {"PENDING", "RUNNING"}:
            continue
        if (folder / "stop-after-current").exists():
            state["status"] = "STOPPED"
            break
        job.update(status="RUNNING", started_at=time.time())
        state["current"] = job["name"]
        write_state(folder, state)
        print(f"START {index}/{len(state['jobs'])}: {job['name']}", flush=True)
        try:
            database = f"loadtest_{stamp}_{index:02d}"
            # Mỗi lượt một DB mới: yêu cầu pending và sweeper từ lượt trước không ảnh hưởng lượt sau.
            with psycopg.connect(base_url, autocommit=True, connect_timeout=10) as conn:
                conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
            # Runner dùng URL SQLAlchemy; thông tin tài khoản không được ghi vào manifest.
            from sqlalchemy.engine import make_url
            url = str(make_url(os.environ["DATABASE_URL"]).set(database=database).render_as_string(hide_password=False))
            env = dict(os.environ, DATABASE_URL=url, LOADTEST_DATABASE_URL=url,
                       PAYMENT_WEBHOOK_SECRET="dev-payment-secret-change-me", PYTHONIOENCODING="utf-8",
                       SETUP_TIMEOUT_SECONDS="1800", DB_MAX_OVERFLOW="10", DB_POOL_TIMEOUT_SECONDS="2")
            job["database"] = database
            scenario = job["name"].split("_")[0]
            locust_workers = 1 if scenario == "S0" else args.locust_workers
            command = [sys.executable, "-m", "loadtest.run", scenario, "--start-api", "--prepare",
                       "--api-port", str(args.api_port), "--host", f"http://127.0.0.1:{args.api_port}",
                       "--locust-workers", str(locust_workers), "--output", str(folder / job["name"])] + job["arguments"]
            with (folder / f"{job['name']}.log").open("w", encoding="utf-8") as log:
                completed = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            job["exit_code"] = completed.returncode
            results = sorted((folder / job["name"]).glob("*/nfr_results.json"))
            if results:
                result = json.loads(results[-1].read_text(encoding="utf-8"))
                job["result_path"] = str(results[-1].relative_to(folder))
                job["result"] = {key: result[key] for key in (
                    "status", "http_requests", "http_failures", "failure_ratio", "counters", "inventory_ok")}
                job["status"] = result["status"]
            else:
                job["status"] = "ERROR"
                job["error"] = "Không có nfr_results.json; xem log của lượt chạy."
        except Exception as exc:
            job["status"] = "ERROR"
            job["error"] = type(exc).__name__
        job["finished_at"] = time.time()
        write_state(folder, state)
        print(f"END {job['name']}: {job['status']}", flush=True)
    else:
        state["status"] = "FINISHED"
    state["finished_at"] = time.time()
    state.pop("current", None)
    write_state(folder, state)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
