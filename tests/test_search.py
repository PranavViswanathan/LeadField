"""Behavior tests for search result parsing and helpers.

The live Google query is not exercised; instead the parsing layer is driven with
fake result objects so the deterministic behavior (name cleaning, directory
detection, dedupe) is covered without network access.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from tasks import search
from tasks.models import Business


@dataclass
class FakeResult:
    """Mimics a googlesearch advanced result object."""

    url: str | None
    title: str | None
    description: str | None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.yelp.com/biz/joes", True),
        ("https://facebook.com/joespizza", True),
        ("https://maps.google.com/?cid=1", True),
        ("https://joespizza.com/menu", False),
        ("https://www.joespizza.co.uk", False),
        (None, False),
        ("", False),
    ],
)
def test_is_directory_url(url: str | None, expected: bool) -> None:
    assert search.is_directory_url(url) is expected


@pytest.mark.parametrize(
    ("title", "url", "expected"),
    [
        ("Joe's Pizza | Yelp", "https://joespizza.com", "Joe's Pizza"),
        ("Maria's Tacos - Home", "https://x.com", "Maria's Tacos"),
        (None, "https://acme-dental.com", "Acme Dental"),
        ("", "https://acme-dental.com", "Acme Dental"),
        (None, None, "Unknown Business"),
    ],
)
def test_clean_business_name(title: str | None, url: str | None, expected: str) -> None:
    assert search._clean_business_name(title, url) == expected


def test_to_business_maps_fields() -> None:
    result = FakeResult(
        url="https://joespizza.com",
        title="Joe's Pizza | Yelp",
        description="Great pizza",
    )
    business = search._to_business(
        result, category="restaurants", query="restaurants in Austin, TX"
    )
    assert business.name == "Joe's Pizza"
    assert business.url == "https://joespizza.com"
    assert business.description == "Great pizza"
    assert business.search_category == "restaurants"
    assert business.source_query == "restaurants in Austin, TX"


def test_dedupe_removes_same_registered_domain() -> None:
    businesses = [
        Business(name="A", url="https://joespizza.com", source_query="q", search_category="r"),
        Business(name="B", url="https://www.joespizza.com/menu", source_query="q", search_category="r"),
        Business(name="C", url="https://mariastacos.com", source_query="q", search_category="r"),
    ]
    unique = search._dedupe(businesses)
    assert len(unique) == 2
    assert {b.name for b in unique} == {"A", "C"}


def test_search_category_handles_library_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("rate limited")

    monkeypatch.setattr(search, "google_search", boom)
    results = search.search_category("restaurants", "restaurants in Austin, TX")
    assert results == []


def test_search_category_parses_results(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = [
        FakeResult("https://joespizza.com", "Joe's Pizza", "pizza"),
        FakeResult("https://mariastacos.com", "Maria's Tacos", "tacos"),
    ]
    monkeypatch.setattr(search, "google_search", lambda *a, **k: fake)
    results = search.search_category("restaurants", "restaurants in Austin, TX")
    assert [b.name for b in results] == ["Joe's Pizza", "Maria's Tacos"]


def test_search_all_throttles_between_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(search.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(
        search,
        "google_search",
        lambda *a, **k: [FakeResult("https://x-unique.com", "X", "d")],
    )
    from config import get_settings

    settings = get_settings().model_copy(
        update={"categories": ["restaurants", "gyms"], "search_delay_seconds": 1.5}
    )
    search.search_all(settings=settings)
    # Sleeps happen between queries only: 2 categories -> 1 sleep.
    assert sleeps == [1.5]
