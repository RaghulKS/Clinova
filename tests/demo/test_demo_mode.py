"""Demo mode and packaging (owner: T31).

Offline tests: seed checksums, compose and Dockerfile shape, the cached-optimizer shim.
`test_demo_flow_*` need the seeded database (they skip when DATABASE_URL does not answer). They run the three
demo trials with CLINOVA_MODE=demo and no GEMINI_API_KEY, with every network connection except the database
refused, and compare the results with docs/demo_results/ (frozen at gate 2). They use the seed's own model
bundle and surrogate, laid out the way the Dockerfile lays them out.
"""

from __future__ import annotations

import json
import shutil
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "db" / "seed"
DEMO = ["NCT03232892", "NCT03417882", "NCT06333678"]
sys.path.insert(0, str(ROOT / "scripts"))


# ---- offline -----------------------------------------------------------------------------------------


def test_seed_checksums_match_manifest():
    import seed

    manifest = seed.verify()
    assert set(manifest["tables"]) == {t for t, _ in seed.TABLES}
    # trial_versions is empty in this build (version histories are not loaded); the rest the demo needs
    for t in ("trials", "criteria_rules", "trial_embeddings", "sim_runs", "persona_outcomes", "predictions",
              "prediction_scores", "outcomes", "fixes", "llm_cache"):
        assert manifest["tables"][t]["rows"] > 0, t
    assert "llm_usage" not in manifest["tables"] and "feedback" not in manifest["tables"]
    assert sum(m["bytes"] for m in manifest["tables"].values()) < 90e6  # over that: Release asset instead


def test_seed_files_are_the_served_bundle():
    assert (SEED / "files/bundle/v1.0/bundle.joblib").is_file()
    assert json.loads((SEED / "files/bundle/v1.0/meta.json").read_text())["version"] == "v1.0"
    assert (SEED / "files/surrogate.json").is_file()


def test_compose_shape():
    text = (ROOT / "docker-compose.yml").read_text()
    c = yaml.safe_load(text)["services"]
    assert c["db"]["healthcheck"] and c["api"]["healthcheck"] and c["app"]["healthcheck"]
    assert c["api"]["depends_on"]["seed"]["condition"] == "service_completed_successfully"
    assert c["app"]["depends_on"]["api"]["condition"] == "service_healthy"
    assert c["seed"]["depends_on"]["db"]["condition"] == "service_healthy"
    assert c["loop"]["profiles"] == ["loop"] and c["mirofish"]["profiles"] == ["mirofish"]
    assert "profiles" not in c["api"] and "profiles" not in c["app"]
    assert "8501:8501" in c["app"]["ports"]
    assert "env_file" not in text  # a clean clone has no .env


def test_dockerfile_non_root_and_slim():
    d = (ROOT / "Dockerfile").read_text()
    assert "FROM python:3.11-slim" in d and "USER clinova" in d and "uv sync --frozen" in d
    ignored = (ROOT / ".dockerignore").read_text().split()
    assert ".env" in ignored and "db" not in ignored  # no secrets in the image, but the seed must get in


def test_cached_optimizer_shim_serves_only_matching_rules(parsed_protocol):
    from clinova.api import demo_cache
    from clinova.contracts import ParsedProtocol

    saved = json.loads((ROOT / "docs/demo_results/NCT03232892_optimize.json").read_text())
    proto = ParsedProtocol.model_validate(saved["protocol"])
    fixes = demo_cache.lookup(proto).fixes
    assert [f.fix_id for f in fixes] == [f["fix_id"] for f in saved["fixes"]] and len(fixes) == 3
    edited = proto.model_copy(update={"rules": proto.rules[1:]})
    assert demo_cache.lookup(edited) is None  # an edited protocol goes to the real optimizer
    assert demo_cache.lookup(parsed_protocol) is None  # the fixture protocol has no cached result


# ---- needs the seeded database ----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def demo_env(tmp_path_factory):
    """Demo mode, no API key, seed-laid-out models, network closed except the database."""
    import psycopg

    from clinova.common import config

    url = config.get_settings().DATABASE_URL
    if url is None:
        pytest.skip("no DATABASE_URL")
    dsn = url.get_secret_value()
    try:
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            if not conn.execute("SELECT 1 FROM fixes LIMIT 1").fetchone():
                pytest.skip("database is not seeded")
    except Exception:
        pytest.skip("database not reachable or not seeded")

    data = tmp_path_factory.mktemp("clinova-data")
    (data / "models").mkdir()
    (data / "model").mkdir()
    shutil.copy(SEED / "files/surrogate.json", data / "models/surrogate.json")
    shutil.copy(SEED / "files/cohort_any_2000_42.parquet", data / "model/cohort_any_2000_42.parquet")
    mp = pytest.MonkeyPatch()
    mp.setattr(config.Settings, "model_config", {**config.Settings.model_config, "env_file": ()})
    for k, v in {"DATABASE_URL": dsn, "CLINOVA_MODE": "demo", "MODEL_VERSION": "v1.0", "DATA_DIR": str(data),
                 "MODELS_DIR": str(SEED / "files/bundle")}.items():
        mp.setenv(k, v)
    from clinova.model import train

    mp.setattr(train, "MODELS_DIR", SEED / "files/bundle")  # bound at import, so the env var is too late
    mp.delenv("GEMINI_API_KEY", raising=False)
    mp.delenv("CLINOVA_API_NO_DB", raising=False)
    config.get_settings.cache_clear()
    assert config.get_settings().GEMINI_API_KEY is None

    host = urlparse(dsn).hostname
    allowed = {a[4][0] for a in socket.getaddrinfo(host, None)} | {"127.0.0.1", "::1"}
    real_connect, real_gai = socket.socket.connect, socket.getaddrinfo
    attempts: list[str] = []

    def connect(self, address, *a, **k):
        if address[0] not in allowed:
            attempts.append(str(address[0]))
            raise OSError(f"network blocked in demo-mode test: {address[0]}")
        return real_connect(self, address, *a, **k)

    def gai(h, *a, **k):
        if h not in (host, "localhost", None):
            attempts.append(str(h))
            raise OSError(f"DNS blocked in demo-mode test: {h}")
        return real_gai(h, *a, **k)

    mp.setattr(socket.socket, "connect", connect)
    mp.setattr(socket, "getaddrinfo", gai)

    from clinova.common import llm

    def no_gemini(*a, **k):
        attempts.append("gemini")
        raise AssertionError("demo mode reached the Gemini client")

    mp.setattr(llm, "_client", no_gemini)
    from clinova.api import live

    live._DB_CHECK = None
    yield attempts
    mp.undo()
    config.get_settings.cache_clear()
    live._DB_CHECK = None


@pytest.fixture(scope="module")
def demo_runs(demo_env):
    from clinova.api.runs import RUNS
    from clinova.api.schemas import RunRequest

    out = {}
    for nct in DEMO:
        rid = RUNS.submit(RunRequest(nct_id=nct), background=False)
        out[nct] = (rid, RUNS.get(rid))
    return out


def test_demo_flow_reproduces_frozen_results(demo_env, demo_runs):
    for nct, (_rid, st) in demo_runs.items():
        assert st.stage == "done", st.message
        frozen = json.loads((ROOT / f"docs/demo_results/{nct}.json").read_text())
        got = {r.target.value: r for r in st.result.risks}
        for s in frozen["scores"]:
            assert got[s["target"]].p == pytest.approx(s["p"], abs=1e-6), (nct, s["target"])
        assert [f.field.value for f in st.result.flags] == [f["field"] for f in frozen["flags"]], nct
        frozen_fixes = json.loads((ROOT / f"docs/demo_results/{nct}_optimize.json").read_text())["fixes"]
        assert [f.fix_id for f in st.result.fixes] == [f["fix_id"] for f in frozen_fixes], nct
    # the demo trial has cached agent decisions, so even the simulation stage is real, not a fixture
    # (since T32 the frozen risk/flags/fixes make the run's source "cache" rather than "live")
    assert demo_runs["NCT03232892"][1].source.value == "cache"
    assert demo_env == [], f"network or Gemini attempts: {demo_env}"


def test_demo_flow_accepts_each_fix(demo_env, demo_runs, monkeypatch):
    from fastapi.testclient import TestClient

    from clinova.api import main

    monkeypatch.setattr(main, "_store_feedback", lambda fb: (fb, main.Source.fixture))  # keep the ledger clean
    client = TestClient(main.app)
    rid, st = demo_runs["NCT03232892"]
    for fix in st.result.fixes:
        r = client.post("/fixes/accept", json={"run_id": rid, "fix_id": fix.fix_id})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["basis"] == "quick_rerun" and body["before"] != body["after"]
    assert demo_env == []
