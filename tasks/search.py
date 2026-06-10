"""Business discovery via Google search (no API key required).

Uses the ``googlesearch-python`` library to run category/location queries and
parses each result into a :class:`~tasks.models.Business`. A configurable delay
is inserted between queries to avoid Google rate limiting.
"""

from __future__ import annotations

import logging
import re
import time
from urllib.parse import urlparse

from googlesearch import search as google_search

from config import Settings, get_settings
from tasks.models import Business

logger = logging.getLogger(__name__)

# Domains that are aggregators/directories rather than a business's own site.
# Used both to clean up names and (later) to decide "no real website".
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
    }
)

_TITLE_SUFFIX = re.compile(
    r"\s*[\|\-–—:]\s*(yelp|facebook|instagram|home|official site|menu).*$",
    re.IGNORECASE,
)


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


def _to_business(result: object, *, category: str, query: str) -> Business:
    """Convert a googlesearch advanced result object into a :class:`Business`."""
    url = getattr(result, "url", None)
    title = getattr(result, "title", None)
    description = getattr(result, "description", None)
    return Business(
        name=_clean_business_name(title, url),
        url=url,
        title=title,
        description=description,
        source_query=query,
        search_category=category,
    )


def search_category(
    category: str,
    query: str,
    *,
    settings: Settings | None = None,
) -> list[Business]:
    """Run a single Google search query and parse the results.

    Args:
        category: Raw category label (e.g. ``"restaurants"``).
        query: Full search query string (e.g. ``"restaurants in Austin, TX"``).
        settings: Optional settings override.

    Returns:
        A list of :class:`Business` objects, deduplicated by registered domain.
    """
    cfg = settings or get_settings()
    logger.info("Searching: %s", query)

    businesses: list[Business] = []
    try:
        raw_results = google_search(
            query,
            num_results=cfg.results_per_category,
            lang=cfg.search_lang,
            advanced=True,
        )
    except Exception as exc:  # noqa: BLE001 - googlesearch raises broad errors
        logger.warning("Search failed for '%s': %s", query, exc)
        return []

    for result in raw_results:
        try:
            businesses.append(_to_business(result, category=category, query=query))
        except Exception as exc:  # noqa: BLE001 - skip malformed results
            logger.debug("Skipping malformed result: %s", exc)

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
