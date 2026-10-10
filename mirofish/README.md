# MiroFish community pulse (optional)

MiroFish-Offline is AGPL-3.0. Clone it unmodified into `.data/mirofish-service/` and run it as a
separate service; no MiroFish code lives in this repo. This folder holds only the compose override,
an env template and this note.

Status: not run. The build host's Docker VM has 3.8 GB RAM and no GPU; the stack needs about 8 GB
(Neo4j 2 GB heap, app, Ollama embeddings). See `docs/handoff/T16.md`.

`clinova/simulate/mirofish_client.py` talks to the service over HTTP (`MIROFISH_URL`, default
`http://localhost:5001`) and reads reports from `docs/pulse/<trial_id>.md`. Without a cached
report the Community Pulse tab shows "not run".
