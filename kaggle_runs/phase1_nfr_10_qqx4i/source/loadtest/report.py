"""Summarize observed results without equating zero sales with a valid test."""
import csv
import json
import math
import sqlite3
from contextlib import closing
from pathlib import Path


def read_csv(path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def inventory_report(db_path, concert_id):
    with closing(sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        inventory = [dict(row) for row in db.execute("""
            SELECT t.id, t.name, t.total_quantity, t.remaining,
                COALESCE(SUM(CASE WHEN o.status='confirmed' THEN o.quantity ELSE 0 END),0) AS confirmed,
                COALESCE(SUM(CASE WHEN o.status='cancelled' THEN o.quantity ELSE 0 END),0) AS cancelled,
                COALESCE(SUM(o.quantity),0) AS booked
            FROM ticket_types t LEFT JOIN orders o ON o.ticket_type_id=t.id
            WHERE t.concert_id=?
            GROUP BY t.id ORDER BY t.id
        """, (concert_id,))]
        orders = [dict(row) for row in db.execute("SELECT o.status, COUNT(*) AS count FROM orders o JOIN ticket_types t ON t.id=o.ticket_type_id WHERE t.concert_id=? GROUP BY o.status", (concert_id,))]
        duplicate_users = db.execute("""SELECT COUNT(*) FROM (
            SELECT o.user_id FROM orders o JOIN ticket_types t ON t.id=o.ticket_type_id WHERE o.status='confirmed' AND t.concert_id=? GROUP BY o.user_id HAVING COUNT(*)>1
        )""", (concert_id,)).fetchone()[0]
        checks = {
            "sqlite_integrity": db.execute("PRAGMA integrity_check").fetchone()[0] == "ok",
            "foreign_keys": not db.execute("PRAGMA foreign_key_check").fetchall(),
            "inventory_matches": bool(inventory) and all(
                0 <= r["remaining"] <= r["total_quantity"]
                and r["total_quantity"] - r["remaining"] == r["confirmed"] for r in inventory),
            "valid_order_statuses": all(r["status"] in ("confirmed", "cancelled") for r in orders),
            "one_active_order_per_user": duplicate_users == 0,
        }
    return inventory, orders, checks


def build_report(run_dir):
    run_dir = Path(run_dir)
    meta = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    metrics_path = run_dir / "scenario.json"
    scenario = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.exists() else {}
    counters = scenario.get("counters", {})
    request_rows = read_csv(run_dir / "requests.csv")
    final_path = run_dir / "final_stats.csv"
    stats = read_csv(final_path if final_path.exists() else run_dir / "result_stats.csv")
    aggregate = next((r for r in stats if r["Name"] == "Aggregated"), None)
    inventory, orders, checks = inventory_report(run_dir / "concert.db", json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))["concert_id"])
    warnings = []
    if not final_path.exists():
        warnings.append("Thiếu final_stats.csv; thống kê định kỳ có thể thiếu request cuối.")
    if not scenario:
        warnings.append("Thiếu scenario.json: không xác nhận được kịch bản kết thúc đầy đủ.")
    if not aggregate or not int(aggregate["Request Count"]):
        warnings.append("Không có request được thống kê.")
    if counters.get("ready", 0) != meta["users"]:
        warnings.append(f"Chỉ {counters.get('ready', 0)}/{meta['users']} user hoàn tất khởi tạo.")
    if counters.get("init_failed", 0) or counters.get("script_errors", 0):
        warnings.append("Có lỗi khởi tạo hoặc lỗi kịch bản; xem scenario.json và locust.log.")
    if not counters.get("buy.ok", 0):
        warnings.append("Không có đặt vé thành công được client xác nhận; chưa đánh giá được luồng mua vé đầy đủ.")
    if not counters.get("cancel.ok", 0):
        warnings.append("Chưa quan sát được hủy vé thành công trong lượt này.")
    if meta.get("locust_exit_code") not in (0, 1):
        warnings.append("Locust không kết thúc bình thường.")
    if not meta.get("api_shutdown_clean"):
        warnings.append("API chưa được xác nhận dừng sạch; cần xem api.log khi đánh giá tồn kho.")
    if not all(checks.values()):
        warnings.append("Đối chiếu dữ liệu KHÔNG ĐẠT.")
    if aggregate and int(aggregate["Request Count"]) != counters.get("requests"):
        warnings.append("Số request trong CSV và bộ đếm kịch bản không khớp.")
    if len(request_rows) != counters.get("requests"):
        warnings.append("Nhật ký từng request chưa khớp bộ đếm kịch bản.")
    api_text = (run_dir / "api.log").read_text(encoding="utf-8", errors="replace") if (run_dir / "api.log").exists() else ""
    lock_mentions = api_text.count("database is locked")
    if lock_mentions:
        warnings.append("api.log có lỗi database is locked (số lần xuất hiện không phải số request lỗi).")
    if "QueuePool limit" in api_text:
        warnings.append("api.log có lỗi hết kết nối QueuePool; cần xem vòng đời transaction/session của backend.")
    for name in ("api.log", "locust.log"):
        path = run_dir / name
        if path.exists():
            (run_dir / name.replace(".log", "_tail.txt")).write_text(
                "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]), encoding="utf-8")
    if inventory:
        with (run_dir / "inventory_check.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(inventory[0]))
            writer.writeheader()
            writer.writerows(inventory)
    total = sum(r["total_quantity"] for r in inventory)
    confirmed = sum(r["confirmed"] for r in inventory)
    booked = sum(r["booked"] for r in inventory)
    returned = sum(r["cancelled"] for r in inventory)
    buy_latencies = {}
    for outcome in ("ok", "sold_out", "failed"):
        values = sorted(float(r["latency_ms"]) for r in request_rows
                        if r["method"] == "POST" and r["endpoint"] == "/orders"
                        and ((r["failed"] == "1") if outcome == "failed" else r["outcome"] == outcome))
        buy_latencies[outcome] = {"count": len(values), "p50_p95_p99_ms": [
            round(values[max(0, math.ceil(len(values) * p) - 1)], 3) for p in (.5, .95, .99)
        ] if values else None}
    lines = ["HIT THE VIBE — SĂN VÉ (ticket-hunt-nfr-v3)",
             f"Môi trường: {meta['platform']}; API và Locust cùng máy; Uvicorn 1 worker.",
             f"Phase: {meta['phase']}; user: {meta['users']}; tăng {meta['spawn_rate']}/giây; thời gian: {meta['seconds']} giây.",
             "Tạo tài khoản ngoài thời gian đo. Đăng nhập/ramp-up có log riêng; bảng NFR đo riêng steady. Có giai đoạn chờ request cuối hoàn tất.",
             f"User bắt đầu / đăng nhập / sẵn sàng: {counters.get('started', 0)} / {counters.get('logged_in', 0)} / {counters.get('ready', 0)}",
             f"Lỗi khởi tạo: {counters.get('init_failed', 0) if scenario else 'không có dữ liệu'}; lỗi kịch bản: {counters.get('script_errors', 0) if scenario else 'không có dữ liệu'}"]
    if aggregate:
        count, failures = int(aggregate["Request Count"]), int(aggregate["Failure Count"])
        lines += [f"Request: {count}; lỗi Locust: {failures} ({100*failures/max(count,1):.2f}%); RPS: {float(aggregate['Requests/s']):.2f}",
                  f"P50/P95/P99 toàn bài (ms): {aggregate['50%']} / {aggregate['95%']} / {aggregate['99%']}"]
        for row in stats:
            if row["Name"] in ("/auth/login", "/orders") and row["Type"] == "POST":
                lines.append(f"POST {row['Name']}: {row['Request Count']} request; {row['Failure Count']} lỗi; "
                             f"RPS {float(row['Requests/s']):.2f}; P50/P95/P99 {row['50%']}/{row['95%']}/{row['99%']} ms")
    lines += [f"Đặt thành công được client xác nhận: {counters.get('buy.ok',0)}; hết vé: {counters.get('buy.sold_out',0)}; hủy thành công: {counters.get('cancel.ok',0)}",
              f"P50/P95/P99 riêng đơn thành công (ms, từ mẫu request): {buy_latencies['ok']['p50_p95_p99_ms']}",
              f"HTTP 4xx/5xx: {counters.get('http_4xx_5xx',0)}; lỗi kết nối: {counters.get('transport_errors',0)}",
              "Chỉ 409 + error=SoldOut được coi là từ chối nghiệp vụ hợp lệ, không phải mua thành công.",
              f"Database: tổng vé từng đặt {booked}; đã hoàn {returned}; còn confirmed {confirmed}/{total}.",
              f"Kiểm tra dữ liệu: {json.dumps(checks, ensure_ascii=False)}",
              "Số đơn DB có thể khác số response thành công nếu client timeout sau khi server ghi dữ liệu.",
              "Đây là mô phỏng hành vi săn vé hữu hạn, không phải đo công suất đặt vé liên tục.",
              "Bảng baseline gồm warmup + steady + drain; dùng nfr_summary.txt để đối chiếu NFR01–05."]
    lines += ["CẢNH BÁO: " + warning for warning in warnings]
    result = {"metadata": meta, "scenario": scenario, "stats": stats, "inventory": inventory,
              "orders": orders, "checks": checks, "warnings": warnings, "buy_latency_by_outcome": buy_latencies}
    (run_dir / "summary.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    (run_dir / "baseline_summary.txt").write_text("\n".join(lines), encoding="utf-8")
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    print(build_report(parser.parse_args().run_dir))
