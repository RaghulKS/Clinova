"""Load the shared contract fixtures in ``tests/fixtures/`` (used by tests and API fixture fallbacks)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures"


def load_fixture(name: str, model: Any) -> Any:
    """Return ``tests/fixtures/<name>`` validated as ``model``, a contract class or a type such as
    ``list[RiskScore]``. Example: ``load_fixture("risk_scores.json", list[RiskScore])``."""
    return TypeAdapter(model).validate_json((FIXTURES_DIR / name).read_bytes())
