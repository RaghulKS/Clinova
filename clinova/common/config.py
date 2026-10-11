"""Settings for every Clinova module, read from the environment and `.env`.

`.env` is looked up in the working tree root first, then in the main clone root
(worktrees share the main clone's `.env`). Real environment variables beat both.
Blank values (`KEY=` or `KEY=   # comment`) count as unset.

Gemini model ids live here and nowhere else in the code.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


def _main_root() -> Path:
    """Main clone root (parent of the shared .git dir); REPO_ROOT when git is unavailable (e.g. Docker)."""
    try:
        out = subprocess.run(
            # Scoped trust: a worktree created by another OS user must still find the main clone (T24).
            ["git", "-c", f"safe.directory={REPO_ROOT.as_posix()}",
             "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return REPO_ROOT
    return Path(out).parent if out else REPO_ROOT


MAIN_ROOT = _main_root()


class Settings(BaseSettings):
    # Later files win, so the tree-root .env overrides the main clone's. Missing files are skipped.
    model_config = SettingsConfigDict(
        env_file=(MAIN_ROOT / ".env", REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    CLINOVA_MODE: Literal["demo", "live"] = "demo"

    GEMINI_API_KEY: SecretStr | None = None
    # Checked 2026-10-10 against https://ai.google.dev/gemini-api/docs/models and
    # https://ai.google.dev/gemini-api/docs/embeddings : gemini-3.5-flash-lite is the cheapest
    # stable Flash-class text model recommended for new projects (structured outputs supported);
    # gemini-embedding-2 is the stable embedding model, 128-3072 dims (768 recommended,
    # auto-normalized). Override via env if Google retires them.
    GEMINI_MODEL_FAST: str = "gemini-3.5-flash-lite"
    GEMINI_EMBED_MODEL: str = "gemini-embedding-2"
    EMBED_DIM: int = 768
    GEMINI_CONCURRENCY: int = 8
    MAX_GEMINI_CALLS: int = 12000

    # Default is the local docker-compose TimescaleDB. Never log this value.
    DATABASE_URL: SecretStr | None = SecretStr("postgresql://postgres:postgres@localhost:5432/clinova")
    DATA_DIR: Path = MAIN_ROOT / ".data"
    MODELS_DIR: Path = MAIN_ROOT / "models"  # T22 model bundles (models/<version>/bundle.joblib), gitignored
    # Served bundle. Falls back to the newest bundle when this version is not on disk.
    MODEL_VERSION: str | None = "v1.0"
    SEED: int = 42
    DEMO_NCT_ID: str | None = "NCT03232892"  # G1 pick, see docs/demo_trial.md; backups NCT03417882, NCT06333678
    SKIP_MIROFISH: bool = False
    NTFY_TOPIC: str | None = None  # used by scripts/orch.sh, not by Python code

    # Regeneron Starter Sheet 01: median cost per substantial protocol amendment.
    AMENDMENT_COST_PHASE2: float = 141_000
    AMENDMENT_COST_PHASE3: float = 535_000
    AMENDMENT_DELAY_MONTHS: float = 3
    AMENDMENT_COST_SOURCE: str = "Regeneron Starter Sheet 01 (median cost per substantial amendment; ~3 months delay)"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def amendment_cost(phase: str | None) -> float:
    """Dollar cost of one substantial amendment for a contract phase value.

    "phase3" and "phase2_3" use the Phase III cost; "phase2", unknown or None use Phase II.
    """
    s = get_settings()
    return s.AMENDMENT_COST_PHASE3 if phase in ("phase3", "phase2_3") else s.AMENDMENT_COST_PHASE2
