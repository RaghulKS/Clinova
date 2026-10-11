"""CLI: ``python -m clinova.api [--host 0.0.0.0] [--port 8000]`` serves ``clinova.api.main:app`` (owner: T15)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import uvicorn

_FLAG = "CLINOVA_API_LIBOMP_FIXED"


def _libomp_dir() -> str | None:
    """On macOS without Homebrew's libomp, LightGBM cannot load. scikit-learn's wheel ships a libomp;
    return its folder when LightGBM fails to import and that folder exists."""
    if sys.platform != "darwin" or os.environ.get(_FLAG):
        return None
    try:
        import lightgbm  # noqa: F401
    except OSError:
        try:
            import sklearn
        except ImportError:
            return None
        d = Path(sklearn.__file__).parent / ".dylibs"
        return str(d) if (d / "libomp.dylib").exists() else None
    except ImportError:
        return None
    return None


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    p = argparse.ArgumentParser(prog="python -m clinova.api")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    a = p.parse_args(args)
    if (d := _libomp_dir()) is not None:
        # dyld reads DYLD_FALLBACK_LIBRARY_PATH only at process start, so re-exec once with it set
        env = dict(os.environ, **{_FLAG: "1"})
        env["DYLD_FALLBACK_LIBRARY_PATH"] = os.pathsep.join(x for x in (d, env.get("DYLD_FALLBACK_LIBRARY_PATH")) if x)
        os.execve(sys.executable, [sys.executable, "-m", "clinova.api", *args], env)
    uvicorn.run("clinova.api.main:app", host=a.host, port=a.port)  # T15 creates clinova/api/main.py
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
