"""Measure actual SPA rendering in Chromium during the steady Locust load."""
import argparse
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
from playwright.sync_api import sync_playwright


def run_pages(host, run_dir, samples=3, latency_ms=50, download_mbps=10, upload_mbps=5):
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    marker = json.loads((run_dir / "steady-start.json").read_text(encoding="utf-8"))
    routes = [("/", "#/", "#concert-results .concert-card"),
              ("/concerts/{id}", f"#/concerts/{manifest['concert_id']}", "#book-button"),
              ("/login", "#/login", '#auth-form[data-mode="login"]'),
              ("/register", "#/register", '#auth-form[data-mode="register"]'),
              ("/my-orders", "#/my-orders", "#order-results")]
    result = {"schema_version": 1, "completed": False, "complete": False,
              "network": {"latency_ms": latency_ms, "download_mbps": download_mbps,
                          "upload_mbps": upload_mbps, "simulated": True},
              "required_routes": [row[0] for row in routes], "samples": [],
              "scope": "Cold browser context; navigation through visible DOM plus two animation frames. "
                       "Network model is an explicit lab assumption, not a measurement of real users' networks."}

    def save():
        (run_dir / "pages.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    def gauge():
        import csv
        with (run_dir / "concurrency.csv").open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        row = rows[-1] if rows else {}
        valid = row.get("phase") == "steady" and time.time() - float(row.get("timestamp", 0)) < 2
        return int(row.get("ready_users", 0)) if valid else 0

    try:
        with sqlite3.connect(f"{(run_dir / 'concert.db').resolve().as_uri()}?mode=ro", uri=True) as db:
            account = manifest["accounts"][0]
            user_id = db.execute("SELECT id FROM users WHERE email=?", (account["email"],)).fetchone()[0]
        now = datetime.now(timezone.utc)
        token = jwt.encode({"sub": str(user_id), "iat": now, "exp": now + timedelta(hours=1)},
                           os.environ["JWT_SECRET"], algorithm="HS256")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            result["browser_version"] = browser.version
            for iteration in range(samples):
                for route, fragment, selector in routes:
                    if time.time() >= marker["ends_at"]:
                        raise TimeoutError("Steady window ended before all browser samples completed")
                    context = browser.new_context(viewport={"width": 1366, "height": 768}, locale="vi-VN")
                    if route == "/my-orders":
                        session = json.dumps({"token": token, "email": account["email"]})
                        context.add_init_script("sessionStorage.setItem('concert.session', " + json.dumps(session) + ");")
                    page = context.new_page()
                    cdp = context.new_cdp_session(page)
                    cdp.send("Network.enable")
                    cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
                    cdp.send("Network.emulateNetworkConditions", {
                        "offline": False, "latency": latency_ms,
                        "downloadThroughput": download_mbps * 1_000_000 / 8,
                        "uploadThroughput": upload_mbps * 1_000_000 / 8})
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("requestfailed", lambda request: errors.append(f"{request.url}: {request.failure}"))
                    page.on("response", lambda response: errors.append(f"HTTP {response.status}: {response.url}")
                            if response.status >= 400 else None)
                    ready_before = gauge()
                    started_ts = time.time()
                    started = time.perf_counter()
                    sample_error = None
                    try:
                        page.goto(host + "/" + fragment, wait_until="domcontentloaded", timeout=15000)
                        page.locator(selector).wait_for(state="visible", timeout=15000)
                        page.wait_for_function("document.querySelector('#main .loading-state') === null", timeout=15000)
                        # The ready selector is rendered after dependent APIs finish. Include a paint opportunity.
                        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
                        if page.locator("#main .notice-info, #main .notice-error").count():
                            errors.append("Page rendered an error or incomplete-data notice")
                    except Exception as exc:
                        sample_error = f"{type(exc).__name__}: {exc}"
                        errors.append(sample_error)
                        try:
                            page.screenshot(path=str(run_dir / f"page-error-{iteration}-{len(result['samples'])}.png"))
                        except Exception:
                            pass
                    elapsed_ms = (time.perf_counter() - started) * 1000
                    ended_ts = time.time()
                    ready_after = gauge()
                    during = (marker["started_at"] <= started_ts <= ended_ts <= marker["ends_at"]
                              and min(ready_before, ready_after) >= marker["users"])
                    result["samples"].append({"route": route, "iteration": iteration + 1,
                        "elapsed_ms": round(elapsed_ms, 3), "success": not errors,
                        "error": sample_error, "errors": list(errors), "started_ts": started_ts,
                        "ended_ts": ended_ts, "timestamp": started_ts, "ready_users": min(ready_before, ready_after),
                        "phase": "steady" if during else "outside_steady", "during_steady": during})
                    save()
                    context.close()
            browser.close()
        result["completed"] = result["complete"] = True
        result["measured_during_steady"] = all(s["during_steady"] for s in result["samples"])
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    save()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--latency-ms", type=float, default=50)
    parser.add_argument("--download-mbps", type=float, default=10)
    parser.add_argument("--upload-mbps", type=float, default=5)
    args = parser.parse_args()
    result = run_pages(args.host, args.run_dir, args.samples, args.latency_ms, args.download_mbps, args.upload_mbps)
    raise SystemExit(0 if result["completed"] else 1)
