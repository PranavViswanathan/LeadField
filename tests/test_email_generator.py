"""Behavior tests for cold email generation."""

from __future__ import annotations

import httpx
import pytest
import respx

from config import get_settings
from tasks import email_generator
from tasks.models import Business
from tests.conftest import make_business


def test_parse_subject_body_extracts_markers() -> None:
    text = "SUBJECT: Quick idea\nBODY: Hi there,\nthis is the body."
    subject, body = email_generator._parse_subject_body(text, fallback_subject="fb")
    assert subject == "Quick idea"
    assert body == "Hi there,\nthis is the body."


def test_parse_subject_body_uses_fallback_without_markers() -> None:
    subject, body = email_generator._parse_subject_body(
        "just some text", fallback_subject="FALLBACK"
    )
    assert subject == "FALLBACK"
    assert body == "just some text"


def test_improve_prompt_includes_name_and_observations() -> None:
    business = make_business(
        has_website=True,
        website_url="https://joespizza.com",
        website_observations=["No mobile viewport tag", "Missing meta description"],
        website_text="We sell pizza.",
        category="restaurant",
    )
    prompt = email_generator.build_improve_prompt(business, settings=get_settings())
    assert "Joe's Pizza" in prompt
    assert "No mobile viewport tag" in prompt
    assert "improve" in prompt.lower()


def test_build_prompt_mentions_no_website() -> None:
    business = make_business(has_website=False, category="restaurant")
    prompt = email_generator.build_build_prompt(business, settings=get_settings())
    assert "Joe's Pizza" in prompt
    assert "not appear to have a website" in prompt.lower()


@respx.mock
def test_generate_email_improve_path(ollama_settings) -> None:
    respx.post("http://ollama.test:11434/api/generate").mock(
        return_value=httpx.Response(
            200,
            json={"response": "SUBJECT: Boost Joe's site\nBODY: Hi Joe, ..."},
        )
    )
    business = make_business(
        has_website=True,
        website_url="https://joespizza.com",
        website_observations=["No viewport tag"],
        category="restaurant",
    )
    email = email_generator.generate_email(business, settings=ollama_settings)

    assert email.email_type == "improve_site"
    assert email.subject == "Boost Joe's site"
    assert email.model == "primary-model"
    assert email.business_url == "https://joespizza.com"


@respx.mock
def test_generate_email_build_path(ollama_settings) -> None:
    respx.post("http://ollama.test:11434/api/generate").mock(
        return_value=httpx.Response(
            200, json={"response": "SUBJECT: Get online\nBODY: Hi there"}
        )
    )
    business = make_business(has_website=False, url=None, category="restaurant")
    email = email_generator.generate_email(business, settings=ollama_settings)
    assert email.email_type == "build_site"
    assert email.subject == "Get online"


def test_generate_email_falls_back_when_ollama_unavailable(
    ollama_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise email_generator.ollama_client.OllamaError("unreachable")

    monkeypatch.setattr(email_generator.ollama_client, "generate", boom)
    business = make_business(has_website=False, category="restaurant", name="No Web Cafe")
    email = email_generator.generate_email(business, settings=ollama_settings)

    assert email.model == "fallback-template"
    assert email.email_type == "build_site"
    assert "No Web Cafe" in email.body
