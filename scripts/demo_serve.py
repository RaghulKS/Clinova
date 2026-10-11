"""API entry point for demo mode (owner: T31): serves the frozen optimizer results for the three demo trials.

    python scripts/demo_serve.py [--host 0.0.0.0] [--port 8000]

The real optimizer takes about two minutes per trial. In demo mode, `live.optimize` returns the fixes frozen
in docs/demo_results/<NCT>_optimize.json when the protocol's rules are exactly the ones they were computed
for; any other protocol (a pasted draft, an edited one) goes to the real optimizer as before. Live mode is
untouched. Nothing here calls Gemini. Obsolete once docs/contract_changes/T31.md is applied to clinova/api.
"""

from __future__ import annotations

import json
import sys

from clinova.api import live
from clinova.common.config import REPO_ROOT, get_settings
from clinova.contracts import Fix

RESULTS = REPO_ROOT / "docs" / "demo_results"
_real_optimize = live.optimize


def _rules(protocol) -> list:
    return [r.model_dump(mode="json") for r in protocol.rules]


def cached_fixes(protocol) -> list[Fix] | None:
    path = RESULTS / f"{protocol.trial_id}_optimize.json"
    if not path.is_file():
        return None
    saved = json.loads(path.read_text(encoding="utf-8"))
    if saved["protocol"]["rules"] != _rules(protocol):
        return None
    return [Fix.model_validate(f) for f in saved["fixes"]]


def optimize(protocol, max_edits=3):
    if get_settings().CLINOVA_MODE == "demo" and (fixes := cached_fixes(protocol)) is not None:
        return fixes
    return _real_optimize(protocol, max_edits)


live.optimize = optimize  # routes and the run manager look it up on the module at call time

if __name__ == "__main__":
    from clinova.api.__main__ import main

    sys.exit(main(sys.argv[1:]))
