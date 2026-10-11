"""CLI (owner: T12):
``python -m clinova.parse run --versions first|amended [--limit N]`` and ``python -m clinova.parse embed``.
Resumable: trials that already have rules for the version are skipped. Test split first.
Amended = ``eligibility_text_current`` of trials with label_amend, stored as version_no 1."""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from clinova.common.db import get_conn
from clinova.contracts import ParsedProtocol, Phase

WORKERS = 8


def _todo(versions: str, limit: int | None) -> list[tuple]:
    vno, col = (0, "eligibility_text_first") if versions == "first" else (1, "eligibility_text_current")
    where = "" if versions == "first" else "AND label_amend IS TRUE"
    sql = (
        f"SELECT nct_id, {col}, phase FROM trials t WHERE {col} IS NOT NULL {where} "
        "AND NOT EXISTS (SELECT 1 FROM criteria_rules r WHERE r.nct_id = t.nct_id AND r.version_no = %s) "
        "ORDER BY CASE split WHEN 'test' THEN 0 WHEN 'train' THEN 1 ELSE 2 END, nct_id"
    )
    with get_conn() as c:
        rows = c.execute(sql + (" LIMIT %s" if limit else ""), (vno, limit) if limit else (vno,)).fetchall()
    return [(r[0], r[1], r[2], vno) for r in rows]


def save_protocol(p: ParsedProtocol) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM criteria_rules WHERE nct_id = %s AND version_no = %s", (p.trial_id, p.version_no))
        for r in p.rules:
            c.execute(
                "INSERT INTO criteria_rules (nct_id, version_no, rule_id, kind, field, operator, value, unit, raw_text, "
                "parse_status, locked, note) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s)",
                (p.trial_id, p.version_no, r.rule_id, r.kind, r.field.value, r.operator.value, json.dumps(r.value),
                 r.unit, r.raw_text, r.parse_status, r.locked, r.note),
            )


def run(versions: str, limit: int | None) -> dict[str, int]:
    from clinova.parse.parser import parse_protocol

    todo = _todo(versions, limit)
    print(f"parse {versions}: {len(todo)} trials to do", flush=True)
    stats = {"todo": len(todo), "ok": 0, "failed": 0, "rules": 0, "unparseable": 0}

    def one(t):
        nid, text, phase, vno = t
        return parse_protocol(text, trial_id=nid, version_no=vno, phase=Phase(phase) if phase else None)

    with ThreadPoolExecutor(WORKERS) as ex:
        futs = {ex.submit(one, t): t for t in todo}
        for i, f in enumerate(as_completed(futs), 1):
            nid = futs[f][0]
            try:
                p = f.result()
                save_protocol(p)
                stats["ok"] += 1
                stats["rules"] += len(p.rules)
                stats["unparseable"] += len(p.unparseable)
            except Exception as e:  # noqa: BLE001 - one bad trial must not stop the batch
                stats["failed"] += 1
                print(f"FAIL {nid}: {type(e).__name__}: {str(e)[:200]}", flush=True)
                if type(e).__name__ == "BudgetExceeded":
                    ex.shutdown(cancel_futures=True)
                    break
            if i % 25 == 0:
                print(f"{i}/{len(todo)} {stats}", flush=True)
    print(f"done {stats}", flush=True)
    return stats


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m clinova.parse")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="Batch-parse trials into criteria_rules (resumable, test split first).")
    r.add_argument("--versions", choices=["first", "amended"], default="first")
    r.add_argument("--limit", type=int, default=None)
    sub.add_parser("embed", help="Embed first-version eligibility text into trial_embeddings (resumable).")
    a = p.parse_args(argv)
    if a.cmd == "embed":
        from clinova.parse.embed import embed_trials

        print(embed_trials())
        return 0
    run(a.versions, a.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
