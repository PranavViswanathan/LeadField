"""Business discovery via web search (no API key required).

Two backends are supported, selected by ``settings.search_backend``:

* ``duckduckgo`` (default) -- scrapes the static HTML endpoint at
  ``html.duckduckgo.com/html/``. Reliable, since DuckDuckGo still serves
  parseable result links without JavaScript.
* ``google`` -- uses the ``googlesearch-python`` library. Note that Google now
  serves a JavaScript-gated page to non-browser clients, so this backend often
  returns no results.

Each backend yields a normalized list of :class:`~tasks.models.Business`. A
configurable delay is inserted between queries to avoid rate limiting.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup
from googlesearch import search as google_search

from config import Settings, get_settings
from tasks.models import Business

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
_DDG_HTML_URL = "https://html.duckduckgo.com/html/"
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# OSM services (Nominatim/Overpass) require a descriptive, non-browser UA and
# reject browser-like agents with a 406.
_OSM_USER_AGENT = "leadfield-leadgen/1.0 (local business lead generation)"

# Domains that are aggregators/directories rather than a business's own site.
DIRECTORY_DOMAINS: frozenset[str] = frozenset(
    {
        "yelp.com",
        "facebook.com",
        "instagram.com",
        "tripadvisor.com",
        "yellowpages.com",
        "mapquest.com",
        "google.com",
        "maps.google.com",
        "foursquare.com",
        "opentable.com",
        "doordash.com",
        "ubereats.com",
        "grubhub.com",
        "zomato.com",
        "linkedin.com",
        "twitter.com",
        "x.com",
        "youtube.com",
        "wikipedia.org",
        "bbb.org",
        "nextdoor.com",
        "angi.com",
        "thumbtack.com",
        "healthgrades.com",
        "zocdoc.com",
        "avvo.com",
        "duckduckgo.com",
    }
)

_TITLE_SUFFIX = re.compile(
    r"\s*[\|\-–—:]\s*(yelp|facebook|instagram|home|official site|menu).*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SearchResult:
    """Backend-agnostic search result before mapping to a :class:`Business`."""

    url: str | None
    title: str | None
    description: str | None


def _registered_domain(url: str | None) -> str:
    """Return the lowercased registered domain (``sub.example.com`` -> ``example.com``)."""
    if not url:
        return ""
    netloc = urlparse(url).netloc.lower()
    netloc = netloc.removeprefix("www.")
    parts = netloc.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return netloc


def is_directory_url(url: str | None) -> bool:
    """Return True if ``url`` points at a known aggregator/directory site."""
    return _registered_domain(url) in DIRECTORY_DOMAINS


def _clean_business_name(title: str | None, fallback_url: str | None) -> str:
    """Derive a readable business name from a result title or URL."""
    if title:
        cleaned = _TITLE_SUFFIX.sub("", title).strip()
        if cleaned:
            return cleaned
    domain = _registered_domain(fallback_url)
    if domain:
        return domain.split(".")[0].replace("-", " ").title()
    return "Unknown Business"


def _to_business(result: SearchResult, *, category: str, query: str) -> Business:
    """Convert a normalized search result into a :class:`Business`."""
    return Business(
        name=_clean_business_name(result.title, result.url),
        url=result.url,
        title=result.title,
        description=result.description,
        source_query=query,
        search_category=category,
    )


# --- DuckDuckGo backend ------------------------------------------------------


def _decode_ddg_href(href: str) -> str:
    """Resolve a DuckDuckGo redirect link to the underlying target URL."""
    if href.startswith("//"):
        href = f"https:{href}"
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        params = parse_qs(parsed.query)
        if "uddg" in params:
            return unquote(params["uddg"][0])
    return href


def _fetch_ddg_html(query: str, *, settings: Settings) -> str:
    """POST a query to the DuckDuckGo HTML endpoint and return the raw HTML."""
    response = httpx.post(
        _DDG_HTML_URL,
        data={"q": query},
        headers={"User-Agent": _USER_AGENT},
        follow_redirects=True,
        timeout=settings.http_timeout_seconds,
    )
    response.raise_for_status()
    return response.text


def _parse_ddg(html: str, *, max_results: int) -> list[SearchResult]:
    """Parse DuckDuckGo HTML results into normalized search results."""
    soup = BeautifulSoup(html, "html.parser")
    results: list[SearchResult] = []
    for block in soup.select("div.result"):
        classes = block.get("class") or []
        if any("result--ad" in cls for cls in classes):
            continue
        anchor = block.select_one("a.result__a")
        if not anchor:
            continue
        url = _decode_ddg_href(anchor.get("href", ""))
        title = anchor.get_text(strip=True) or None
        snippet_el = block.select_one(".result__snippet")
        description = snippet_el.get_text(strip=True) if snippet_el else None
        results.append(SearchResult(url=url, title=title, description=description))
        if len(results) >= max_results:
            break
    return results


def _search_duckduckgo(
    query: str, *, max_results: int, settings: Settings
) -> list[SearchResult]:
    """Run a DuckDuckGo HTML search, returning [] on failure."""
    try:
        html = _fetch_ddg_html(query, settings=settings)
    except httpx.HTTPError as exc:
        logger.warning("DuckDuckGo search failed for '%s': %s", query, exc)
        return []
    return _parse_ddg(html, max_results=max_results)


# --- OpenStreetMap / Overpass backend ----------------------------------------
# Real business entities (with names and, when present, websites). Businesses
# with no website tag become "build a site" leads; those with one become
# "improve" leads. Category labels are mapped to OSM tag selectors by keyword.

_OSM_KEYWORD_FILTERS: tuple[tuple[tuple[str, ...], tuple[tuple[str, str], ...]], ...] = (
    (
        ("restaurant", "food", "cafe", "coffee", "dining", "eatery", "pizza", "bar"),
        (("amenity", "restaurant"), ("amenity", "cafe"), ("amenity", "fast_food")),
    ),
    (("dentist", "dental", "orthodont"), (("amenity", "dentist"),)),
    (
        ("doctor", "medical", "clinic", "physician", "health", "pediatric"),
        (("amenity", "doctors"), ("amenity", "clinic"), ("healthcare", "clinic")),
    ),
    (("law", "lawyer", "attorney", "legal"), (("office", "lawyer"),)),
    (
        ("salon", "hair", "barber"),
        (("shop", "hairdresser"), ("shop", "barber")),
    ),
    (
        ("beauty", "spa", "nail", "cosmetic", "skincare"),
        (("shop", "beauty"), ("leisure", "spa")),
    ),
    (
        ("gym", "fitness", "yoga", "pilates", "crossfit"),
        (("leisure", "fitness_centre"), ("leisure", "sports_centre")),
    ),
    (
        ("plumb", "electric", "hvac", "roofing", "contractor", "construction"),
        (("craft", "plumber"), ("craft", "electrician"), ("craft", "hvac")),
    ),
    (
        ("auto", "car", "mechanic", "tire"),
        (("shop", "car_repair"), ("shop", "car")),
    ),
    (
        ("account", "insurance", "real estate", "realtor", "financial", "tax"),
        (("office", "accountant"), ("office", "insurance"), ("office", "estate_agent")),
    ),
    (
        ("retail", "shop", "store", "boutique", "clothing", "grocery", "market"),
        (("shop", "clothes"), ("shop", "convenience"), ("shop", "gift")),
    ),
)


def _osm_filters_for_category(category: str) -> tuple[tuple[str, str], ...]:
    """Map a category label to OSM tag selectors via keyword matching."""
    lowered = category.lower()
    for keywords, filters in _OSM_KEYWORD_FILTERS:
        if any(keyword in lowered for keyword in keywords):
            return filters
    return (("shop", ""),)  # fallback: any named shop (existence selector)


def _normalize_url(raw: str) -> str:
    """Ensure an OSM website value has a scheme."""
    url = raw.strip()
    if url and not url.startswith(("http://", "https://")):
        return f"https://{url}"
    return url


@lru_cache(maxsize=64)
def _geocode_bbox(
    location: str, user_agent: str, timeout: float
) -> tuple[float, float, float, float] | None:
    """Geocode a location to an Overpass bbox ``(south, west, north, east)``.

    Cached so a multi-category run geocodes each location only once.
    """
    response = httpx.get(
        _NOMINATIM_URL,
        params={"q": location, "format": "json", "limit": 1},
        headers={"User-Agent": user_agent},
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    if not data:
        return None
    # Nominatim boundingbox is [south, north, west, east] as strings.
    south, north, west, east = (float(value) for value in data[0]["boundingbox"])
    return (south, west, north, east)


def _overpass_query(
    filters: tuple[tuple[str, str], ...], bbox: tuple[float, float, float, float]
) -> str:
    """Build an Overpass QL query for named nodes/ways matching the filters."""
    box = "({},{},{},{})".format(*bbox)
    lines: list[str] = []
    for key, value in filters:
        selector = f'["{key}"="{value}"]' if value else f'["{key}"]'
        lines.append(f'  node{selector}["name"]{box};')
        lines.append(f'  way{selector}["name"]{box};')
    body = "\n".join(lines)
    return f"[out:json][timeout:40];\n(\n{body}\n);\nout tags center 80;"


def _fetch_overpass(query: str, *, settings: Settings) -> dict:
    """POST an Overpass QL query and return the parsed JSON.

    Retries with backoff on 429 (rate limit), since the public instance throttles
    rapid consecutive queries.
    """
    response: httpx.Response | None = None
    for attempt in range(3):
        response = httpx.post(
            _OVERPASS_URL,
            data={"data": query},
            headers={"User-Agent": _OSM_USER_AGENT},
            timeout=settings.overpass_timeout_seconds,
        )
        if response.status_code == 429 and attempt < 2:
            wait = 3 * (attempt + 1)
            logger.info("Overpass rate-limited, retrying in %ds", wait)
            time.sleep(wait)
            continue
        break
    assert response is not None  # loop always assigns at least once
    response.raise_for_status()
    return response.json()


def _osm_description(tags: dict[str, str]) -> str | None:
    """Build a short description from OSM tags."""
    primary = tags.get("amenity") or tags.get("shop") or tags.get("office") or ""
    cuisine = tags.get("cuisine", "")
    parts = [primary.replace("_", " "), cuisine.replace(";", ", ")]
    text = " ".join(part for part in parts if part).strip()
    return text or None


def _parse_overpass(data: dict, *, max_results: int) -> list[SearchResult]:
    """Parse Overpass elements into normalized search results (deduped by name)."""
    results: list[SearchResult] = []
    seen: set[str] = set()
    for element in data.get("elements", []):
        tags = element.get("tags", {})
        name = tags.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        website = tags.get("website") or tags.get("contact:website")
        url = _normalize_url(website) if website else None
        results.append(
            SearchResult(url=url, title=name, description=_osm_description(tags))
        )
        if len(results) >= max_results:
            break
    return results


def _search_overpass(
    category: str, *, max_results: int, settings: Settings
) -> list[SearchResult]:
    """Discover real businesses for a category via OpenStreetMap, [] on failure."""
    try:
        bbox = _geocode_bbox(
            settings.location, _OSM_USER_AGENT, settings.http_timeout_seconds
        )
    except httpx.HTTPError as exc:
        logger.warning("Geocoding failed for '%s': %s", settings.location, exc)
        return []
    if bbox is None:
        logger.warning("Could not geocode location '%s'", settings.location)
        return []

    query = _overpass_query(_osm_filters_for_category(category), bbox)
    try:
        data = _fetch_overpass(query, settings=settings)
    except httpx.HTTPError as exc:
        logger.warning("Overpass query failed for '%s': %s", category, exc)
        return []
    return _parse_overpass(data, max_results=max_results)


# --- Google backend ----------------------------------------------------------


def _search_google(
    query: str, *, max_results: int, settings: Settings
) -> list[SearchResult]:
    """Run a Google search via googlesearch-python, returning [] on failure."""
    try:
        raw_results = google_search(
            query,
            num_results=max_results,
            lang=settings.search_lang,
            advanced=True,
        )
    except Exception as exc:  # noqa: BLE001 - googlesearch raises broad errors
        logger.warning("Google search failed for '%s': %s", query, exc)
        return []
    return [
        SearchResult(
            url=getattr(result, "url", None),
            title=getattr(result, "title", None),
            description=getattr(result, "description", None),
        )
        for result in raw_results
    ]


# --- Public API --------------------------------------------------------------


def search_category(
    category: str,
    query: str,
    *,
    settings: Settings | None = None,
) -> list[Business]:
    """Run a single search query with the configured backend and parse results.

    Args:
        category: Raw category label (e.g. ``"restaurants"``).
        query: Full search query string (e.g. ``"restaurants in Boston, MA"``).
        settings: Optional settings override.

    Returns:
        A list of :class:`Business` objects, deduplicated by registered domain.
    """
    cfg = settings or get_settings()
    logger.info("Searching (%s): %s", cfg.search_backend, query)

    if cfg.search_backend == "google":
        results = _search_google(
            query, max_results=cfg.results_per_category, settings=cfg
        )
    elif cfg.search_backend == "duckduckgo":
        results = _search_duckduckgo(
            query, max_results=cfg.results_per_category, settings=cfg
        )
    else:  # "overpass" (default)
        results = _search_overpass(
            category, max_results=cfg.results_per_category, settings=cfg
        )

    businesses = [
        _to_business(result, category=category, query=query) for result in results
    ]
    return _dedupe(businesses)


def _dedupe(businesses: list[Business]) -> list[Business]:
    """Remove duplicate businesses sharing the same registered domain."""
    seen: set[str] = set()
    unique: list[Business] = []
    for business in businesses:
        key = _registered_domain(business.url) or business.name.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(business)
    return unique


def search_all(settings: Settings | None = None) -> list[Business]:
    """Search every configured category, throttling between queries.

    Args:
        settings: Optional settings override.

    Returns:
        Combined, de-duplicated list of businesses across all categories.
    """
    cfg = settings or get_settings()
    all_results: list[Business] = []

    for index, (category, query) in enumerate(cfg.search_queries()):
        all_results.extend(search_category(category, query, settings=cfg))
        is_last = index == len(cfg.categories) - 1
        if not is_last and cfg.search_delay_seconds > 0:
            logger.debug("Sleeping %.1fs before next query", cfg.search_delay_seconds)
            time.sleep(cfg.search_delay_seconds)

    logger.info("Search complete: %d businesses found", len(all_results))
    return _dedupe(all_results)
