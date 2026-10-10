"""Eligibility embeddings and similar-trial search (owner: T12). Table ``trial_embeddings``."""

from __future__ import annotations

from collections.abc import Sequence


def embed_trials(nct_ids: Sequence[str] | None = None) -> int:
    """Embed each trial's FIRST-VERSION eligibility text with ``clinova.common.llm.embed`` at
    EMBED_DIM (768) and upsert into ``trial_embeddings``. ``None`` = every trial not yet embedded.
    Returns the number of trials embedded."""
    raise NotImplementedError("T12 implements clinova.parse.embed.embed_trials")


def nearest(query: str | Sequence[float], k: int = 10, *, exclude: Sequence[str] = ()) -> list[tuple[str, float]]:
    """Nearest trials by pgvector cosine distance. ``query`` is an NCT id (its stored embedding)
    or a 768-d vector. Returns ``[(nct_id, cosine_distance), ...]`` ascending, excluding ``exclude``
    and the query trial itself."""
    raise NotImplementedError("T12 implements clinova.parse.embed.nearest")
