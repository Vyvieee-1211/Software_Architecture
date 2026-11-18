"""Kiểm tra harness qua HTTP thật trên DB SQLite tạm; không chứng minh chịu tải PostgreSQL."""
import csv
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest
import requests

from loadtest.prepare import prepare
from loadtest.run import ROOT, report


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    folder = tmp_path_factory.mktemp("locust_api")
    url = "sqlite:///" + (folder / "test.db").as_posix()
    fixture = prepare(url, 100, users=4, accounts_path=folder / "accounts.json")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    host = f"http://127.0.0.1:{port}"
    env = dict(os.environ, DATABASE_URL=url, PAYMENT_PROVIDER="mock", ORDER_WORKERS="1",
               HOLD_MINUTES="0.05", HOLD_SWEEP_INTERVAL_SECONDS="0.2", SWEEP_WAIT_SECONDS="0.5", PUBLIC_BASE_URL=host,
               PAYMENT_WEBHOOK_SECRET="loadtest-integration-secret", SETUP_TIMEOUT_SECONDS="30", **fixture)
    env.pop("LOADTEST_DATABASE_URL", None)
    with (folder / "api.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--workers", "1", "--port", str(port)],
                                   cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                if process.poll() is not None:
                    pytest.fail((folder / "api.log").read_text(encoding="utf-8"))
                try:
                    if requests.get(host + "/concerts", timeout=1).status_code == 200:
                        break
                except requests.RequestException:
                    pass
                time.sleep(.1)
            else:
                pytest.fail("API không sẵn sàng")
            yield host, env
        finally:
            process.terminate()
            process.wait(timeout=15)


@pytest.mark.parametrize("scenario,workers", [("S0", 1), ("S1", 2), ("S2", 1), ("S4", 1), ("S5", 1), ("S6", 1), ("S7", 1)])
def test_scenario_http(api, tmp_path, scenario, workers):
    host, env = api
    process = subprocess.run([sys.executable, "-m", "loadtest.run", scenario, "--host", host,
                              "--users", "2" if workers == 2 else "1", "--rate", "1" if scenario == "S1" else "2",
                              "--seconds", "12" if scenario == "S6" else "6",
                              "--locust-workers", str(workers), "--output", str(tmp_path)],
                             cwd=ROOT, env=env, capture_output=True, text=True, timeout=65)
    paths = list(tmp_path.glob("*/nfr_results.json"))
    logs = "\n".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("*/*.log"))
    assert paths, process.stdout + process.stderr + logs
    result = json.loads(paths[0].read_text(encoding="utf-8"))
    assert process.returncode == 0, result | {"logs": logs}
    assert result["http_requests"] > 0
    assert result["http_failures"] == 0
    assert result["counters"].get("script_error", 0) == 0
    if scenario in {"S1", "S2"}:
        with_setup = []
        for path in paths[0].parent.glob("requests_*.csv"):
            with path.open(encoding="utf-8", newline="") as handle:
                with_setup.extend(row for row in csv.DictReader(handle) if row["phase"] == "setup")
        if scenario == "S1":
            assert any(row["method"] == "GET" and row["status"] == "200" for row in with_setup)
        else:
            assert all(row["name"] != "/orders [POST flash]" for row in with_setup)
    if scenario in {"S0", "S4", "S5", "S6", "S7"}:
        assert result["counters"].get("verify_ok", 0) > 0
    if scenario != "S1":
        assert result["status"] == "INCONCLUSIVE"  # SQLite không thay thế kiểm tra kho PostgreSQL.
    if scenario == "S4":
        assert result["counters"]["expired"] == 1
    if scenario == "S6":
        assert result["counters"]["expiry_race_cases"] == 1


def test_empty_measurement_is_inconclusive(tmp_path):
    assert report(tmp_path, "S0", [0], None)["status"] == "INCONCLUSIVE"


def test_verification_failure_cannot_pass(tmp_path):
    (tmp_path / "summary_local.json").write_text(json.dumps({
        "steady_started": 1, "counters": {"verify_fail": 1}}), encoding="utf-8")
    assert report(tmp_path, "S7", [0], None)["status"] == "FAIL"


def test_standard_suite_has_three_identical_repeats():
    from loadtest.suite import jobs
    from collections import Counter
    plan = list(jobs())
    assert len(plan) == 48
    counts = Counter(name.rsplit("_r", 1)[0] for name, _ in plan)
    assert all(count == 3 for count in counts.values())
    assert all(args[:2] == ["--db-pool-size", "20"] for name, args in plan if not name.startswith("S9_"))
    assert {name.rsplit("_r", 1)[0] for name, _ in plan if name.startswith("S9_")} == {
        f"S9_{workload}_p{pool}" for workload in ("flash", "funnel") for pool in (5, 10, 20)
    }
