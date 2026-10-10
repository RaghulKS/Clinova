"""Postgres access: one lazy connection pool, `get_conn()` and `apply_schema()`.

CLI: `python -m clinova.common.db apply` applies db/schema.sql and lists public tables,
views and hypertables.
"""

from __future__ import annotations

import re
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector
from psycopg_pool import ConnectionPool

from clinova.common.config import REPO_ROOT, get_settings

_pool: ConnectionPool | None = None
_lock = threading.Lock()


def _dsn() -> str:
    url = get_settings().DATABASE_URL
    if url is None:
        raise RuntimeError("DATABASE_URL is not set")
    return url.get_secret_value()


def _configure(conn: psycopg.Connection) -> None:
    # Register the pgvector type only where the extension exists; the pool needs the
    # connection back idle, hence the commit.
    if conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'").fetchone():
        register_vector(conn)
    conn.commit()


def _get_pool() -> ConnectionPool:
    global _pool
    with _lock:
        if _pool is None:
            _pool = ConnectionPool(
                _dsn(),
                min_size=1,
                max_size=8,
                open=True,
                configure=_configure,
                check=ConnectionPool.check_connection,  # Tiger Cloud drops idle connections
            )
        return _pool


@contextmanager
def get_conn() -> Iterator[psycopg.Connection]:
    """Yield a pooled connection (autocommit off). Commits on success, rolls back on error."""
    with _get_pool().connection() as conn:
        yield conn


def close_pool() -> None:
    global _pool
    with _lock:
        if _pool is not None:
            _pool.close()
            _pool = None


def _statements(sql: str) -> list[str]:
    # db/schema.sql promises no DO blocks / dollar quotes and no semicolon inside comments or
    # literals, so splitting on a semicolon at end of line is exact for that file.
    chunks = re.split(r";[ \t]*$", sql, flags=re.MULTILINE)
    return [
        c.strip()
        for c in chunks
        if any(line.strip() and not line.strip().startswith("--") for line in c.splitlines())
    ]


def apply_schema(path: Path = REPO_ROOT / "db" / "schema.sql") -> None:
    """Run the (idempotent) schema file one statement at a time in autocommit mode.

    Autocommit and one statement per call because TimescaleDB continuous aggregates cannot be
    created inside a transaction block (a multi-statement string runs as one implicit transaction).
    """
    sql = Path(path).read_text(encoding="utf-8")
    with psycopg.connect(_dsn(), autocommit=True) as conn:
        for stmt in _statements(sql):
            conn.execute(stmt)
    close_pool()  # new pooled connections re-run _configure, e.g. if `vector` was just created


def _list_objects() -> dict[str, list[str]]:
    with psycopg.connect(_dsn(), autocommit=True) as conn:
        out = {
            "tables": [r[0] for r in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY 1")],
            "views": [r[0] for r in conn.execute(  # plain + materialized, minus extension-owned ones
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind IN ('v', 'm') AND NOT EXISTS "
                "(SELECT 1 FROM pg_depend d WHERE d.objid = c.oid AND d.deptype = 'e') ORDER BY 1")],
            "hypertables": [],
        }
        if conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'").fetchone():
            out["hypertables"] = [r[0] for r in conn.execute(
                "SELECT hypertable_name FROM timescaledb_information.hypertables "
                "WHERE hypertable_schema = 'public' ORDER BY 1")]
    return out


if __name__ == "__main__":
    if sys.argv[1:] != ["apply"]:
        sys.exit("usage: python -m clinova.common.db apply")
    apply_schema()
    for kind, names in _list_objects().items():
        print(f"{kind} ({len(names)}): {', '.join(names)}")
