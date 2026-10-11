"""llm.py with the DB seams and the Gemini client replaced by in-memory fakes. No network, no DB."""

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from clinova.common import llm
from clinova.common.config import get_settings


class Answer(BaseModel):
    label: str
    score: float


class FakeModels:
    def __init__(self):
        self.replies: list[str] = []
        self.prompts: list[str] = []
        self.embedded: list[list[str]] = []

    def generate_content(self, *, model, contents, config):
        assert config.response_mime_type == "application/json" and config.seed == 7
        self.prompts.append(contents)
        usage = SimpleNamespace(prompt_token_count=10, candidates_token_count=5, thoughts_token_count=None)
        return SimpleNamespace(text=self.replies.pop(0), usage_metadata=usage)

    def embed_content(self, *, model, contents, config):
        texts = [c.parts[0].text for c in contents]
        self.embedded.append(texts)
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[float(len(t)), float(config.output_dimensionality)])
                                           for t in texts])


def _set_env(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()


@pytest.fixture
def fake(monkeypatch):
    cache, usage, count = {}, [], {"n": 0}
    models = FakeModels()
    monkeypatch.setattr(llm, "_cache_get", lambda keys: {k: cache[k] for k in keys if k in cache})
    monkeypatch.setattr(llm, "_cache_put", lambda rows: [cache.setdefault(r[0], r[4]) for r in rows])
    monkeypatch.setattr(llm, "_cache_scan", lambda *a: [])
    monkeypatch.setattr(llm, "_usage_count", lambda fp: count["n"] + len(usage))
    monkeypatch.setattr(llm, "_usage_add", lambda *row: usage.append(row))
    monkeypatch.setattr(llm, "_client", lambda: SimpleNamespace(models=models))
    _set_env(monkeypatch, CLINOVA_MODE="live", GEMINI_API_KEY="test-key-abcdef", MAX_GEMINI_CALLS="100",
             GEMINI_CONCURRENCY="2", EMBED_DIM="768")
    yield SimpleNamespace(cache=cache, usage=usage, count=count, models=models)
    get_settings.cache_clear()


def test_demo_miss_raises_and_never_builds_client(fake, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    _set_env(monkeypatch, CLINOVA_MODE="demo")
    monkeypatch.setattr(llm, "_client", lambda: pytest.fail("client built in demo mode"))
    with pytest.raises(llm.CacheMiss, match="ns1"):
        llm.generate_json("q", Answer, namespace="ns1", seed=7)
    with pytest.raises(llm.CacheMiss, match="ns2"):
        llm.embed(["a"], namespace="ns2")
    assert fake.usage == []


def test_demo_accepts_prompt_differing_only_in_float_noise(fake, monkeypatch):
    _set_env(monkeypatch, CLINOVA_MODE="demo")
    request = {"prompt": "crcl 97.91688470963422, age 59", "schema": "Answer", "seed": 7, "temperature": 0.2}
    monkeypatch.setattr(llm, "_cache_scan", lambda *a: [(request, {"label": "yes", "score": 0.9})])
    assert llm.generate_json("crcl 97.9168847096342, age 59", Answer, namespace="ns", seed=7).label == "yes"
    with pytest.raises(llm.CacheMiss):  # a real difference is still a miss
        llm.generate_json("crcl 97.9168847096342, age 60", Answer, namespace="ns", seed=7)


def test_live_miss_calls_once_caches_and_records_usage(fake):
    fake.models.replies = ['{"label": "yes", "score": 0.9}']
    out = llm.generate_json("q", Answer, namespace="ns", seed=7)
    assert out == Answer(label="yes", score=0.9)
    assert fake.models.prompts == ["q"]
    assert list(fake.cache.values()) == [{"label": "yes", "score": 0.9}]
    (ns, model, fp, kind, tin, tout, ok), = fake.usage
    assert (ns, kind, tin, tout, ok) == ("ns", "generate", 10, 5, True)
    assert model == get_settings().GEMINI_MODEL_FAST
    assert fp == llm._fingerprint("test-key-abcdef") and len(fp) == 12


def test_cache_hit_skips_client_in_live_and_demo(fake, monkeypatch):
    fake.models.replies = ['{"label": "yes", "score": 0.9}']
    first = llm.generate_json("q", Answer, namespace="ns", seed=7)
    assert llm.generate_json("q", Answer, namespace="ns", seed=7) == first
    _set_env(monkeypatch, CLINOVA_MODE="demo")
    monkeypatch.setattr(llm, "_client", lambda: pytest.fail("client built on a cache hit"))
    assert llm.generate_json("q", Answer, namespace="ns", seed=7) == first
    assert len(fake.models.prompts) == 1 and len(fake.usage) == 1
    with pytest.raises(llm.CacheMiss):  # seed is part of the key
        llm.generate_json("q", Answer, namespace="ns", seed=8)


def test_one_repair_retry_then_success(fake):
    fake.models.replies = ['{"label": "yes"}', '{"label": "yes", "score": 0.5}']
    assert llm.generate_json("q", Answer, namespace="ns", seed=7).score == 0.5
    assert len(fake.models.prompts) == 2
    assert "Validation error" in fake.models.prompts[1] and '{"label": "yes"}' in fake.models.prompts[1]
    assert len(fake.usage) == 2 and len(fake.cache) == 1


def test_second_invalid_reply_raises_and_caches_nothing(fake):
    fake.models.replies = ["not json", '{"label": 1}']
    with pytest.raises(llm.LLMError, match="repair"):
        llm.generate_json("q", Answer, namespace="ns", seed=7)
    assert fake.cache == {} and len(fake.models.prompts) == 2


def test_budget_exceeded_before_network(fake):
    fake.count["n"] = 100
    with pytest.raises(llm.BudgetExceeded):
        llm.generate_json("q", Answer, namespace="ns", seed=7)
    with pytest.raises(llm.BudgetExceeded):
        llm.embed(["a"], namespace="ns")
    assert fake.models.prompts == [] and fake.models.embedded == [] and fake.usage == []


def test_embed_keeps_order_and_requests_only_uncached(fake):
    assert llm.embed(["bb"], namespace="emb") == [[2.0, 768.0]]
    out = llm.embed(["a", "bb", "cccc", "a"], namespace="emb")
    assert out == [[1.0, 768.0], [2.0, 768.0], [4.0, 768.0], [1.0, 768.0]]
    assert fake.models.embedded == [["bb"], ["a", "cccc"]]
    assert [row[3] for row in fake.usage] == ["embed", "embed"]


def test_embed_batches_of_100(fake):
    texts = [f"t{i}" for i in range(250)]
    assert len(llm.embed(texts, namespace="emb", dim=128)) == 250
    assert [len(b) for b in fake.models.embedded] == [100, 100, 50]


async def test_async_variants(fake):
    fake.models.replies = ['{"label": "a", "score": 1}']
    assert (await llm.agenerate_json("q", Answer, namespace="ns", seed=7)).label == "a"
    assert await llm.aembed(["x"], namespace="emb") == [[1.0, 768.0]]
    with pytest.raises(RuntimeError, match="agenerate_json"):
        llm.generate_json("q", Answer, namespace="ns", seed=7)
