"""Behavior tests for the shared pipeline orchestrator."""

from __future__ import annotations

import pytest

import pipeline
from tasks import email_generator, ollama_client, search, storage
from tests.conftest import make_business


@pytest.fixture
def fake_search(monkeypatch: pytest.MonkeyPatch) -> list:
    """Replace the live search with two no-website businesses (no network)."""
    businesses = [
        make_business(name="Alpha", url=None, search_category="restaurants"),
        make_business(name="Beta", url=None, search_category="gyms"),
    ]
    monkeypatch.setattr(search, "search_all", lambda settings=None: businesses)
    return businesses


@pytest.fixture(autouse=True)
def fake_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid real Ollama calls during pipeline tests."""
    monkeypatch.setattr(
        email_generator.ollama_client,
        "generate",
        lambda *a, **k: ollama_client.GenerationResult(
            text="SUBJECT: Hi\nBODY: Hello there", model="test-model"
        ),
    )


def test_run_pipeline_persists_and_reports_progress(tmp_settings, fake_search) -> None:
    stages: list[str] = []
    result = pipeline.run_pipeline(
        tmp_settings, progress=lambda stage, *_: stages.append(stage)
    )

    assert result == {"businesses": 2, "emails": 2}
    assert stages[0] == "search"
    assert stages[-1] == "done"
    assert {"audit", "draft"}.issubset(set(stages))
    assert len(storage.fetch_emails(settings=tmp_settings)) == 2


def test_run_pipeline_limit_caps_businesses(tmp_settings, fake_search) -> None:
    result = pipeline.run_pipeline(tmp_settings, limit=1)
    assert result["businesses"] == 1
    assert len(storage.fetch_emails(settings=tmp_settings)) == 1


def test_interleave_spreads_limit_across_categories() -> None:
    # 3 restaurants then 3 gyms; a limit of 2 must not be all restaurants.
    businesses = [
        make_business(name=f"R{i}", url=None, search_category="restaurants")
        for i in range(3)
    ] + [
        make_business(name=f"G{i}", url=None, search_category="gyms") for i in range(3)
    ]
    interleaved = pipeline._interleave_by_category(businesses)
    categories = [b.search_category for b in interleaved[:2]]
    assert set(categories) == {"restaurants", "gyms"}
    assert len(interleaved) == 6  # nothing dropped


def test_run_pipeline_reset_clears_previous_results(tmp_settings, fake_search) -> None:
    pipeline.run_pipeline(tmp_settings)
    assert len(storage.fetch_emails(settings=tmp_settings)) == 2

    # A reset run with a single new business should leave only that one.
    pipeline.run_pipeline(tmp_settings, limit=1, reset=True)
    assert len(storage.fetch_emails(settings=tmp_settings)) == 1


def test_clear_all_empties_both_tables(tmp_settings) -> None:
    storage.save_businesses(
        [make_business(name="X", url=None)], settings=tmp_settings
    )
    storage.clear_all(settings=tmp_settings)
    assert storage.fetch_emails(settings=tmp_settings) == []
