"""Thu tài nguyên API/DB, kết nối PostgreSQL, hồi kho và mốc gây lỗi theo thời gian."""
import csv
import json
import os
import re
import subprocess
import threading
import time

import psutil
import psycopg

from loadtest.run import ROOT


def monitor_services(output, done, args, api_pid=None):
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    latest_docker = {}
    docker_error = []
    docker_process = None
    stopped_db = False
    outage_done = False
    processes = {}
    url = os.getenv("LOADTEST_DATABASE_URL", os.getenv("DATABASE_URL", "")).replace("postgresql+psycopg://", "postgresql://")
    # Runner truyền URL/fixture qua args vì env của tiến trình con khác env của runner.
    url = getattr(args, "monitor_database_url", url).replace("postgresql+psycopg://", "postgresql://")
    ticket_type = getattr(args, "monitor_ticket_type", None)
    api_container = os.getenv("LOADTEST_API_CONTAINER")
    initial_remaining = None
    start = None

    def docker_reader():
        nonlocal docker_process
        try:
            docker_process = subprocess.Popen(["docker", "stats", "--format", "{{json .}}"],
                                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                              text=True, creationflags=flags)
            for line in docker_process.stdout:
                try:
                    record = json.loads(re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line))
                    latest_docker[record.get("Name", "db")] = record
                except ValueError:
                    pass
                if done.is_set():
                    break
        except OSError as exc:
            docker_error.append(type(exc).__name__)

    def db_command(action, handle):
        return subprocess.run(["docker", "compose", action, "db"], cwd=ROOT, stdout=handle,
                              stderr=subprocess.STDOUT, check=True, timeout=45, creationflags=flags)

    def inject_outage():
        nonlocal stopped_db, outage_done
        with (output / "fault.log").open("a", encoding="utf-8") as log:
            log.write(f"stop_requested {time.time()}\n"); log.flush()
            stopped_db = True
            db_command("stop", log)
            log.write(f"stop_completed {time.time()}\n"); log.flush()
            done.wait(10)
            db_command("start", log)
            stopped_db = False
            log.write(f"start_completed {time.time()}\n"); log.flush()
            for _ in range(20):
                try:
                    with psycopg.connect(url, connect_timeout=2) as conn:
                        conn.execute("SELECT 1")
                    log.write(f"db_ready {time.time()}\n"); log.flush()
                    break
                except psycopg.Error:
                    done.wait(1)
            outage_done = True

    reader = threading.Thread(target=docker_reader, daemon=True)
    reader.start()
    fault_thread = None
    with (output / "resources.csv").open("w", encoding="utf-8", newline="") as resources, \
         (output / "pg_activity.csv").open("w", encoding="utf-8", newline="") as activity, \
         (output / "remaining.csv").open("w", encoding="utf-8", newline="") as remaining:
        resource_writer, pg_writer, stock_writer = csv.writer(resources), csv.writer(activity), csv.writer(remaining)
        resource_writer.writerow(["timestamp", "phase", "api_cpu_percent", "api_rss_bytes", "docker_stats_json", "error"])
        pg_writer.writerow(["timestamp", "phase", "connections", "active", "idle", "waiting_locks", "deadlocks", "error"])
        stock_writer.writerow(["timestamp", "phase", "initial_remaining", "remaining", "held_quantity", "expired_orders", "pending_requests", "first_hold_at", "last_expires_at", "error"])
        try:
            while not done.is_set():
                tick = time.monotonic()
                if start is None:
                    path = output / "steady_start.json"
                    if path.exists():
                        try:
                            start = json.loads(path.read_text(encoding="utf-8"))["timestamp"]
                        except (OSError, ValueError):
                            pass
                stamp = time.time()
                phase = "setup" if start is None else "steady" if stamp < start + args.monitor_steady_seconds else "drain"
                cpu = rss = 0
                error = ""
                if api_pid:
                    try:
                        parent = psutil.Process(api_pid)
                        for process in [parent] + parent.children(recursive=True):
                            process = processes.setdefault(process.pid, process)
                            cpu += process.cpu_percent()
                            rss += process.memory_info().rss
                    except (psutil.Error, OSError) as exc:
                        error = type(exc).__name__
                elif api_container and api_container in latest_docker:
                    api_stats = latest_docker[api_container]
                    cpu = float(api_stats.get("CPUPerc", "0%").rstrip("%"))
                    match = re.match(r"([\d.]+)\s*([A-Za-z]+)", api_stats.get("MemUsage", "0B").split("/")[0].strip())
                    if match:
                        scale = {"B": 1, "kB": 1000, "MB": 1000**2, "GB": 1000**3,
                                 "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}.get(match[2], 1)
                        rss = int(float(match[1]) * scale)
                resource_writer.writerow([stamp, phase, cpu, rss, json.dumps(list(latest_docker.values())), error or ",".join(docker_error)])
                if url.startswith("postgresql"):
                    try:
                        with psycopg.connect(url, connect_timeout=2, options="-c statement_timeout=2000") as conn:
                            row = conn.execute("""SELECT COUNT(*), COUNT(*) FILTER (WHERE state='active'),
                                COUNT(*) FILTER (WHERE state='idle'), COUNT(*) FILTER (WHERE wait_event_type='Lock'),
                                (SELECT deadlocks FROM pg_stat_database WHERE datname=current_database())
                                FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()""").fetchone()
                            pg_writer.writerow([stamp, phase, *row, ""])
                            if args.scenario == "S4" and ticket_type:
                                row = conn.execute("""SELECT tt.remaining,
                                    COALESCE((SELECT SUM(quantity) FROM orders WHERE ticket_type_id=tt.id AND status='held'),0),
                                    (SELECT COUNT(*) FROM orders WHERE ticket_type_id=tt.id AND status='expired'),
                                    (SELECT COUNT(*) FROM order_requests WHERE ticket_type_id=tt.id AND status='pending'),
                                    (SELECT EXTRACT(EPOCH FROM MIN(created_at)) FROM orders WHERE ticket_type_id=tt.id),
                                    (SELECT EXTRACT(EPOCH FROM MAX(expires_at)) FROM orders WHERE ticket_type_id=tt.id)
                                    FROM ticket_types tt WHERE tt.id=%s""", (int(ticket_type),)).fetchone()
                                if row:
                                    if initial_remaining is None:
                                        initial_remaining = row[0]
                                    stock_writer.writerow([stamp, phase, initial_remaining, *row, ""])
                    except psycopg.Error as exc:
                        pg_writer.writerow([stamp, phase, "", "", "", "", "", type(exc).__name__])
                resources.flush(); activity.flush(); remaining.flush()
                if args.inject_db_outage and not outage_done and fault_thread is None and start and stamp - start >= args.outage_at:
                    fault_thread = threading.Thread(target=inject_outage, daemon=True)
                    fault_thread.start()
                done.wait(max(0, 1 - (time.monotonic() - tick)))
        finally:
            if fault_thread:
                fault_thread.join(timeout=100)
            if stopped_db:
                with (output / "fault.log").open("a", encoding="utf-8") as log:
                    db_command("start", log)
            if docker_process and docker_process.poll() is None:
                docker_process.terminate()
            reader.join(timeout=3)
