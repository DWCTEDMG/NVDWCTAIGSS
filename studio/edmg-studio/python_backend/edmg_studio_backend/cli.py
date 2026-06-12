from __future__ import annotations

import argparse
import uvicorn

from .app import app


def main() -> None:
    p = argparse.ArgumentParser(
        prog="edmg-studio-backend",
        description="EDMG NVIDIA Workshop backend server.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="Run FastAPI server.")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=7863)
    s.add_argument("--reload", action="store_true")

    args = p.parse_args()

    if args.cmd == "serve":
        uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
