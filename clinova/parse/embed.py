"""Eligibility embeddings and similar-trial search (owner: T12). Table ``trial_embeddings``."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from clinova.common import llm
from clinova.common.config import get_settings
from clinova.common.db import get_conn

BATCH = 64


def embed_trials(nct_ids: Sequence[str] | None = None) -> int:
    """Embed each trial's FIRST-VERSION eligibility text with ``clinova.common.llm.embed`` at
    EMBED_DIM (768) and upsert into ``trial_embeddings``. ``None`` = every trial not yet embedded.
    Returns the number of trials embedded."""
    s = get_settings()
    with get_conn() as c:
        if nct_ids is None:
            rows = c.execute(
                "SELECT nct_id, title, eligibility_text_first FROM trials t WHERE eligibility_text_first IS NOT NULL "
                "AND NOT EXISTS (SELECT 1 FROM trial_embeddings e WHERE e.nct_id = t.nct_id) "
                "ORDER BY split = 'test' DESC, nct_id"
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT nct_id, title, eligibility_text_first FROM trials "
                "WHERE nct_id = ANY(%s) AND eligibility_text_first IS NOT NULL",
                (list(nct_ids),),
            ).fetchall()
    done = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i : i + BATCH]
        vecs = llm.embed([f"{t or ''}\n{e}" for _, t, e in chunk], namespace="trials_embed", dim=s.EMBED_DIM)
        with get_conn() as c:
            for (nid, _, _), v in zip(chunk, vecs, strict=True):
                c.execute(
                    "INSERT INTO trial_embeddings (nct_id, model, embedding) VALUES (%s, %s, %s) "
                    "ON CONFLICT (nct_id) DO UPDATE SET model = EXCLUDED.model, "
                    "embedding = EXCLUDED.embedding, created_at = now()",
                    (nid, s.GEMINI_EMBED_MODEL, np.asarray(v, dtype=np.float32)),
                )
        done += len(chunk)
        print(f"embedded {done}/{len(rows)}", flush=True)
    return done


def nearest(query: str | Sequence[float], k: int = 10, *, exclude: Sequence[str] = ()) -> list[tuple[str, float]]:
    """Nearest trials by pgvector cosine distance. ``query`` is an NCT id (its stored embedding)
    or a 768-d vector. Returns ``[(nct_id, cosine_distance), ...]`` ascending, excluding ``exclude``
    and the query trial itself."""
    skip = list(exclude)
    if isinstance(query, str):
        skip.append(query)
        sub, q = "(SELECT embedding FROM trial_embeddings WHERE nct_id = %s)", query
    else:
        sub, q = "%s::vector", np.asarray(query, dtype=np.float32)
    with get_conn() as c:
        rows = c.execute(
            f"SELECT nct_id, embedding <=> {sub} AS d FROM trial_embeddings WHERE nct_id <> ALL(%s) "
            f"ORDER BY embedding <=> {sub} LIMIT %s",
            (q, skip, q, k),
        ).fetchall()
    return [(r[0], float(r[1])) for r in rows]
