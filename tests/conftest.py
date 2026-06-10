"""Shared pytest fixtures and factory functions for the lead gen test suite.

Tests follow the project convention of building data through factory functions
rather than module-level mutable state, and exercise behavior through the public
API of each module.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from config import Settings, get_settings
from tasks.models import Business, Email


def make_business(**overrides: object) -> Business:
    """Build a :class:`Business` with sensible defaults, overridable per field."""
    defaults: dict[str, object] = {
        "name": "Joe's Pizza",
        "url": "https://joespizza.com",
        "title": "Joe's Pizza | Best in Town",
        "description": "Authentic wood-fired pizza in Austin.",
        "source_query": "restaurants in Austin, TX",
        "search_category": "restaurants",
    }
    defaults.update(overrides)
    return Business(**defaults)


def make_email(**overrides: object) -> Email:
    """Build an :class:`Email` with sensible defaults, overridable per field."""
    defaults: dict[str, object] = {
        "business_name": "Joe's Pizza",
        "business_url": "https://joespizza.com",
        "category": "restaurant",
        "email_type": "improve_site",
        "subject": "A few ideas for your site",
        "body": "Hi Joe, ...",
        "model": "llama3.2",
    }
    defaults.update(overrides)
    return Email(**defaults)


@pytest.fixture
def tmp_settings() -> Iterator[Settings]:
    """Settings pointed at an isolated temp SQLite DB."""
    with tempfile.TemporaryDirectory() as tmp:
        base = get_settings()
        yield base.model_copy(update={"database_path": Path(tmp) / "leads.db"})


@pytest.fixture
def ollama_settings() -> Settings:
    """Settings with deterministic Ollama URLs/models for mocking."""
    return get_settings().model_copy(
        update={
            "ollama_base_url": "http://ollama.test:11434",
            "ollama_model": "primary-model",
            "ollama_fallback_model": "fallback-model",
        }
    )
