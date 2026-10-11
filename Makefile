.PHONY: all sync lint test check db-apply up down seed-export seed-verify tiger-load

all: check

sync:
	uv sync

lint:
	uv run ruff check .

# macOS without Homebrew libomp: borrow scikit-learn's copy for LightGBM (empty and harmless elsewhere; T32).
# Set inline in the recipe so SIP cannot strip it between make and uv. Untested on macOS by the integrator.
SKLEARN_OMP = $(shell uv run python -c "import sklearn,os;print(os.path.join(os.path.dirname(sklearn.__file__),'.dylibs'))" 2>/dev/null)

test:
	DYLD_FALLBACK_LIBRARY_PATH="$(SKLEARN_OMP)" uv run pytest -q

# Every task must pass this before finishing.
check: lint test

# Apply db/schema.sql (idempotent) to DATABASE_URL from .env (Tiger Cloud or the local stack).
db-apply:
	uv run python -m clinova.common.db apply
	uv run python -m clinova.simulate features

# Local stack: TimescaleDB with pgvector, seed loader, API (8000) and app (8501). Demo mode, no keys needed.
up:
	docker compose up -d --build

down:
	docker compose down

# Demo seed (T31): db/seed/*.csv.gz plus model bundle files, small enough to live in git.
seed-export:
	uv run python scripts/seed.py export

seed-verify:
	uv run python scripts/seed.py verify

# Load schema + seed into the database in DATABASE_URL (Tiger Cloud or any Postgres with TimescaleDB and pgvector).
# Tables that already have rows are skipped (to replace them: uv run python scripts/seed.py load --force).
tiger-load:
	uv run python scripts/seed.py load
