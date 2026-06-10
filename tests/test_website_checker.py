"""Behavior tests for website detection and analysis."""

from __future__ import annotations

import datetime

import httpx
import pytest
import respx
from bs4 import BeautifulSoup

from tasks import website_checker
from tests.conftest import make_business

_CURRENT_YEAR = datetime.datetime.now(datetime.timezone.utc).year

MODERN_HTML = """
<html><head>
  <title>Joe's Pizza</title>
  <meta name="viewport" content="width=device-width">
  <meta name="description" content="Best pizza in Austin since 2024">
</head><body>
  <section><img src="hero.jpg">
  <p>{filler}</p>
  <footer>(c) {year} Joe's Pizza</footer></section>
</body></html>
""".format(filler="word " * 200, year=_CURRENT_YEAR)

DATED_HTML = "<html><head><title>Old</title></head><body><table><tr><td>hi</td></tr></table></body></html>"


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def test_modern_site_yields_no_observations() -> None:
    soup = _soup(MODERN_HTML)
    text = website_checker._extract_text(soup, max_chars=6000)
    observations = website_checker._build_observations(soup, MODERN_HTML, page_text=text)
    assert observations == []


def test_dated_site_yields_relevant_observations() -> None:
    soup = _soup(DATED_HTML)
    text = website_checker._extract_text(soup, max_chars=6000)
    observations = website_checker._build_observations(soup, DATED_HTML, page_text=text)
    joined = " ".join(observations).lower()
    assert "viewport" in joined
    assert "meta description" in joined
    assert "thin content" in joined
    assert len(observations) <= 4


def test_observations_capped_at_four() -> None:
    soup = _soup(DATED_HTML)
    text = website_checker._extract_text(soup, max_chars=6000)
    observations = website_checker._build_observations(soup, DATED_HTML, page_text=text)
    assert len(observations) <= 4


def test_extract_text_strips_scripts_and_truncates() -> None:
    html = "<html><body><script>evil()</script><p>" + ("x " * 100) + "</p></body></html>"
    text = website_checker._extract_text(_soup(html), max_chars=20)
    assert "evil" not in text
    assert len(text) == 20


def test_check_business_no_url_marks_no_website() -> None:
    business = make_business(url=None)
    result = website_checker.check_business(business)
    assert result.has_website is False
    assert "no url" in (result.website_error or "")


def test_check_business_directory_url_marks_no_website() -> None:
    business = make_business(url="https://www.yelp.com/biz/joes")
    result = website_checker.check_business(business)
    assert result.has_website is False
    assert "directory" in (result.website_error or "")


@respx.mock
def test_check_business_fetches_and_analyzes(tmp_settings) -> None:
    respx.get("https://joespizza.com").mock(
        return_value=httpx.Response(200, html=DATED_HTML)
    )
    business = make_business(url="https://joespizza.com")
    result = website_checker.check_business(business, settings=tmp_settings)

    assert result.has_website is True
    assert result.website_title == "Old"
    assert result.website_observations
    assert result.website_error is None


@respx.mock
def test_check_business_handles_fetch_failure() -> None:
    respx.get("https://down.example.com").mock(
        side_effect=httpx.ConnectError("refused")
    )
    business = make_business(url="https://down.example.com")
    result = website_checker.check_business(business)

    assert result.has_website is False
    assert result.website_error is not None


@respx.mock
def test_check_business_handles_http_error_status() -> None:
    respx.get("https://gone.example.com").mock(return_value=httpx.Response(503))
    business = make_business(url="https://gone.example.com")
    result = website_checker.check_business(business)
    assert result.has_website is False


def test_recent_copyright_year_not_flagged() -> None:
    year = datetime.datetime.now(datetime.timezone.utc).year
    html = (
        f"<html><head><title>T</title>"
        f"<meta name='viewport' content='x'>"
        f"<meta name='description' content='d'></head>"
        f"<body><section><img src='a.jpg'><p>{'w ' * 200}</p>"
        f"<footer>(c) {year} Joe</footer></section></body></html>"
    )
    soup = _soup(html)
    text = website_checker._extract_text(soup, max_chars=6000)
    observations = website_checker._build_observations(soup, html, page_text=text)
    assert observations == []
