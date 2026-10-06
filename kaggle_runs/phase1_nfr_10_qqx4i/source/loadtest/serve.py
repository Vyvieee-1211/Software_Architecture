"""One Uvicorn worker with graceful, private file-based shutdown on Windows/Linux."""
import argparse
import asyncio
from pathlib import Path

import uvicorn


async def serve(port, stop_file):
    server = uvicorn.Server(uvicorn.Config("app.main:app", host="127.0.0.1", port=port, workers=1))

    async def watch():
        while not stop_file.exists():
            await asyncio.sleep(0.2)
        server.should_exit = True

    watcher = asyncio.create_task(watch())
    try:
        await server.serve()
    finally:
        watcher.cancel()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--stop-file", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(serve(args.port, args.stop_file))
