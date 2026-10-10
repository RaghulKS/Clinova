"""CLI: ``python -m clinova.api [--host 0.0.0.0] [--port 8000]`` serves ``clinova.api.main:app`` (owner: T15)."""

from __future__ import annotations

import argparse

import uvicorn


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m clinova.api")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    a = p.parse_args(argv)
    uvicorn.run("clinova.api.main:app", host=a.host, port=a.port)  # T15 creates clinova/api/main.py
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
