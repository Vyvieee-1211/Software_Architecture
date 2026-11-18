"""Tổng hợp ba lượt mỗi cấu hình và vẽ throughput/p95 S9 bằng SVG độc lập."""
import csv
import json
from collections import defaultdict
from statistics import mean


def write_scalability(folder, state):
    records = []
    for job in state["jobs"]:
        if not job["name"].startswith("S9_") or not job.get("result_path"):
            continue
        result_path = folder / job["result_path"]
        result = json.loads(result_path.read_text(encoding="utf-8"))
        metadata = json.loads(result_path.with_name("metadata.json").read_text(encoding="utf-8"))
        db_container = metadata.get("db_container")
        values = [v["p95_ms"] for key, v in result["metrics"].items() if key.startswith("POST /orders [")]
        peak_cpu = 0
        with result_path.with_name("resources.csv").open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if row.get("phase") != "steady":
                    continue
                for container in json.loads(row["docker_stats_json"] or "[]"):
                    name = container.get("Name", "")
                    if name == db_container or (not db_container and "-db-" in name):
                        peak_cpu = max(peak_cpu, float(container.get("CPUPerc", "0%").rstrip("%")))
        records.append({"job": job["name"], "workload": metadata["workload"], "api_workers": metadata["api_workers"],
                        "pool_size": metadata["db_pool_size"], "status": result["status"],
                        "http_rps": result["http_requests"] / metadata["steady_seconds"],
                        "hold_p95_ms": max(values) if values else None, "db_peak_cpu_percent": peak_cpu,
                        "error_ratio": result["failure_ratio"]})
    if not records:
        return
    with (folder / "scalability.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader(); writer.writerows(records)
    groups = defaultdict(list)
    for row in records:
        groups[(row["workload"], row["pool_size"])].append(row)
    means = {key: {"http_rps": mean(r["http_rps"] for r in rows),
                   "hold_p95_ms": mean(r["hold_p95_ms"] for r in rows if r["hold_p95_ms"] is not None)
                      if any(r["hold_p95_ms"] is not None for r in rows) else None,
                   "runs": len(rows)} for key, rows in groups.items()}
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="820" height="730" viewBox="0 0 820 730">',
             '<rect width="820" height="730" fill="white"/>',
             '<g font-family="Arial,sans-serif" font-size="13" fill="#172033">',
             '<text x="45" y="26" font-size="20">S9: trung bình theo kích thước pool với một API worker</text>']
    colors = {"flash": "#2563eb", "funnel": "#059669"}
    pool_positions = {5: 130, 10: 420, 20: 710}
    for panel, (metric, label) in enumerate((("http_rps", "HTTP requests / giây"), ("hold_p95_ms", "p95 POST /orders (ms)"))):
        top = 65 + panel * 320
        ceiling = max((v[metric] or 0 for v in means.values()), default=1) * 1.1 or 1
        parts += [f'<text x="55" y="{top}">{label}</text>',
                  f'<path d="M80 {top+15} V{top+240} H750" fill="none" stroke="#94a3b8"/>']
        for step in range(5):
            y = top + 240 - step * 225 / 4
            parts.append(f'<text x="12" y="{y+4:.1f}">{ceiling*step/4:.1f}</text>')
        for pool, x in pool_positions.items():
            parts.append(f'<text x="{x-8}" y="{top+263}">{pool}</text>')
        for workload, color in colors.items():
            points = []
            for pool, x in pool_positions.items():
                value = means.get((workload, pool))
                if value is None or value[metric] is None:
                    continue
                y = top + 240 - value[metric] / ceiling * 225
                points.append(f"{x},{y:.1f}")
                parts.append(f'<circle cx="{x}" cy="{y:.1f}" r="4" fill="{color}"/>')
            parts.append(f'<polyline points="{" ".join(points)}" stroke="{color}" fill="none" stroke-width="2"/>')
    parts += ['<text x="90" y="710" fill="#2563eb">flash</text>', '<text x="190" y="710" fill="#059669">funnel</text>',
              '<text x="290" y="710">Đối chiếu số lượt, lỗi và CPU DB trong scalability.csv.</text>', '</g></svg>']
    (folder / "scalability.svg").write_text("\n".join(parts), encoding="utf-8")
