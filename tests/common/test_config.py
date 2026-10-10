from clinova.common.config import REPO_ROOT, Settings, amendment_cost, get_settings

KEYS = ["GEMINI_API_KEY", "DATABASE_URL", "NTFY_TOPIC", "MAX_GEMINI_CALLS", "GEMINI_CONCURRENCY",
        "CLINOVA_MODE", "DEMO_NCT_ID", "SKIP_MIROFISH"]


def test_amendment_cost(monkeypatch):
    monkeypatch.delenv("AMENDMENT_COST_PHASE2", raising=False)
    monkeypatch.delenv("AMENDMENT_COST_PHASE3", raising=False)
    get_settings.cache_clear()
    try:
        assert amendment_cost("phase2") == 141_000
        assert amendment_cost("phase3") == 535_000
        assert amendment_cost("phase2_3") == 535_000
        assert amendment_cost(None) == 141_000
        assert amendment_cost("weird") == 141_000
        assert get_settings().AMENDMENT_DELAY_MONTHS == 3
    finally:
        get_settings.cache_clear()


def test_blank_values_and_inline_comments_behave_as_unset(monkeypatch):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=REPO_ROOT / ".env.example")
    assert s.GEMINI_API_KEY is None
    assert s.DEMO_NCT_ID is None
    assert s.SKIP_MIROFISH is False
    assert s.NTFY_TOPIC is None
    assert s.DATABASE_URL is not None  # blank falls back to the local docker default
    assert s.MAX_GEMINI_CALLS == 12000
    assert s.GEMINI_CONCURRENCY == 8
    assert s.CLINOVA_MODE == "live"  # "live   # comment" -> "live"


def test_env_values_parse(monkeypatch, tmp_path):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    env = tmp_path / ".env"
    env.write_text("SKIP_MIROFISH=1\nDEMO_NCT_ID=NCT01234567\nUNKNOWN_KEY=x\nGEMINI_API_KEY=abc\n")
    s = Settings(_env_file=env)
    assert s.SKIP_MIROFISH is True
    assert s.DEMO_NCT_ID == "NCT01234567"
    assert s.GEMINI_API_KEY.get_secret_value() == "abc"
    assert s.CLINOVA_MODE == "demo"
    assert "abc" not in repr(s)
