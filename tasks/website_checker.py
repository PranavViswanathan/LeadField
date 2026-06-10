"""Determine whether a business has its own website and analyze it.

A search result URL counts as "has a website" only when it points to the
business's own domain (not a directory/aggregator) and the page actually
fetches. When a page is fetched, lightweight heuristics produce observations
the email generator can reference (e.g. "no mobile viewport tag", "thin copy").
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup

from config import Settings, get_settings
from tasks.models import Business
from tasks.search import is_directory_url

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def _extract_text(soup: BeautifulSoup, *, max_chars: int) -> str:
    """Return visible page text, scripts/styles removed and truncated."""
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    text = " ".join(soup.get_text(separator=" ").split())
    return text[:max_chars]


def _build_observations(soup: BeautifulSoup, html: str, *, page_text: str) -> list[str]:
    """Derive heuristic observations about a fetched website."""
    observations: list[str] = []

    if not soup.find("meta", attrs={"name": "viewport"}):
        observations.append(
            "No mobile viewport meta tag, the site likely is not mobile-responsive."
        )

    meta_desc = soup.find("meta", attrs={"name": "description"})
    if not meta_desc or not meta_desc.get("content", "").strip():
        observations.append("Missing meta description, hurting search visibility (SEO).")

    title_tag = soup.find("title")
    if not title_tag or not title_tag.get_text(strip=True):
        observations.append("No page title tag, which weakens SEO and browser tabs.")

    word_count = len(page_text.split())
    if word_count < 150:
        observations.append(
            f"Very thin content (~{word_count} words), little for visitors or Google."
        )

    if soup.find("table") and not soup.find("section"):
        observations.append(
            "Layout appears to rely on table-based markup, an outdated pattern."
        )

    current_year = datetime.now(timezone.utc).year
    has_recent_year = any(
        str(year) in html for year in range(current_year - 1, current_year + 1)
    )
    if not has_recent_year:
        observations.append(
            "No recent copyright year, the site may not have been updated lately."
        )

    if not soup.find("img"):
        observations.append("No images detected, the design likely feels dated or sparse.")

    return observations[:4]


def fetch_website(url: str, *, settings: Settings | None = None) -> dict[str, object]:
    """Fetch and analyze a single URL.

    Args:
        url: The candidate website URL.
        settings: Optional settings override.

    Returns:
        A dict with keys ``ok`` (bool), and on success ``title``, ``text``,
        ``observations``, ``final_url``; on failure ``error``.
    """
    cfg = settings or get_settings()
    try:
        with httpx.Client(
            timeout=cfg.http_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            response = client.get(url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.info("Fetch failed for %s: %s", url, exc)
        return {"ok": False, "error": str(exc)}

    soup = BeautifulSoup(response.text, "html.parser")
    page_text = _extract_text(soup, max_chars=cfg.max_website_chars)
    title_tag = soup.find("title")
    return {
        "ok": True,
        "final_url": str(response.url),
        "title": title_tag.get_text(strip=True) if title_tag else None,
        "text": page_text,
        "observations": _build_observations(soup, response.text, page_text=page_text),
    }


def check_business(business: Business, *, settings: Settings | None = None) -> Business:
    """Resolve whether a business has a usable website and analyze it.

    A business with no URL, or whose URL is a known directory, is marked
    ``has_website=False`` without a network call. Otherwise the page is fetched
    and analyzed.

    Args:
        business: The business to check.
        settings: Optional settings override.

    Returns:
        A copy of ``business`` with website fields populated.
    """
    cfg = settings or get_settings()

    if not business.url:
        return business.model_copy(
            update={"has_website": False, "website_error": "no url in search result"}
        )

    if is_directory_url(business.url):
        return business.model_copy(
            update={
                "has_website": False,
                "website_error": "only a directory/aggregator listing was found",
            }
        )

    result = fetch_website(business.url, settings=cfg)
    if not result.get("ok"):
        return business.model_copy(
            update={"has_website": False, "website_error": str(result.get("error"))}
        )

    return business.model_copy(
        update={
            "has_website": True,
            "website_url": result.get("final_url"),
            "website_title": result.get("title"),
            "website_text": result.get("text"),
            "website_observations": result.get("observations", []),
            "website_error": None,
        }
    )


def check_all(
    businesses: list[Business], *, settings: Settings | None = None
) -> list[Business]:
    """Run :func:`check_business` across a list of businesses."""
    cfg = settings or get_settings()
    checked = [check_business(business, settings=cfg) for business in businesses]
    with_site = sum(1 for business in checked if business.has_website)
    logger.info(
        "Website check complete: %d with site, %d without",
        with_site,
        len(checked) - with_site,
    )
    return checked
