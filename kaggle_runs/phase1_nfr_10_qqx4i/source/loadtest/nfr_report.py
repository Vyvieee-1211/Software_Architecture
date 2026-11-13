"""Evaluate measured NFR evidence; missing evidence never becomes a PASS.

This module only reads the workload/browser/concurrency artefacts. Dedicated
concurrency fixtures are assessed from concurrency.json, separately from the
ticket-hunt inventory used by the original baseline report.
"""

import csv
import json
import math
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


PASS, FAIL, INCONCLUSIVE = "PASS", "FAIL", "INCONCLUSIVE"
DEFAULT_ENDPOINTS = ["/concerts", "/concerts?on_sale=true", "/concerts/{id}"]
DEFAULT_ROUTES = ["/", "/concerts/{id}", "/login", "/register", "/my-orders"]
DESCRIPTIONS = {
    "NFR01": "Các trang thông thường phản hồi tối đa 3 giây.",
    "NFR02": "API xem concert phản hồi trung bình dưới 2 giây.",
    "NFR03": "Hỗ trợ tối thiểu 100 người dùng đồng thời.",
    "NFR04": "Đặt vé không bán vượt số lượng còn lại.",
    "NFR05": "Cập nhật tồn kho có cơ chế xử lý đồng thời phù hợp.",
}


def _number(value, default=None):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _true(value):
    return value is True or str(value).lower() in ("1", "true", "yes")


def _invalid_json_constant(value):
    raise ValueError(f"Non-finite JSON constant: {value}")


def _load_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=_invalid_json_constant)
        if not isinstance(value, dict):
            return {}, f"{path.name}: nội dung phải là JSON object."
        return value, None
    except (OSError, ValueError) as error:
        return {}, f"Không đọc được {path.name}: {type(error).__name__}: {error}"


def _load_csv(path):
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream)), None
    except (OSError, ValueError, csv.Error) as error:
        return [], f"Không đọc được {path.name}: {type(error).__name__}: {error}"


def _normalize_endpoint(value):
    """Keep on_sale as a separate view; coalesce actual concert IDs."""
    parsed = urlsplit(str(value or ""))
    path = parsed.path.rstrip("/") or "/"
    path = re.sub(r"(?<=/concerts/)(?:\d+|\{concert_id\}|\{id\})(?=/|$)", "{id}", path)
    query = parse_qs(parsed.query)
    if path == "/concerts" and query.get("on_sale", [""])[0].lower() in ("true", "1"):
        return "/concerts?on_sale=true"
    return path


def _normalize_route(value):
    value = str(value or "")
    if "#" in value:
        value = value.split("#", 1)[1]
    return _normalize_endpoint(value)


def _unexpected_failure(row):
    status = _number(row.get("http_status"))
    if _true(row.get("failed")):
        return True
    # Only a validated SoldOut business response is accepted by this scenario.
    if status == 409 and row.get("outcome") == "sold_out":
        return False
    return status is not None and (status <= 0 or status >= 400)


def _request_evidence_complete(row):
    status = _number(row.get("http_status"))
    return (status is not None and status.is_integer() and 0 <= status < 600
            and str(row.get("failed", "")).lower() in ("0", "1", "true", "false", "yes", "no"))


def _result(nfr, status, reasons, observed, target):
    return {"nfr": nfr, "description": DESCRIPTIONS[nfr], "status": status,
            "reasons": list(dict.fromkeys(reasons)), "observed": observed, "target": target}


def _monitor_evidence(meta, scenario, rows):
    """A sampled, continuously covered steady interval, not just peak users."""
    required_users = max(100, int(_number(meta.get("minimum_users"), 100)))
    configured_users = int(_number(meta.get("users"), required_users))
    minimum_seconds = max(120.0, _number(meta.get("min_steady_seconds"), 120.0))
    max_gap = max(0.01, _number(meta.get("max_monitor_gap_seconds"), 2.5))
    started = _number(scenario.get("steady_started_ts"))
    ended = _number(scenario.get("steady_ended_ts"))
    reported_seconds = _number(scenario.get("observed_steady_seconds"))
    missing, failures = [], []
    samples = []
    for row in rows:
        if row.get("phase") != "steady":
            continue
        stamp = _number(row.get("timestamp"))
        users = _number(row.get("users"))
        ready = _number(row.get("ready_users"))
        if stamp is None or users is None or ready is None or ready < 0 or users < ready:
            missing.append("Có mẫu concurrency.csv không hợp lệ.")
            continue
        samples.append({"timestamp": stamp, "users": users, "ready_users": ready})
    samples.sort(key=lambda sample: sample["timestamp"])
    if configured_users < required_users:
        missing.append(f"Chỉ cấu hình {configured_users} user; lượt smoke không đánh giá NFR03.")
    if started is None or ended is None or ended <= started:
        missing.append("Thiếu khoảng thời gian steady hợp lệ trong scenario.json.")
        duration = None
    else:
        duration = min(ended - started, reported_seconds) if reported_seconds is not None else ended - started
        samples = [sample for sample in samples if started <= sample["timestamp"] <= ended]
        if duration < minimum_seconds:
            missing.append(f"Chỉ đo steady {duration:.3f}s; cần ít nhất {minimum_seconds:g}s.")
    if not samples:
        missing.append("Không có mẫu số user đang hoạt động trong steady.")
    elif started is not None and ended is not None and ended > started:
        gaps = ([samples[0]["timestamp"] - started, ended - samples[-1]["timestamp"]]
                + [right["timestamp"] - left["timestamp"] for left, right in zip(samples, samples[1:])])
        if max(gaps) > max_gap:
            missing.append(f"Nhật ký số user có khoảng trống {max(gaps):.3f}s (> {max_gap:g}s).")
        minimum_ready = min(sample["ready_users"] for sample in samples)
        if minimum_ready < required_users and configured_users >= required_users:
            failures.append(f"Số user sẵn sàng thấp nhất trong steady là {minimum_ready:g}, dưới {required_users}.")
    observed = {"configured_users": configured_users, "required_users": required_users,
                "steady_started_ts": started, "steady_ended_ts": ended,
                "observed_steady_seconds": duration,
                "monitor_samples": len(samples),
                "minimum_ready_users": min((s["ready_users"] for s in samples), default=None),
                "peak_ready_users": max((s["ready_users"] for s in samples), default=None),
                "max_allowed_monitor_gap_seconds": max_gap}
    return observed, samples, missing, failures


def _sample_under_load(sample, monitor, samples):
    started = _number(sample.get("started_ts", sample.get("timestamp")))
    ended = _number(sample.get("ended_ts", sample.get("timestamp")))
    window_start, window_end = monitor["steady_started_ts"], monitor["steady_ended_ts"]
    if (started is None or ended is None or ended < started or window_start is None
            or window_end is None or started < window_start or ended > window_end
            or not _true(sample.get("during_steady"))):
        return False
    before = [row for row in samples if row["timestamp"] <= started]
    after = [row for row in samples if row["timestamp"] >= ended]
    if not before or not after:
        return False
    left, right = before[-1], after[0]
    gap = monitor["max_allowed_monitor_gap_seconds"]
    if started - left["timestamp"] > gap or right["timestamp"] - ended > gap:
        return False
    relevant = [row for row in samples if left["timestamp"] <= row["timestamp"] <= right["timestamp"]]
    return (all(row["ready_users"] >= monitor["required_users"] for row in relevant)
            and all(b["timestamp"] - a["timestamp"] <= gap for a, b in zip(relevant, relevant[1:])))


def _pages_result(meta, pages, monitor, monitor_samples, artifact_error):
    limit = min(3000.0, _number(meta.get("page_limit_ms"), 3000.0))
    required_count = max(1, int(_number(meta.get("page_samples_per_route"), 3)))
    routes = [_normalize_route(route) for route in pages.get("required_routes", DEFAULT_ROUTES)]
    # The caller may add routes, but cannot omit the five application views.
    routes = list(dict.fromkeys(DEFAULT_ROUTES + routes))
    grouped = {route: [] for route in routes}
    missing, failures = [], []
    if artifact_error:
        missing.append(artifact_error)
    if not _true(pages.get("completed")):
        missing.append("Phép đo trình duyệt chưa hoàn tất.")
    network = pages.get("network", {})
    if not isinstance(network, dict) or not _true(network.get("simulated")):
        missing.append("Thiếu mô hình mạng mô phỏng và thông số mạng bình thường.")
    else:
        for key in ("latency_ms", "download_mbps", "upload_mbps"):
            number = _number(network.get(key))
            if number is None or number < 0 or (key != "latency_ms" and number == 0):
                missing.append(f"Thông số mạng {key} không hợp lệ.")
    sample_rows = pages.get("samples", [])
    if not isinstance(sample_rows, list):
        missing.append("pages.json.samples phải là danh sách.")
        sample_rows = []
    for sample in sample_rows:
        if not isinstance(sample, dict):
            missing.append("Có mẫu đo trang không hợp lệ.")
            continue
        route = _normalize_route(sample.get("route"))
        if route in grouped:
            grouped[route].append(sample)
    observed = {}
    for route, route_samples in grouped.items():
        latencies, valid_load, success_count = [], 0, 0
        for sample in route_samples:
            success = sample.get("success")
            elapsed = _number(sample.get("elapsed_ms"))
            if success is None:
                missing.append(f"{route}: thiếu trạng thái đo trang.")
            elif not _true(success) or sample.get("error") or sample.get("errors"):
                failures.append(f"{route}: có lỗi tải trang/API hoặc render chưa thành công.")
            else:
                success_count += 1
            if elapsed is None or elapsed < 0:
                missing.append(f"{route}: thiếu thời gian render hợp lệ.")
            else:
                latencies.append(elapsed)
                if elapsed > limit:
                    failures.append(f"{route}: có mẫu {elapsed:.3f}ms, vượt {limit:g}ms.")
            if _sample_under_load(sample, monitor, monitor_samples):
                valid_load += 1
        observed[route] = {"samples": len(route_samples), "successful_samples": success_count,
                           "samples_during_required_load": valid_load,
                           "max_ms": max(latencies, default=None)}
        if len(route_samples) < required_count:
            missing.append(f"{route}: có {len(route_samples)} mẫu, cần {required_count}.")
        if valid_load < len(route_samples) or not route_samples:
            missing.append(f"{route}: chưa xác minh toàn bộ mẫu trong steady với >=100 user sẵn sàng.")
    status = FAIL if failures else INCONCLUSIVE if missing else PASS
    reasons = failures + missing if status != PASS else ["Mọi mẫu của cả 5 trang render thành công trong giới hạn khi đủ tải steady."]
    return _result("NFR01", status, reasons, {"routes": observed, "network": network,
                    "scope": "Browser end-to-end render (including required API responses), simulated normal network, local laboratory."},
                   {"max_page_ms": limit, "samples_per_route": required_count,
                    "minimum_ready_users": monitor["required_users"], "required_routes": routes})


def _concerts_result(meta, rows, artifact_error):
    limit = min(2000.0, _number(meta.get("concert_mean_limit_ms"), 2000.0))
    endpoints = list(dict.fromkeys(_normalize_endpoint(ep) for ep in meta.get("concert_endpoints", DEFAULT_ENDPOINTS)))
    endpoints = list(dict.fromkeys(DEFAULT_ENDPOINTS + endpoints))
    groups = {endpoint: [] for endpoint in endpoints}
    missing, failures, values = [], [], []
    if artifact_error:
        missing.append(artifact_error)
    for row in rows:
        endpoint = _normalize_endpoint(row.get("endpoint"))
        if row.get("phase") == "steady" and row.get("method", "").upper() == "GET" and endpoint in groups:
            groups[endpoint].append(row)
    observed = {}
    for endpoint, requests in groups.items():
        latencies = [_number(row.get("latency_ms")) for row in requests]
        valid = [value for value in latencies if value is not None and value >= 0]
        bad_count = sum(_unexpected_failure(row) or (_number(row.get("http_status")) is not None and _number(row.get("http_status")) != 200) for row in requests)
        if not requests:
            missing.append(f"GET {endpoint}: không có request trong steady.")
        if len(valid) != len(latencies):
            missing.append(f"GET {endpoint}: có độ trễ không hợp lệ.")
        if any(not _request_evidence_complete(row) for row in requests):
            missing.append(f"GET {endpoint}: thiếu HTTP status/trạng thái lỗi.")
        mean = sum(valid) / len(valid) if valid else None
        if bad_count:
            failures.append(f"GET {endpoint}: có {bad_count} request lỗi; không loại chúng để làm đẹp latency.")
        if mean is not None and mean >= limit:
            failures.append(f"GET {endpoint}: trung bình {mean:.3f}ms, yêu cầu < {limit:g}ms.")
        observed[endpoint] = {"requests": len(requests), "failures": bad_count, "mean_ms": mean}
        values.extend(valid)
    overall_mean = sum(values) / len(values) if values else None
    if overall_mean is not None and overall_mean >= limit:
        failures.append(f"Trung bình tất cả API xem concert {overall_mean:.3f}ms, yêu cầu < {limit:g}ms.")
    status = FAIL if failures else INCONCLUSIVE if missing else PASS
    reasons = failures + missing if status != PASS else ["Tất cả endpoint xem concert trong steady có trung bình < giới hạn và không có request lỗi."]
    return _result("NFR02", status, reasons, {"endpoints": observed, "overall_mean_ms": overall_mean,
                   "requests": sum(len(group) for group in groups.values()), "phase": "steady"},
                   {"mean_ms_exclusive": limit, "allowed_failures": 0, "required_endpoints": endpoints})


def _capacity_result(meta, scenario, rows, monitor, monitor_missing, monitor_failures, artifact_errors):
    missing, failures = list(monitor_missing), list(monitor_failures)
    missing.extend(error for error in artifact_errors if error)
    counters = scenario.get("counters", {})
    if not isinstance(counters, dict):
        counters = {}
        missing.append("scenario.json.counters không hợp lệ.")
    configured = monitor["configured_users"]
    for name in ("init_failed", "script_errors"):
        count = _number(counters.get(name, 0))
        if count is None:
            missing.append(f"Bộ đếm {name} không hợp lệ.")
        elif count:
            failures.append(f"Có {count:g} {name}; số user đỉnh không đủ để kết luận hỗ trợ tải.")
    if scenario.get("errors"):
        failures.append("Kịch bản có lỗi; xem scenario.json.errors.")
    ready = _number(counters.get("ready"))
    if ready is None:
        missing.append("Thiếu số user hoàn tất khởi tạo.")
    elif ready != configured:
        failures.append(f"Chỉ {ready:g}/{configured} user hoàn tất khởi tạo.")
    completed = meta.get("run_completed", meta.get("completed", scenario.get("completed", scenario.get("steady_completed"))))
    if completed is None:
        missing.append("Thiếu xác nhận lượt chạy hoàn tất bình thường.")
    elif not _true(completed):
        missing.append("Lượt chạy chưa hoàn tất bình thường.")
    exit_code = _number(meta.get("locust_exit_code"))
    if exit_code is None:
        missing.append("Thiếu exit code của Locust.")
    elif exit_code not in (0, 1):
        failures.append(f"Locust exit code {exit_code:g}; lượt tải bị lỗi tiến trình.")
    if meta.get("api_shutdown_clean") is not None and not _true(meta.get("api_shutdown_clean")):
        missing.append("API chưa được xác nhận dừng sạch.")
    steady = [row for row in rows if row.get("phase") == "steady"]
    distinct_users = {str(row.get("user")).strip() for row in steady
                      if row.get("user") is not None and str(row.get("user")).strip()}
    if configured >= monitor["required_users"] and len(distinct_users) < monitor["required_users"]:
        missing.append(f"Chỉ có request steady từ {len(distinct_users)} user khác nhau; cần ít nhất {monitor['required_users']}.")
    error_limit = _number(meta.get("max_error_rate"), 0.01)
    if error_limit < 0 or error_limit > 1:
        missing.append("max_error_rate phải nằm trong [0,1].")
        error_limit = 0.01
    bad = sum(_unexpected_failure(row) for row in steady)
    rate = bad / len(steady) if steady else None
    if not steady:
        missing.append("Không có request steady để đánh giá khả năng phục vụ.")
    if any(not _request_evidence_complete(row) for row in steady):
        missing.append("Request steady thiếu HTTP status/trạng thái lỗi.")
    if rate is not None and rate > error_limit:
        failures.append(f"Tỷ lệ lỗi bất thường {rate:.4%}, vượt ngưỡng giả định {error_limit:.4%}.")
    recorded_count = _number(counters.get("requests"))
    if recorded_count is None:
        missing.append("Thiếu bộ đếm request của kịch bản.")
    elif recorded_count != len(rows):
        missing.append(f"Nhật ký request ({len(rows)}) không khớp bộ đếm ({recorded_count:g}).")
    if "request_failures" in counters:
        counted_failures = _number(counters.get("request_failures"))
        csv_failures = sum(_true(row.get("failed")) for row in rows)
        if counted_failures is None or counted_failures != csv_failures:
            missing.append("Bộ đếm lỗi request không khớp nhật ký từng request.")
    status = FAIL if failures else INCONCLUSIVE if missing else PASS
    observed = dict(monitor)
    observed.update({"steady_requests": len(steady), "distinct_steady_users": len(distinct_users), "unexpected_failures": bad,
                     "unexpected_error_rate": rate, "ready_users_total": ready,
                     "locust_exit_code": exit_code, "run_completed": completed})
    reasons = failures + missing if status != PASS else ["Duy trì đủ user sẵn sàng toàn bộ steady, đủ thời gian, tỷ lệ lỗi trong ngưỡng và lượt chạy hoàn tất sạch."]
    return _result("NFR03", status, reasons, observed,
                   {"minimum_ready_users": monitor["required_users"],
                    "minimum_steady_seconds": max(120.0, _number(meta.get("min_steady_seconds"), 120)),
                    "max_unexpected_error_rate": error_limit,
                    "error_rate_is_operational_assumption": True})


def _integrity_result(nfr, concurrency, artifact_error):
    probe = concurrency.get(nfr.lower(), concurrency.get(nfr, {}))
    if not isinstance(probe, dict):
        probe = {}
    reasons = probe.get("reasons", [])
    reasons = reasons if isinstance(reasons, list) else [str(reasons)]
    observed = probe.get("observed", {})
    observed = dict(observed) if isinstance(observed, dict) else {"raw": observed}
    successful_sales = _number(probe.get("successful_sales", observed.get("successful_sales")), 0)
    observed["successful_sales"] = successful_sales
    status = str(probe.get("status", INCONCLUSIVE)).upper()
    if status not in (PASS, FAIL, INCONCLUSIVE):
        status = INCONCLUSIVE
        reasons.append("Trạng thái concurrency probe không hợp lệ.")
    if artifact_error:
        status = INCONCLUSIVE
        reasons.append(artifact_error)
    elif not probe:
        status = INCONCLUSIVE
        reasons.append("Thiếu dedicated concurrency probe; baseline inventory không chứng minh xử lý race.")
    elif status == PASS and successful_sales <= 0:
        status = INCONCLUSIVE
        reasons.append("Không có đặt vé thành công được xác nhận; lượt không bán vé không thể PASS.")
    if not reasons:
        reasons = ["Dedicated concurrent probes hoàn tất với dữ liệu đối chiếu hợp lệ."] if status == PASS else ["Concurrency probe chưa có kết luận đủ bằng chứng."]
    return _result(nfr, status, reasons, observed,
                   {"positive_successful_sales_required": True,
                    "dedicated_concurrency_probe_required": True,
                    "invariant": "0 <= remaining <= total; confirmed_quantity + remaining = total",
                    "fixture_scope": "Separate fixture IDs; excluded from the baseline ticket-hunt inventory."})


def build_nfr_report(run_dir):
    """Read raw evidence and write JSON/CSV/text NFR outcomes without raising on absent data."""
    run_dir = Path(run_dir)
    meta, meta_error = _load_json(run_dir / "metadata.json")
    scenario, scenario_error = _load_json(run_dir / "scenario.json")
    pages, pages_error = _load_json(run_dir / "pages.json")
    concurrency, concurrency_error = _load_json(run_dir / "concurrency.json")
    requests, requests_error = _load_csv(run_dir / "requests.csv")
    monitor_rows, monitor_error = _load_csv(run_dir / "concurrency.csv")
    warnings = [error for error in (meta_error, scenario_error, pages_error, concurrency_error,
                                    requests_error, monitor_error) if error]
    results = []
    try:
        monitor, samples, monitor_missing, monitor_failures = _monitor_evidence(meta, scenario, monitor_rows)
    except (TypeError, ValueError, OverflowError) as error:
        monitor, samples, monitor_missing, monitor_failures = _monitor_evidence({}, {}, [])
        monitor_missing.append(f"Không xử lý được cấu hình/nhật ký steady: {type(error).__name__}: {error}")
    evaluations = [
        ("NFR01", lambda: _pages_result(meta, pages, monitor, samples, pages_error)),
        ("NFR02", lambda: _concerts_result(meta, requests, requests_error)),
        ("NFR03", lambda: _capacity_result(meta, scenario, requests, monitor, monitor_missing,
                        monitor_failures, (meta_error, scenario_error, requests_error, monitor_error))),
        ("NFR04", lambda: _integrity_result("NFR04", concurrency, concurrency_error)),
        ("NFR05", lambda: _integrity_result("NFR05", concurrency, concurrency_error)),
    ]
    for nfr, evaluate in evaluations:
        try:
            result = evaluate()
            if meta_error and result["status"] == PASS and nfr in ("NFR01", "NFR02"):
                result["status"] = INCONCLUSIVE
                result["reasons"].append(meta_error)
            results.append(result)
        except (TypeError, ValueError, KeyError, AttributeError, OverflowError) as error:
            results.append(_result(nfr, INCONCLUSIVE,
                [f"Không xử lý được dữ liệu đo: {type(error).__name__}: {error}"], {}, {}))
    overall = FAIL if any(r["status"] == FAIL for r in results) else INCONCLUSIVE if any(r["status"] == INCONCLUSIVE for r in results) else PASS
    result = {"schema_version": 1, "run_dir": str(run_dir), "status": overall,
              "results": results, "requirements": {row["nfr"]: row for row in results},
              "assumptions": ["Normal network: the explicit simulated Chromium profile in pages.json, not a claim about every real network.",
                  "Steady observation >=120 seconds; readiness is sampled throughout, not inferred from peak configured users.",
                  f"NFR03 unexpected error budget {_number(meta.get('max_error_rate'), 0.01):.2%} is an operational assumption; the supplied NFRs did not specify an error budget.",
                  "Measurements are local laboratory observations; no production capacity is inferred.",
                  "Dedicated race fixtures are separate from baseline ticket-hunt inventory."],
              "warnings": warnings}
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "nfr_results.json").write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    with (run_dir / "nfr_results.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["nfr", "status", "description", "reasons", "observed", "target"])
        writer.writeheader()
        for row in results:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                             for key, value in row.items()})
    lines = [f"NFR report: {overall}"]
    for row in results:
        lines.append(f"{row['nfr']} — {row['status']}: {row['description']}")
        lines.extend(f"  - {reason}" for reason in row["reasons"])
    lines.extend(["", "Assumptions:"] + [f"  - {note}" for note in result["assumptions"]])
    (run_dir / "nfr_summary.txt").write_text("\n".join(lines), encoding="utf-8")
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    build_nfr_report(args.run_dir)
    print((args.run_dir / "nfr_summary.txt").read_text(encoding="utf-8"))
