#!/usr/bin/env bash
# Reset only the local, seeded Gate 3 demo database and in-memory UI/API state.
# The LLM cache, seed tables, and non-demo trial rows are preserved.
set -euo pipefail

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required to reset the local demo stack." >&2
  exit 1
fi

compose_json="$(docker compose config --format json)"
python -c 'import json,sys,urllib.parse; c=json.load(sys.stdin); u=c["services"]["api"]["environment"].get("DATABASE_URL",""); p=urllib.parse.urlparse(u); ok=(p.hostname=="db" and p.path=="/clinova" and p.username=="postgres"); sys.exit(0 if ok else 1)' <<<"$compose_json" || {
  echo "Refusing reset: Compose API is not pointed at the local seeded database (db/clinova)." >&2
  exit 2
}
unset compose_json

docker compose exec -T db psql -v ON_ERROR_STOP=1 -U postgres -d clinova <<'SQL'
BEGIN;
DELETE FROM feedback WHERE trial_id = 'NCT03232892';
DELETE FROM predictions WHERE trial_id = 'NCT03232892';
COMMIT;
SQL

docker compose restart api app >/dev/null
echo "Demo audit rows cleared; API runs and app session restarted. Seed data and LLM cache are intact."
