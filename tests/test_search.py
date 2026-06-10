"""Behavior tests for search result parsing and both search backends.

No live network query is exercised: the Google backend is driven with fake
result objects, and the DuckDuckGo backend is driven with fixture HTML, so the
deterministic behavior (name cleaning, directory detection, dedupe, redirect
decoding, throttling) is covered without network access.
"""

from __future__ import annotations

import pytest

from config import get_settings
from tasks import search
from tasks.models import Business
from tasks.search import SearchResult


def _google_settings():
    return get_settings().model_copy(update={"search_backend": "google"})


def _ddg_settings():
    return get_settings().model_copy(update={"search_backend": "duckduckgo"})


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.yelp.com/biz/joes", True),
        ("https://facebook.com/joespizza", True),
        ("https://maps.google.com/?cid=1", True),
        ("https://duckduckgo.com/l/?uddg=x", True),
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
    result = SearchResult(
        url="https://joespizza.com",
        title="Joe's Pizza | Yelp",
        description="Great pizza",
    )
    business = search._to_business(
        result, category="restaurants", query="restaurants in Boston, MA"
    )
    assert business.name == "Joe's Pizza"
    assert business.url == "https://joespizza.com"
    assert business.description == "Great pizza"
    assert business.search_category == "restaurants"
    assert business.source_query == "restaurants in Boston, MA"


def test_dedupe_removes_same_registered_domain() -> None:
    businesses = [
        Business(name="A", url="https://joespizza.com", source_query="q", search_category="r"),
        Business(name="B", url="https://www.joespizza.com/menu", source_query="q", search_category="r"),
        Business(name="C", url="https://mariastacos.com", source_query="q", search_category="r"),
    ]
    unique = search._dedupe(businesses)
    assert len(unique) == 2
    assert {b.name for b in unique} == {"A", "C"}


# --- DuckDuckGo backend ------------------------------------------------------

_DDG_HTML = """
<html><body>
  <div class="result results_links_deep result--ad">
    <a class="result__a" href="//duckduckgo.com/y.js?ad=1">Sponsored</a>
  </div>
  <div class="result">
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fbostonpizza.com%2Fmenu&rut=abc">Boston Pizza Co</a>
    <a class="result__snippet">The best slices downtown.</a>
  </div>
  <div class="result">
    <a class="result__a" href="https://mariastacos.com">Maria's Tacos</a>
    <a class="result__snippet">Authentic tacos.</a>
  </div>
</body></html>
"""


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        ("//duckduckgo.com/l/?uddg=https%3A%2F%2Fjoes.com%2F&rut=x", "https://joes.com/"),
        ("https://direct.example.com/path", "https://direct.example.com/path"),
        ("//duckduckgo.com/l/?uddg=https%3A%2F%2Fa.com%3Fq%3D1", "https://a.com?q=1"),
    ],
)
def test_decode_ddg_href(href: str, expected: str) -> None:
    assert search._decode_ddg_href(href) == expected


def test_parse_ddg_skips_ads_and_decodes_links() -> None:
    results = search._parse_ddg(_DDG_HTML, max_results=10)
    assert [r.url for r in results] == [
        "https://bostonpizza.com/menu",
        "https://mariastacos.com",
    ]
    assert results[0].title == "Boston Pizza Co"
    assert results[0].description == "The best slices downtown."


def test_parse_ddg_respects_max_results() -> None:
    results = search._parse_ddg(_DDG_HTML, max_results=1)
    assert len(results) == 1


def test_search_category_duckduckgo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(search, "_fetch_ddg_html", lambda *a, **k: _DDG_HTML)
    results = search.search_category(
        "restaurants", "restaurants in Boston, MA", settings=_ddg_settings()
    )
    assert [b.name for b in results] == ["Boston Pizza Co", "Maria's Tacos"]
    assert results[0].url == "https://bostonpizza.com/menu"


def test_search_category_duckduckgo_handles_fetch_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    def boom(*_a: object, **_k: object) -> object:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(search, "_fetch_ddg_html", boom)
    results = search.search_category(
        "restaurants", "restaurants in Boston, MA", settings=_ddg_settings()
    )
    assert results == []


# --- OpenStreetMap / Overpass backend ----------------------------------------


def _overpass_settings():
    return get_settings().model_copy(
        update={"search_backend": "overpass", "location": "Boston, MA"}
    )


_OVERPASS_DATA = {
    "elements": [
        {
            "type": "node",
            "tags": {
                "name": "Flour Bakery",
                "amenity": "cafe",
                "cuisine": "bakery",
                "website": "flourbakery.com",
            },
        },
        {
            "type": "node",
            "tags": {"name": "Corner Diner", "amenity": "restaurant"},
        },
        {  # no name -> skipped
            "type": "node",
            "tags": {"amenity": "restaurant"},
        },
        {  # duplicate name -> skipped
            "type": "way",
            "tags": {"name": "Flour Bakery", "amenity": "cafe"},
        },
    ]
}


@pytest.mark.parametrize(
    ("category", "expected_selector"),
    [
        ("restaurants", ("amenity", "restaurant")),
        ("dentists", ("amenity", "dentist")),
        ("law firms", ("office", "lawyer")),
        ("hair salons", ("shop", "hairdresser")),
        ("gyms", ("leisure", "fitness_centre")),
        ("plumbers", ("craft", "plumber")),
    ],
)
def test_osm_filters_for_category(category: str, expected_selector: tuple) -> None:
    assert expected_selector in search._osm_filters_for_category(category)


def test_osm_filters_fallback_for_unknown_category() -> None:
    assert search._osm_filters_for_category("widgets") == (("shop", ""),)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("flourbakery.com", "https://flourbakery.com"),
        ("http://x.com", "http://x.com"),
        ("https://y.com", "https://y.com"),
        ("  spaced.com  ", "https://spaced.com"),
    ],
)
def test_normalize_url(raw: str, expected: str) -> None:
    assert search._normalize_url(raw) == expected


def test_overpass_query_builds_selectors() -> None:
    query = search._overpass_query(
        (("amenity", "restaurant"), ("shop", "")), (42.0, -71.2, 42.4, -70.9)
    )
    assert '["amenity"="restaurant"]["name"](42.0,-71.2,42.4,-70.9);' in query
    assert '["shop"]["name"](42.0,-71.2,42.4,-70.9);' in query
    assert query.startswith("[out:json]")


def test_parse_overpass_maps_names_and_websites() -> None:
    results = search._parse_overpass(_OVERPASS_DATA, max_results=10)
    assert [r.title for r in results] == ["Flour Bakery", "Corner Diner"]
    assert results[0].url == "https://flourbakery.com"
    assert results[1].url is None  # no website tag -> build-site lead


def test_search_category_overpass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        search, "_geocode_bbox", lambda *a, **k: (42.0, -71.2, 42.4, -70.9)
    )
    monkeypatch.setattr(search, "_fetch_overpass", lambda *a, **k: _OVERPASS_DATA)
    results = search.search_category(
        "restaurants", "restaurants in Boston, MA", settings=_overpass_settings()
    )
    assert [b.name for b in results] == ["Flour Bakery", "Corner Diner"]
    assert results[1].has_website is False


def test_search_category_overpass_handles_geocode_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(search, "_geocode_bbox", lambda *a, **k: None)
    results = search.search_category(
        "restaurants", "restaurants in Nowhere", settings=_overpass_settings()
    )
    assert results == []


# --- Google backend ----------------------------------------------------------


def test_search_category_google_handles_library_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("rate limited")

    monkeypatch.setattr(search, "google_search", boom)
    results = search.search_category(
        "restaurants", "restaurants in Boston, MA", settings=_google_settings()
    )
    assert results == []


def test_search_category_google_parses_results(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = [
        SearchResult("https://joespizza.com", "Joe's Pizza", "pizza"),
        SearchResult("https://mariastacos.com", "Maria's Tacos", "tacos"),
    ]
    monkeypatch.setattr(search, "google_search", lambda *a, **k: fake)
    results = search.search_category(
        "restaurants", "restaurants in Boston, MA", settings=_google_settings()
    )
    assert [b.name for b in results] == ["Joe's Pizza", "Maria's Tacos"]


def test_search_all_throttles_between_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(search.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(
        search,
        "google_search",
        lambda *a, **k: [SearchResult("https://x-unique.com", "X", "d")],
    )
    settings = get_settings().model_copy(
        update={
            "search_backend": "google",
            "categories": ["restaurants", "gyms"],
            "search_delay_seconds": 1.5,
        }
    )
    search.search_all(settings=settings)
    # Sleeps happen between queries only: 2 categories -> 1 sleep.
    assert sleeps == [1.5]
