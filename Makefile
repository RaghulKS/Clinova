.PHONY: all sync lint test check db-apply up down

all: check

sync:
	uv sync

lint:
	uv run ruff check .

test:
	uv run pytest -q

# Every task must pass this before finishing.
check: lint test

# Apply db/schema.sql (idempotent) to DATABASE_URL from .env (Tiger Cloud or the local stack).
db-apply:
	uv run python -m clinova.common.db apply

# Local stack: TimescaleDB with pgvector (T31 adds the api and app services and the seed).
up:
	docker compose up -d

down:
	docker compose down
