"""Frozen v1.0 results for the demo trial and its backups (written by ``scripts/g2_demo_results.py`` into
``docs/demo_results/``). The real optimizer takes about two minutes per trial with the v1.0 model, so the API
serves these files when the protocol being run is exactly the snapshot they were computed on (same rules and
visits; lock toggles ignored). Any edit makes the run go to the live engine instead.

Served results are tagged ``source: cache``. Nothing here computes a number.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache

from clinova.common.config import MAIN_ROOT, REPO_ROOT
from clinova.contracts import CriterionFlag, Fix, ParsedProtocol, RiskScore

log = logging.getLogger("clinova.api")

REL = "docs/demo_results"


@dataclass(frozen=True)
class Cached:
    trial_id: str
    model_version: str
    risks: list[RiskScore]
    flags: list[CriterionFlag]
    fixes: list[Fix]
    snapshot: ParsedProtocol


def _file(name: str):
    for root in (REPO_ROOT, MAIN_ROOT):
        p = root / REL / name
        if p.is_file():
            return p
    return None


@lru_cache(maxsize=16)
def _load(trial_id: str) -> Cached | None:
    scores_p, opt_p = _file(f"{trial_id}.json"), _file(f"{trial_id}_optimize.json")
    if scores_p is None or opt_p is None:
        return None
    try:
        s = json.loads(scores_p.read_text(encoding="utf-8"))
        o = json.loads(opt_p.read_text(encoding="utf-8"))
        return Cached(
            trial_id=trial_id,
            model_version=str(s.get("model_version", "")),
            risks=[RiskScore.model_validate(r) for r in s.get("scores", [])],
            flags=[CriterionFlag.model_validate(f) for f in s.get("flags", [])],
            fixes=[Fix.model_validate(f) for f in o.get("fixes", [])],
            snapshot=ParsedProtocol.model_validate(o["protocol"]),
        )
    except (ValueError, KeyError) as exc:
        log.warning("demo results for %s do not match the contract: %s", trial_id, exc)
        return None


def _shape(p: ParsedProtocol) -> tuple:
    rules = [r.model_dump(mode="json", exclude={"locked"}) for r in p.rules]
    visits = [v.model_dump(mode="json") for v in p.visits]
    return (p.trial_id, p.version_no, json.dumps(rules, sort_keys=True), json.dumps(visits, sort_keys=True))


def lookup(protocol: ParsedProtocol) -> Cached | None:
    """The frozen results for this protocol, or None when there are none or the protocol was edited."""
    if not protocol.trial_id.startswith("NCT"):
        return None
    c = _load(protocol.trial_id)
    if c is None:
        return None
    if _shape(c.snapshot) != _shape(protocol):
        log.info("demo results for %s not used: the protocol differs from the snapshot", protocol.trial_id)
        return None
    locked = {r.rule_id for r in protocol.rules if r.locked}
    fixes = [f for f in c.fixes if not any(e.target_id in locked for e in f.edits if e.kind in ("relax_rule", "drop_rule"))]
    return Cached(c.trial_id, c.model_version, c.risks, c.flags, fixes, c.snapshot)
