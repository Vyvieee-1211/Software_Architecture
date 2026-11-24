"""Đồng bộ user sẵn sàng, tách pha đo và lưu mẫu trên từng tiến trình Locust."""
import csv
import json
import os
import time
from pathlib import Path

import gevent
from locust.runners import MasterRunner, WorkerRunner

READY = 0
STARTED = None
REPORTS = {}


def timed_request(request):
    def send(method, url, **kwargs):
        kwargs.setdefault("timeout", float(os.getenv("HTTP_TIMEOUT_SECONDS", "20")))
        return request(method, url, **kwargs)
    return send


def ready(environment):
    global READY
    READY += 1
    if isinstance(environment.runner, WorkerRunner):
        environment.runner.send_message("loadtest_ready", READY)
    return READY


def install(events, counters, barrier):
    output = Path(os.getenv("LOADTEST_OUTPUT", "loadtest/results/manual"))
    handles = []
    node = "local"

    @events.init.add_listener
    def init(environment, **kwargs):
        nonlocal node
        runner = environment.runner
        node = runner.client_id if isinstance(runner, WorkerRunner) else "master" if isinstance(runner, MasterRunner) else "local"
        if isinstance(runner, MasterRunner):
            def receive(environment, msg, **kwargs):
                REPORTS[msg.node_id] = msg.data
            runner.register_message("loadtest_ready", receive)
        elif isinstance(runner, WorkerRunner):
            runner.register_message("loadtest_release", lambda environment, msg, **kwargs: release(environment, msg.data))

    def release(environment, timestamp):
        global STARTED
        STARTED = timestamp
        if not isinstance(environment.runner, WorkerRunner):
            (output / "steady_start.json").write_text(json.dumps({"timestamp": timestamp}), encoding="utf-8")
        environment.runner.stats.reset_all()
        barrier.set()

    @events.test_start.add_listener
    def start(environment, **kwargs):
        global READY, STARTED
        READY, STARTED = 0, None
        REPORTS.clear()
        counters.clear()
        barrier.clear()
        output.mkdir(parents=True, exist_ok=True)
        request_file = (output / f"requests_{node}.csv").open("w", encoding="utf-8", newline="")
        concurrency_file = (output / f"concurrency_{node}.csv").open("w", encoding="utf-8", newline="")
        handles.extend([request_file, concurrency_file])
        environment.loadtest_writer = csv.writer(request_file)
        environment.loadtest_writer.writerow(["timestamp", "phase", "method", "name", "ms", "status", "error", "retry_after"])
        concurrency = csv.writer(concurrency_file)
        concurrency.writerow(["timestamp", "phase", "ready", "users"])
        if isinstance(environment.runner, WorkerRunner):
            return

        def coordinate():
            target = environment.parsed_options.num_users or environment.runner.target_user_count
            timeout = time.monotonic() + float(os.getenv("SETUP_TIMEOUT_SECONDS", "600"))
            duration = float(os.getenv("STEADY_SECONDS", "60"))
            while STARTED is None:
                total = sum(REPORTS.values()) if isinstance(environment.runner, MasterRunner) else READY
                concurrency.writerow([time.time(), "setup", total, environment.runner.user_count])
                concurrency_file.flush()
                if total >= target:
                    timestamp = time.time()
                    if isinstance(environment.runner, MasterRunner):
                        environment.runner.send_message("loadtest_release", timestamp)
                    release(environment, timestamp)
                elif time.monotonic() > timeout:
                    counters["setup_timeout"] += 1
                    environment.process_exit_code = 1
                    environment.runner.quit()
                    return
                gevent.sleep(0.5)
            while time.time() - STARTED < duration:
                concurrency.writerow([time.time(), "steady", target, environment.runner.user_count])
                concurrency_file.flush()
                gevent.sleep(0.5)
            environment.runner.quit()
        environment.runner.greenlet.spawn(coordinate)

    @events.request.add_listener
    def request(request_type, name, response_time, exception, response=None, start_time=None, **kwargs):
        # Listener dùng writer đã mở trên chính node hiện tại.
        if handles and not handles[0].closed:
            writer = csv.writer(handles[0])
            stamp = time.time()
            if STARTED is None or (start_time is not None and start_time < STARTED):
                phase = "setup"
            elif stamp >= STARTED + float(os.getenv("STEADY_SECONDS", "60")):
                phase = "drain"
            else:
                phase = "steady"
            writer.writerow([stamp, phase, request_type,
                             name, response_time, getattr(response, "status_code", ""), str(exception or ""),
                             response.headers.get("Retry-After", "") if response is not None else ""])

    @events.user_error.add_listener
    def user_error(user_instance, exception, tb, **kwargs):
        counters["script_error"] += 1
        user_instance.environment.process_exit_code = 1

    @events.quitting.add_listener
    def quit(environment, **kwargs):
        for handle in handles:
            handle.close()
        result = {"node": node, "ready": READY, "steady_started": STARTED, "counters": dict(counters)}
        (output / f"summary_{node}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        if any(counters.get(key) for key in ("verify_fail", "script_error", "setup_failed", "setup_timeout", "request_timeout")):
            environment.process_exit_code = 1
