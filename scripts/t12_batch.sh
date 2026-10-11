#!/usr/bin/env bash
# T12 background batch: waits for T11's trials, then parses (test split first), parses amended
# eligibility, and embeds. Every step is resumable; the loop repeats so trials that T11 loads later
# are picked up. Usage: nohup scripts/t12_batch.sh >> <main>/.data/logs/T12-parse.log 2>&1 &
cd "$(dirname "$0")/.." || exit 1
for round in $(seq 1 60); do
  echo "[$(date -u +%FT%TZ)] round $round"
  uv run python -m clinova.parse run --versions first
  uv run python -m clinova.parse run --versions amended
  uv run python -m clinova.parse embed
  sleep 300
done
