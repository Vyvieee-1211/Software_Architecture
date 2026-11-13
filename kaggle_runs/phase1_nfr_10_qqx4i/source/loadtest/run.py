"""Run the complete benchmark on a fresh DB, preserve logs even when it fails."""
import argparse
import csv
import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]


def source_files():
    files = [ROOT / "requirements.txt", ROOT / "README.md"]
    for directory in ("app", "scripts", "frontend"):
        files.extend(p for p in (ROOT / directory).rglob("*") if p.is_file()
                     and p.suffix in (".py", ".txt", ".md", ".html", ".css", ".js")
                     and "__pycache__" not in p.parts)
    files.extend(p for p in (ROOT / "loadtest").iterdir() if p.is_file() and p.suffix in (".py", ".txt", ".md"))
    return sorted(files)


def run(args):
    if args.output_dir:
        run_dir = args.output_dir.resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        if any(run_dir.iterdir()):
            raise RuntimeError("Output directory must be empty; never reuse a test database.")
    else:
        parent = ROOT / "loadtest" / "runs"
        parent.mkdir(exist_ok=True)
        run_dir = Path(tempfile.mkdtemp(prefix=f"phase{args.phase}_", dir=parent))
    print(f"RESULT_DIR={run_dir}", flush=True)
    env = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME"):
        env.pop(key, None)
    env.update(PYTHONNOUSERSITE="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
               DATABASE_URL="sqlite:///" + (run_dir / "concert.db").as_posix(),
               JWT_SECRET="ticket-hunt-isolated-benchmark-secret-1211", JWT_EXPIRE_MINUTES="60",
               HUNT_MANIFEST=str(run_dir / "manifest.json"), HUNT_RUN_DIR=str(run_dir),
               NFR_STEADY_SECONDS=str(args.seconds), NFR_WARMUP_SECONDS=str(args.warmup_seconds))
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    meta = {"scenario": "ticket-hunt-nfr-v3", "phase": args.phase, "users": args.users,
            "spawn_rate": args.spawn_rate, "seconds": args.seconds, "tickets": args.tickets,
            "random_seed": args.seed, "platform": platform.platform(), "python": sys.version,
            "logical_cpus": os.cpu_count(), "sqlite_version": __import__("sqlite3").sqlite_version,
            "started_utc": datetime.now(timezone.utc).isoformat(), "api_workers": 1,
            "locust_exit_code": None, "api_shutdown_clean": False,
            "notes": "Login/ramp-up recorded separately; 120s steady load; browser and API share host.",
            "run_completed": False, "min_steady_seconds": 120, "page_limit_ms": 3000,
            "concert_mean_limit_ms": 2000, "max_error_rate": args.max_error_rate,
            "page_samples_per_route": args.page_samples,
            "max_monitor_gap_seconds": 2.5,
            "concert_endpoints": ["/concerts", "/concerts?on_sale=true", "/concerts/{id}"],
            "network_model": {"latency_ms": args.latency_ms, "download_mbps": args.download_mbps,
                              "upload_mbps": args.upload_mbps, "simulated": True}}
    api = locust = pages = None
    api_stream = locust_stream = pages_stream = None
    failure = None
    try:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", args.port))
        hashes = {}
        for path in source_files():
            relative = path.relative_to(ROOT)
            target = run_dir / "source" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            hashes[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        meta["source_sha256"] = hashes
        (run_dir / "requirements-resolved.txt").write_text(subprocess.check_output(
            [sys.executable, "-m", "pip", "freeze"], env=env, text=True, **flags), encoding="utf-8")
        print(f"Preparing fresh database and {max(100, args.users)} accounts (bcrypt, outside measured load)...", flush=True)
        with (run_dir / "prepare.log").open("w", encoding="utf-8") as out:
            subprocess.run([sys.executable, "-m", "loadtest.prepare", str(run_dir),
                            "--users", str(args.users), "--tickets", str(args.tickets), "--seed", str(args.seed)],
                           cwd=ROOT, env=env, stdout=out, stderr=subprocess.STDOUT, check=True, timeout=600, **flags)
        print("Accounts ready. Starting API...", flush=True)
        api_stream = (run_dir / "api.log").open("w", encoding="utf-8")
        api = subprocess.Popen([sys.executable, "-m", "loadtest.serve", "--port", str(args.port),
                                "--stop-file", str(run_dir / "stop-api")], cwd=ROOT, env=env,
                               stdout=api_stream, stderr=subprocess.STDOUT, **flags)
        base_url = f"http://127.0.0.1:{args.port}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if api.poll() is not None:
                raise RuntimeError("API exited before readiness; read api.log")
            try:
                with opener.open(base_url + "/health", timeout=1) as response:
                    if json.load(response).get("status") == "ok":
                        break
            except (OSError, ValueError):
                pass
            time.sleep(0.3)
        else:
            raise RuntimeError("API readiness timed out; read api.log")
        command = [sys.executable, "-m", "locust", "-f", str(ROOT / "loadtest" / "locustfile.py"),
                   "--host", base_url, "--headless", "-u", str(args.users), "-r", str(args.spawn_rate),
                   "--stop-timeout", "15", "--only-summary", "--html", str(run_dir / "report.html")]
        meta["locust_command"] = command
        print(f"Initializing {args.users} users; then measuring {args.seconds}s with all users ready...", flush=True)
        locust_stream = (run_dir / "locust.log").open("w", encoding="utf-8")
        started = time.monotonic()
        locust = subprocess.Popen(command, cwd=ROOT, env=env, stdout=locust_stream, stderr=subprocess.STDOUT, **flags)
        processes = {"api": psutil.Process(api.pid), "locust": psutil.Process(locust.pid)}
        for process in processes.values():
            process.cpu_percent()
        with (run_dir / "resources.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(["elapsed_s", "host_cpu_percent", "api_cpu_percent", "api_rss_mb", "locust_cpu_percent", "locust_rss_mb"])
            next_print = 20
            while locust.poll() is None:
                elapsed = time.monotonic() - started
                if (run_dir / "stop-run").exists():
                    raise KeyboardInterrupt("Notebook requested a clean stop")
                if elapsed > args.warmup_seconds + args.seconds + 60:
                    raise TimeoutError("Locust exceeded runtime plus shutdown allowance")
                if api.poll() is not None:
                    raise RuntimeError("API exited during benchmark; read api.log")
                if pages is None and (run_dir / "steady-start.json").exists():
                    pages_stream = (run_dir / "pages.log").open("w", encoding="utf-8")
                    pages = subprocess.Popen([sys.executable, "-m", "loadtest.pages", "--host", base_url,
                        "--run-dir", str(run_dir), "--samples", str(args.page_samples),
                        "--latency-ms", str(args.latency_ms), "--download-mbps", str(args.download_mbps),
                        "--upload-mbps", str(args.upload_mbps)], cwd=ROOT, env=env,
                        stdout=pages_stream, stderr=subprocess.STDOUT, **flags)
                    print("Steady load ready. Measuring rendered pages in Chromium...", flush=True)
                row = [round(elapsed, 2), psutil.cpu_percent()]
                for process in processes.values():
                    try:
                        row.extend([process.cpu_percent(), round(process.memory_info().rss / 1024**2, 2)])
                    except psutil.Error:
                        row.extend([None, None])
                writer.writerow(row)
                stream.flush()
                if elapsed >= next_print:
                    print(f"Elapsed: {int(elapsed)}s", flush=True)
                    next_print += 20
                time.sleep(1)
        meta["locust_exit_code"] = locust.returncode
        meta["elapsed_locust_seconds"] = round(time.monotonic() - started, 2)
        if locust.returncode not in (0, 1):
            raise RuntimeError(f"Locust exited abnormally: {locust.returncode}")
        if pages is not None:
            try:
                meta["pages_exit_code"] = pages.wait(timeout=20)
            except subprocess.TimeoutExpired:
                pages.terminate()
                pages.wait(timeout=10)
                meta["pages_exit_code"] = pages.returncode
        if args.skip_contention:
            meta["contention_skipped"] = True
            print("Smoke-only: contention skipped; NFR04/05 must be INCONCLUSIVE.", flush=True)
        else:
            print("Load drained. Checking overselling and concurrent cancellation on isolated fixtures...", flush=True)
            with (run_dir / "concurrency.log").open("w", encoding="utf-8") as out:
                probe = subprocess.Popen([sys.executable, "-m", "loadtest.concurrency", "--host", base_url,
                                         "--run-dir", str(run_dir), "--users", str(max(100, args.users))],
                                        cwd=ROOT, env=env, stdout=out, stderr=subprocess.STDOUT, **flags)
                try:
                    probe_deadline = time.monotonic() + 600
                    while probe.poll() is None:
                        if (run_dir / "stop-run").exists():
                            raise KeyboardInterrupt("Notebook requested a clean stop")
                        if time.monotonic() >= probe_deadline:
                            raise TimeoutError("Concurrency probes exceeded 600 seconds")
                        time.sleep(0.5)
                finally:
                    if probe.poll() is None:
                        probe.terminate()
                        try:
                            probe.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            probe.kill()
                            probe.wait()
            meta["concurrency_exit_code"] = probe.returncode
        meta["run_completed"] = True
    except BaseException as exc:
        failure = exc
        meta["runner_error"] = f"{type(exc).__name__}: {exc}"
        print(meta["runner_error"], flush=True)
    finally:
        if pages is not None and pages.poll() is None:
            pages.terminate()
            try:
                pages.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pages.kill()
                pages.wait()
        if locust is not None and locust.poll() is None:
            locust.terminate()
            try:
                locust.wait(timeout=10)
            except subprocess.TimeoutExpired:
                locust.kill()
                locust.wait()
        if api is not None and api.poll() is None:
            (run_dir / "stop-api").touch()
            try:
                api.wait(timeout=30)
                meta["api_shutdown_clean"] = api.returncode == 0
            except subprocess.TimeoutExpired:
                api.kill()
                api.wait()
        for stream in (api_stream, locust_stream, pages_stream):
            if stream is not None:
                stream.close()
        (run_dir / "metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        try:
            from loadtest.report import build_report
            print(build_report(run_dir), flush=True)
        except Exception as exc:
            (run_dir / "report_error.txt").write_text(f"{type(exc).__name__}: {exc}", encoding="utf-8")
            print(f"Report incomplete: {exc}", flush=True)
        nfr_status = "INCONCLUSIVE"
        try:
            from loadtest.nfr_report import build_nfr_report
            nfr = build_nfr_report(run_dir)
            nfr_status = nfr["status"]
            print((run_dir / "nfr_summary.txt").read_text(encoding="utf-8"), flush=True)
        except Exception as exc:
            (run_dir / "nfr_report_error.txt").write_text(f"{type(exc).__name__}: {exc}", encoding="utf-8")
            print(f"NFR report incomplete: {exc}", flush=True)
        for filename in ("prepare.log", "api.log", "locust.log", "pages.log", "concurrency.log"):
            path = run_dir / filename
            if path.exists() and (failure or filename == "api.log"):
                tail = "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-200:])
                (run_dir / filename.replace(".log", "_tail.txt")).write_text(tail, encoding="utf-8")
        archive = shutil.make_archive(str(run_dir), "zip", run_dir)
        print(f"RESULT_ZIP={archive}", flush=True)
    return 2 if failure or nfr_status == "INCONCLUSIVE" else (1 if nfr_status == "FAIL" else 0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--users", type=int, default=100)
    parser.add_argument("--spawn-rate", type=float, default=20)
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("--tickets", type=int, default=150)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--seed", type=int, default=1211)
    parser.add_argument("--phase", choices=("1", "2"), default="1")
    parser.add_argument("--warmup-seconds", type=int, default=180)
    parser.add_argument("--page-samples", type=int, default=3)
    parser.add_argument("--latency-ms", type=float, default=50)
    parser.add_argument("--download-mbps", type=float, default=10)
    parser.add_argument("--upload-mbps", type=float, default=5)
    parser.add_argument("--max-error-rate", type=float, default=0.01)
    parser.add_argument("--skip-contention", action="store_true", help="Smoke only: NFR04/05 INCONCLUSIVE")
    args = parser.parse_args()
    if (not 1 <= args.users <= 200 or args.tickets < 10 or args.spawn_rate <= 0 or args.seconds <= 0
            or args.warmup_seconds <= args.users / args.spawn_rate or args.page_samples < 1
            or args.latency_ms < 0 or args.download_mbps <= 0 or args.upload_mbps <= 0
            or not 0 <= args.max_error_rate <= 1):
        parser.error("users: 1..200; tickets >= 10; spawn-rate > 0; positive steady seconds; valid network/rate; warmup must exceed ramp-up")
    raise SystemExit(run(args))
