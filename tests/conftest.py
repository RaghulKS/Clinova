"""Shared test helpers: the contract fixtures in tests/fixtures/."""

import json
import os
import socket
import tempfile
import threading
from pathlib import Path

# Tests never see the shared trained bundles in <main clone>/models: the API keeps its fixture
# fallback and results do not depend on what was trained on this machine (T22 contract change 2).
_TEST_ROOT = tempfile.TemporaryDirectory(prefix="clinova-tests-")
os.environ["MODELS_DIR"] = str(Path(_TEST_ROOT.name) / "models")
os.environ["DATA_DIR"] = str(Path(_TEST_ROOT.name) / "data")
os.environ["CLINOVA_MODE"] = "demo"
os.environ["CLINOVA_API_NO_DB"] = "1"

import psycopg  # noqa: E402
import pytest  # noqa: E402
from pydantic import TypeAdapter  # noqa: E402

from clinova.common.config import Settings, get_settings  # noqa: E402
from clinova.contracts import Funnel, ParsedProtocol, Persona  # noqa: E402

# Tests must not inherit a developer's paid API key or production database from .env.
Settings.model_config["env_file"] = ()
os.environ.pop("GEMINI_API_KEY", None)
os.environ["DATABASE_URL"] = "postgresql://test:test@localhost:1/clinova_test"
get_settings.cache_clear()

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail even when application fallback code catches a forbidden network attempt."""
    attempts = []
    internal = threading.local()
    socketpair = socket.socketpair

    def local_pair(*args, **kwargs):
        # Windows implements asyncio's local wakeup pipe with loopback TCP sockets.
        internal.socketpair = True
        try:
            return socketpair(*args, **kwargs)
        finally:
            internal.socketpair = False

    def deny(*args, **kwargs):
        attempts.append("network")
        raise AssertionError("Tests must use fixtures, recorded responses or a fake database")

    for name in ("connect", "connect_ex", "sendto", "sendmsg"):
        if not hasattr(socket.socket, name):
            continue
        original = getattr(socket.socket, name)

        def guarded(sock, *args, _original=original, **kwargs):
            if sock.family in (socket.AF_INET, socket.AF_INET6) and not getattr(internal, "socketpair", False):
                return deny()
            return _original(sock, *args, **kwargs)

        monkeypatch.setattr(socket.socket, name, guarded)
    monkeypatch.setattr(socket, "socketpair", local_pair)
    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr"):
        monkeypatch.setattr(socket, name, deny)
    # libpq opens sockets in native code, bypassing Python's socket methods.
    monkeypatch.setattr(psycopg, "connect", deny)
    monkeypatch.setattr(psycopg.Connection, "connect", deny)
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", deny)
    get_settings.cache_clear()
    yield attempts
    get_settings.cache_clear()
    assert not attempts, "A test attempted network access, possibly hidden by a fallback"


def pytest_sessionfinish(session, exitstatus):
    _TEST_ROOT.cleanup()


def load_fixture(name: str):
    """Raw fixture by file name: parsed JSON for .json files, text otherwise."""
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return json.loads(text) if name.endswith(".json") else text


@pytest.fixture
def parsed_protocol() -> ParsedProtocol:
    return ParsedProtocol.model_validate(load_fixture("parsed_protocol.json"))


@pytest.fixture
def personas() -> list[Persona]:
    return TypeAdapter(list[Persona]).validate_python(load_fixture("personas.json"))


@pytest.fixture
def funnel() -> Funnel:
    return Funnel.model_validate(load_fixture("funnel.json"))
