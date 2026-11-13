"""Ticket hunt v2: use python -m loadtest.run; one Locust process only."""
import csv
import json
import logging
import os
import random
import time

import gevent
from collections import Counter, deque
from pathlib import Path

from locust import HttpUser, between, events, task
from locust.runners import LocalRunner
from locust.stats import StatsCSV, PERCENTILES_TO_REPORT
from loadtest.policy import classify

MANIFEST = None
ACCOUNTS = deque()
COUNTERS = Counter()
ERRORS = []
STREAM = None
WRITER = None
PHASE = "warmup"
STARTED_TS = None
ENDED_TS = None
PEAK_READY = 0
STEADY_COMPLETED = False
LIVE_READY = 0
MONITOR = None


def monitor(environment):
    global PHASE, STARTED_TS, ENDED_TS, PEAK_READY, STEADY_COMPLETED
    run_dir = Path(os.environ["HUNT_RUN_DIR"])
    duration = float(os.environ.get("NFR_STEADY_SECONDS", "120"))
    warmup_deadline = time.monotonic() + float(os.environ.get("NFR_WARMUP_SECONDS", "180"))
    steady_clock = None
    with (run_dir / "concurrency.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["timestamp", "elapsed_s", "phase", "users", "ready_users"])
        origin = time.monotonic()
        while True:
            now = time.time()
            PEAK_READY = max(PEAK_READY, LIVE_READY)
            if STARTED_TS is None and LIVE_READY == len(MANIFEST["accounts"]):
                STARTED_TS = now
                steady_clock = time.monotonic()
                PHASE = "steady"
                (run_dir / "steady-start.json").write_text(json.dumps({
                    "started_at": now, "ends_at": now + duration,
                    "users": len(MANIFEST["accounts"])}), encoding="utf-8")
            writer.writerow([now, round(time.monotonic() - origin, 3), PHASE,
                             environment.runner.user_count, LIVE_READY])
            stream.flush()
            if STARTED_TS is not None and time.monotonic() - steady_clock >= duration:
                ENDED_TS = now
                STEADY_COMPLETED = True
                PHASE = "drain"
                gevent.spawn(environment.runner.quit)
                return
            if STARTED_TS is None and (time.monotonic() >= warmup_deadline
                    or (COUNTERS["started"] >= len(MANIFEST["accounts"]) and COUNTERS["init_failed"])):
                ERRORS.append("Did not reach the required concurrently ready users during warmup")
                environment.process_exit_code = 1
                PHASE = "drain"
                gevent.spawn(environment.runner.quit)
                return
            gevent.sleep(0.5)


@events.test_start.add_listener
def start(environment, **kwargs):
    global MANIFEST, STREAM, WRITER, PHASE, STARTED_TS, ENDED_TS, PEAK_READY, STEADY_COMPLETED, LIVE_READY, MONITOR
    if not isinstance(environment.runner, LocalRunner):
        raise RuntimeError("Use one local Locust process, no distributed workers.")
    MANIFEST = json.loads(Path(os.environ["HUNT_MANIFEST"]).read_text(encoding="utf-8"))
    ACCOUNTS.clear()
    ACCOUNTS.extend(MANIFEST["accounts"])
    COUNTERS.clear()
    ERRORS.clear()
    PHASE, STARTED_TS, ENDED_TS = "warmup", None, None
    PEAK_READY, LIVE_READY, STEADY_COMPLETED = 0, 0, False
    STREAM = (Path(os.environ["HUNT_RUN_DIR"]) / "requests.csv").open("w", newline="", encoding="utf-8")
    WRITER = csv.writer(STREAM)
    WRITER.writerow(["timestamp", "user", "method", "endpoint", "latency_ms", "http_status", "outcome", "failed", "phase"])
    MONITOR = gevent.spawn(monitor, environment)


@events.request.add_listener
def record(request_type, name, response_time, response=None, context=None, exception=None, **kwargs):
    context = context or {}
    outcome = context.get("outcome", "unclassified")
    status = getattr(response, "status_code", 0)
    COUNTERS["requests"] += 1
    COUNTERS["request_failures"] += int(exception is not None)
    COUNTERS["http_4xx_5xx"] += int(status >= 400)
    COUNTERS["transport_errors"] += int(not status)
    COUNTERS[f"{context.get('operation', 'unknown')}.{outcome}"] += 1
    if WRITER is not None:
        WRITER.writerow([time.time(), context.get("user"), request_type, name,
                         round(response_time, 3), status, outcome, int(exception is not None), context.get("phase", PHASE)])
        STREAM.flush()


@events.user_error.add_listener
def user_error(user_instance, exception, tb, **kwargs):
    COUNTERS["script_errors"] += 1
    ERRORS.append(f"{type(exception).__name__}: {exception}")


@events.test_stop.add_listener
def finish(environment, **kwargs):
    if STREAM is not None:
        STREAM.flush()
        STREAM.close()
    (Path(os.environ["HUNT_RUN_DIR"]) / "scenario.json").write_text(
        json.dumps({"counters": dict(COUNTERS), "errors": ERRORS,
                    "steady_started_ts": STARTED_TS, "steady_ended_ts": ENDED_TS,
                    "observed_steady_seconds": (ENDED_TS - STARTED_TS) if STARTED_TS and ENDED_TS else 0,
                    "peak_ready_users": PEAK_READY, "steady_completed": STEADY_COMPLETED}, indent=2), encoding="utf-8")
    # Periodic --csv output can lag behind the last requests. Export a final snapshot
    # to separate files so the background CSV writer cannot overwrite it.
    exporter = StatsCSV(environment, PERCENTILES_TO_REPORT)
    for suffix, export in (("stats", exporter.requests_csv), ("failures", exporter.failures_csv),
                           ("exceptions", exporter.exceptions_csv)):
        with (Path(os.environ["HUNT_RUN_DIR"]) / f"final_{suffix}.csv").open("w", newline="", encoding="utf-8") as stream:
            export(csv.writer(stream))
    if COUNTERS["init_failed"] or COUNTERS["script_errors"] or COUNTERS["ready"] != len(MANIFEST["accounts"]):
        environment.process_exit_code = 1


class TicketBuyer(HttpUser):
    wait_time = between(0.5, 2)

    def on_start(self):
        global LIVE_READY
        self.ready = False
        self.email = "unassigned"
        self.active_order = None
        self.reconcile = False
        self.uncertain_purchase = False
        self.cancelled_once = False
        self.cancel_at = float("inf")
        self.ticket_types = []
        COUNTERS["started"] += 1
        try:
            if not ACCOUNTS:
                raise RuntimeError("No unused account remains")
            account = ACCOUNTS.popleft()
            self.email = account["email"]
            self.rng = random.Random(MANIFEST["random_seed"] + account["number"])
            self.will_cancel = account["number"] % 10 == 0
            body, outcome = self.call("login", "POST", "/auth/login",
                                      json={"email": self.email, "password": self.email})
            if outcome != "ok":
                raise RuntimeError(f"Login failed: {outcome}")
            self.client.headers.update({"Authorization": f"Bearer {body['access_token']}"})
            COUNTERS["logged_in"] += 1
            concerts, outcome = self.call("concerts", "GET", "/concerts")
            if outcome != "ok" or not any(c["id"] == MANIFEST["concert_id"] for c in concerts):
                raise RuntimeError("Target concert unavailable")
            if not self.refresh_tickets():
                raise RuntimeError("Ticket list unavailable")
            self.ready = True
            COUNTERS["ready"] += 1
            LIVE_READY += 1
        except Exception as exc:
            COUNTERS["init_failed"] += 1
            ERRORS.append(f"{self.email}: {type(exc).__name__}: {exc}")
            logging.error("User initialization failed: %s (%s)", self.email, exc)
            # Remain idle: don't silently replace failed users or reuse accounts.

    def on_stop(self):
        global LIVE_READY
        if self.ready:
            LIVE_READY -= 1
            self.ready = False

    def browse_concert(self):
        choice = self.rng.randrange(3)
        if choice == 0:
            self.call("concerts", "GET", "/concerts")
        elif choice == 1:
            self.call("concerts", "GET", "/concerts?on_sale=true")
        else:
            self.call("detail", "GET", f"/concerts/{MANIFEST['concert_id']}")

    def call(self, operation, method, path, **kwargs):
        name = {"tickets": "/concerts/{id}/ticket-types", "cancel": "/orders/{id}", "detail": "/concerts/{id}"}.get(operation, path)
        context = {"operation": operation, "user": self.email, "phase": PHASE}
        with self.client.request(method, path, name=name, timeout=(3, 10), catch_response=True,
                                 context=context, **kwargs) as response:
            try:
                body = response.json()
            except ValueError:
                body = None
            outcome, accepted = classify(operation, response.status_code, body)
            response.request_meta["context"]["outcome"] = outcome
            if accepted:
                response.success()
            else:
                response.failure(f"{operation}: {outcome}")
        return body, outcome

    def refresh_tickets(self):
        body, outcome = self.call("tickets", "GET", f"/concerts/{MANIFEST['concert_id']}/ticket-types")
        if outcome == "ok":
            self.ticket_types = body
        return outcome == "ok" and bool(body)

    def read_orders(self):
        body, outcome = self.call("orders", "GET", "/orders/me")
        if outcome != "ok":
            return False
        active = [row for row in body if row["status"] == "confirmed"]
        if len(active) > 1:
            raise RuntimeError(f"More than one active order for {self.email}")
        self.active_order = active[0] if active else None
        if any(row["status"] == "cancelled" for row in body):
            self.cancelled_once = True
        # An empty read may race with the earlier POST still running on the API.
        # Without an idempotency key, stop buying until that order becomes visible.
        self.reconcile = self.uncertain_purchase and not active
        if active:
            self.uncertain_purchase = False
        return True

    @task
    def hunt(self):
        if not self.ready:
            return
        if self.reconcile:
            self.read_orders()
            return
        if self.active_order:
            if self.will_cancel and not self.cancelled_once and time.monotonic() >= self.cancel_at:
                self.cancelled_once = True
                self.call("cancel", "DELETE", f"/orders/{self.active_order['id']}")
                self.reconcile = True
            elif self.rng.random() < 0.7:
                self.read_orders()
            else:
                self.browse_concert()
            return
        choice = self.rng.random()
        if choice < 0.2:
            self.browse_concert()
        elif choice < 0.4:
            self.refresh_tickets()
        else:
            available = [t for t in self.ticket_types if t["remaining"] > 0]
            if not available:
                self.refresh_tickets()
                return
            ticket = self.rng.choice(available)
            quantity = self.rng.randint(1, min(2, ticket["remaining"]))
            body, outcome = self.call("buy", "POST", "/orders",
                                      json={"ticket_type_id": ticket["id"], "quantity": quantity})
            if outcome == "ok":
                self.active_order = body
                self.cancel_at = time.monotonic() + self.rng.uniform(5, 15)
                COUNTERS["tickets_acknowledged"] += quantity
            elif outcome == "sold_out":
                self.refresh_tickets()
            else:
                # A failed response can follow a committed write; don't retry blindly.
                self.cancel_at = time.monotonic() + self.rng.uniform(5, 15)
                self.uncertain_purchase = True
                self.reconcile = True
