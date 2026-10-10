"""Shared test helpers: the contract fixtures in tests/fixtures/."""

import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from clinova.contracts import Funnel, ParsedProtocol, Persona

FIXTURES = Path(__file__).parent / "fixtures"


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
