"""Run the backend and frontend together: python -m scripts.dev."""

import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    node = shutil.which("node")
    if node is None:
        print("Node.js is required to run the frontend.", file=sys.stderr)
        return 1

    local_python = ROOT / ".venv" / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    python = str(local_python) if local_python.exists() else sys.executable
    try:
        check = subprocess.run(
            [python, "-m", "uvicorn", "--version"], cwd=ROOT,
            capture_output=True, text=True,
        )
    except OSError as exc:
        print(f"Cannot start Python: {exc}", file=sys.stderr)
        return 1
    if check.returncode:
        print(check.stderr or check.stdout, file=sys.stderr)
        print("Check your Python environment and install requirements.txt.", file=sys.stderr)
        return 1
    processes = []
    options = {"cwd": ROOT}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True

    try:
        processes.append(subprocess.Popen(
            [python, "-m", "uvicorn", "app.main:app", "--reload"], **options
        ))
        processes.append(subprocess.Popen(
            [node, "frontend/dev-server.cjs"], **options
        ))
        print("Starting FE and BE. Press Ctrl+C to stop both.", flush=True)
        while True:
            for process in processes:
                if process.poll() is not None:
                    return process.returncode
            time.sleep(0.2)
    except KeyboardInterrupt:
        return 0
    finally:
        # Ignore repeated Ctrl+C while the servers finish shutting down.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        for process in processes:
            if process.poll() is None:
                try:
                    if os.name == "nt":
                        process.send_signal(signal.CTRL_BREAK_EVENT)
                    else:
                        os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
