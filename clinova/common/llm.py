"""The only Gemini entry point in the repo. Never import google.genai anywhere else.

    generate_json(prompt, Schema, namespace="parse", seed=42) -> Schema instance
    embed(texts, namespace="trials") -> list of vectors, in input order
    (agenerate_json / aembed are the async versions)

Every response is cached in Postgres `llm_cache`. Demo mode (CLINOVA_MODE=demo) is
cache-only: a miss raises CacheMiss and nothing touches the network. Live mode calls
Gemini with a per-event-loop concurrency limit, backoff on 429/5xx, one `llm_usage`
row per network request, and a hard stop (BudgetExceeded) once the API key's request
count reaches MAX_GEMINI_CALLS.

Gemini Embedding 2 has no task_type parameter; for retrieval, callers prefix texts
themselves (e.g. "task: search result | query: ..." or "title: none | text: ...").
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import weakref
from collections.abc import Coroutine, Sequence
from functools import lru_cache
from typing import Any, TypeVar

import httpx
from google import genai
from google.genai import errors, types
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ValidationError
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_random_exponential

from clinova.common.config import get_settings
from clinova.common.db import get_conn

T = TypeVar("T", bound=BaseModel)
EMBED_BATCH = 100  # Gemini batchEmbedContents limit


class LLMError(Exception):
    """Base class for every error raised by this module."""


class CacheMiss(LLMError):
    """Demo mode needed a response that is not in llm_cache."""


class BudgetExceeded(LLMError):
    """This API key already made MAX_GEMINI_CALLS requests."""


# ---- private seams (tests monkeypatch these) -------------------------------------------


@lru_cache(maxsize=1)
def _client() -> genai.Client:
    key = get_settings().GEMINI_API_KEY
    if key is None:
        raise LLMError("CLINOVA_MODE=live needs GEMINI_API_KEY")
    return genai.Client(api_key=key.get_secret_value())


def _cache_get(keys: Sequence[str]) -> dict[str, Any]:
    with get_conn() as conn:
        rows = conn.execute("SELECT key, response FROM llm_cache WHERE key = ANY(%s)", (list(keys),))
        return dict(rows.fetchall())


def _cache_scan(namespace: str, model: str, seed: int, schema_name: str) -> list[tuple[Any, Any]]:
    """(request, response) of every cached generate call with this namespace, model, seed and schema name."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT request, response FROM llm_cache WHERE namespace = %s AND model = %s "
            "AND request->>'seed' = %s AND request->>'schema' = %s",
            (namespace, model, str(seed), schema_name),
        ).fetchall()


def _cache_put(rows: Sequence[tuple[str, str, str, Any, Any]]) -> None:
    """rows: (key, namespace, model, request, response)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO llm_cache (key, namespace, model, request, response) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (key) DO NOTHING",
            [(k, ns, m, Jsonb(req), Jsonb(resp)) for k, ns, m, req, resp in rows],
        )


def _usage_count(fingerprint: str) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT count(*) FROM llm_usage WHERE key_fingerprint = %s", (fingerprint,)
        ).fetchone()[0]


def _usage_add(namespace: str, model: str, fingerprint: str, kind: str,
               input_tokens: int, output_tokens: int, ok: bool) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO llm_usage (namespace, model, key_fingerprint, kind, input_tokens, output_tokens, ok) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (namespace, model, fingerprint, kind, input_tokens, output_tokens, ok),
        )


# ---- helpers ------------------------------------------------------------------------------


def _key(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


_LONG_FLOAT = re.compile(r"\d+\.\d{6,}")


def _loose(prompt: str) -> str:
    """Prompt with long float literals cut to 10 significant digits. Unrounded floats (e.g. persona lab values)
    differ in the last digit between platforms' math libraries, which changes the exact cache key."""
    return _LONG_FLOAT.sub(lambda m: f"{float(m.group()):.10g}", prompt)


def _fingerprint(api_key: str) -> str:
    return hashlib.sha256(api_key[-6:].encode("utf-8")).hexdigest()[:12]


def _retryable(e: BaseException) -> bool:
    if isinstance(e, errors.APIError):
        return e.code == 429 or (e.code or 0) >= 500
    return isinstance(e, httpx.TransportError)


# ponytail: one semaphore per event loop, so sync calls from many threads (each its own
# asyncio.run loop) are not limited together. Use agenerate_json/aembed for bulk work.
_sems: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = weakref.WeakKeyDictionary()


def _sem() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if loop not in _sems:
        _sems[loop] = asyncio.Semaphore(get_settings().GEMINI_CONCURRENCY)
    return _sems[loop]


async def _call(kind: str, namespace: str, model: str, method: str, **kwargs: Any) -> Any:
    """One logical Gemini request: budget check, concurrency limit, retries, a usage row per attempt.

    The sync SDK client runs in a worker thread, so no HTTP session is tied to an event loop.
    """
    s = get_settings()
    if s.GEMINI_API_KEY is None:
        raise LLMError("CLINOVA_MODE=live needs GEMINI_API_KEY")
    fp = _fingerprint(s.GEMINI_API_KEY.get_secret_value())
    # Hold the Client itself: genai.Client.__del__ closes its HTTP session, and lru_cache can build
    # a duplicate under thread races that is garbage-collected while `fn` is still in use.
    client = _client()
    fn = getattr(client.models, method)
    async with _sem():
        async for attempt in AsyncRetrying(
            retry=retry_if_exception(_retryable),
            wait=wait_random_exponential(multiplier=1, max=60),
            stop=stop_after_attempt(6),
            reraise=True,
        ):
            with attempt:
                # ponytail: up to GEMINI_CONCURRENCY in-flight requests can pass the check together.
                if await asyncio.to_thread(_usage_count, fp) >= s.MAX_GEMINI_CALLS:
                    raise BudgetExceeded(f"API key {fp} reached MAX_GEMINI_CALLS={s.MAX_GEMINI_CALLS}")
                resp, ok = None, False
                try:
                    resp = await asyncio.to_thread(fn, model=model, **kwargs)
                    ok = True
                    return resp
                finally:
                    u = getattr(resp, "usage_metadata", None)
                    tokens_in = (getattr(u, "prompt_token_count", None) or 0) if u else 0
                    tokens_out = ((getattr(u, "candidates_token_count", None) or 0)
                                  + (getattr(u, "thoughts_token_count", None) or 0)) if u else 0
                    await asyncio.to_thread(_usage_add, namespace, model, fp, kind, tokens_in, tokens_out, ok)


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    coro.close()
    raise RuntimeError("sync llm call inside a running event loop; use agenerate_json / aembed")


# ---- public API ---------------------------------------------------------------------------


async def agenerate_json(prompt: str, schema: type[T], *, namespace: str, seed: int,
                         temperature: float = 0.2) -> T:
    """Return a validated `schema` instance for `prompt` (cached; see module docstring)."""
    s = get_settings()
    model = s.GEMINI_MODEL_FAST
    json_schema = schema.model_json_schema()
    key = _key(namespace, model, prompt, json.dumps(json_schema, sort_keys=True, separators=(",", ":")), str(seed))

    hit = (await asyncio.to_thread(_cache_get, [key])).get(key)
    if hit is not None:
        return schema.model_validate(hit)
    if s.CLINOVA_MODE == "demo":
        # The cache was filled on Windows; a Linux container computes some floats 1 ulp apart. Accept a cached
        # response whose prompt matches after rounding long floats (demo mode only; live mode keys stay exact).
        # ponytail: scans the namespace per miss; fine for the demo cache, index on a loose key if it grows.
        want = _loose(prompt)
        rows = await asyncio.to_thread(_cache_scan, namespace, model, seed, schema.__name__)
        for request, response in rows:
            if request.get("temperature") == temperature and _loose(request.get("prompt", "")) == want:
                return schema.model_validate(response)
        raise CacheMiss(f"demo mode: no cached response in namespace {namespace!r}")

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_json_schema=json_schema,
        seed=seed,
        temperature=temperature,
    )
    text = (await _call("generate", namespace, model, "generate_content", contents=prompt, config=config)).text or ""
    try:
        obj = schema.model_validate_json(text)
    except ValidationError as err:
        repair = (
            f"{prompt}\n\nYour previous reply did not match the required JSON schema.\n"
            f"Validation error:\n{err}\n\nPrevious reply:\n{text}\n\n"
            "Reply again with only corrected JSON that matches the schema."
        )
        text = (await _call("generate", namespace, model, "generate_content", contents=repair, config=config)).text or ""
        try:
            obj = schema.model_validate_json(text)
        except ValidationError as err2:
            raise LLMError(f"{namespace}: invalid JSON after one repair retry: {err2}") from err2

    request = {"prompt": prompt, "schema": schema.__name__, "seed": seed, "temperature": temperature}
    await asyncio.to_thread(_cache_put, [(key, namespace, model, request, obj.model_dump(mode="json"))])
    return obj


def generate_json(prompt: str, schema: type[T], *, namespace: str, seed: int, temperature: float = 0.2) -> T:
    return _run(agenerate_json(prompt, schema, namespace=namespace, seed=seed, temperature=temperature))


async def aembed(texts: Sequence[str], *, namespace: str, dim: int | None = None) -> list[list[float]]:
    """Embed `texts` (dim defaults to settings.EMBED_DIM); vectors come back in input order."""
    s = get_settings()
    model, dim = s.GEMINI_EMBED_MODEL, dim or s.EMBED_DIM
    keys = [_key(namespace, model, t, str(dim)) for t in texts]
    text_of = dict(zip(keys, texts, strict=True))
    found = await asyncio.to_thread(_cache_get, list(text_of)) if keys else {}
    missing = [k for k in text_of if k not in found]
    if missing and s.CLINOVA_MODE == "demo":
        raise CacheMiss(f"demo mode: {len(missing)} uncached embeddings in namespace {namespace!r}")

    config = types.EmbedContentConfig(output_dimensionality=dim)
    # ponytail: batches go one after another; gather them if embedding ever becomes the bottleneck.
    for i in range(0, len(missing), EMBED_BATCH):
        batch = missing[i:i + EMBED_BATCH]
        # One Content per text: a plain list of strings is aggregated into ONE embedding by Gemini Embedding 2.
        contents = [types.Content(parts=[types.Part(text=text_of[k])]) for k in batch]
        resp = await _call("embed", namespace, model, "embed_content", contents=contents, config=config)
        vectors = [list(e.values) for e in (resp.embeddings or [])]
        if len(vectors) != len(batch):
            raise LLMError(f"{namespace}: asked for {len(batch)} embeddings, got {len(vectors)}")
        found.update(zip(batch, vectors, strict=True))
        await asyncio.to_thread(
            _cache_put, [(k, namespace, model, {"text": text_of[k], "dim": dim}, v) for k, v in zip(batch, vectors, strict=True)]
        )
    return [found[k] for k in keys]


def embed(texts: Sequence[str], *, namespace: str, dim: int | None = None) -> list[list[float]]:
    return _run(aembed(texts, namespace=namespace, dim=dim))
