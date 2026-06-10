"""Generate personalized cold emails with Ollama.

Two flavors:

* ``improve_site`` -- for businesses that already have a website. References
  2-3 concrete observations gathered by ``website_checker``.
* ``build_site`` -- for businesses with no usable website.

The model is asked to return ``SUBJECT:`` and ``BODY:`` markers, which are
parsed out; a deterministic fallback subject is used if parsing fails.
"""

from __future__ import annotations

import logging

from config import Settings, get_settings
from tasks import ollama_client
from tasks.models import Business, Email

logger = logging.getLogger(__name__)


def _sender_block(cfg: Settings) -> str:
    return (
        f"You are a friendly web designer. Sign the email off with the sender "
        f"name '{cfg.sender_name}' and email '{cfg.sender_email}'."
    )


def _format_rules(cfg: Settings) -> str:
    return (
        "Write a short, warm, non-spammy cold email (under 150 words). "
        "Do not be pushy or use hype. Sound like a real person. "
        f"Sign off with the sender's name and email exactly as the literal "
        f"placeholders '{cfg.sender_name}' and '{cfg.sender_email}'; do NOT "
        "invent a real name, company, or email address.\n"
        "Return your answer in exactly this format:\n"
        "SUBJECT: <one line subject>\n"
        "BODY: <the email body>\n"
        "Do not add any other text before or after."
    )


def build_improve_prompt(business: Business, *, settings: Settings) -> str:
    """Build the prompt for a business that already has a website."""
    observations = business.website_observations or [
        "The site could be modernized for a stronger first impression."
    ]
    observation_lines = "\n".join(f"- {obs}" for obs in observations[:3])
    snippet = (business.website_text or "")[:1200]
    return (
        f"{_sender_block(settings)}\n\n"
        f"Write a cold email to '{business.name}', a {business.category} business. "
        f"They already have a website ({business.website_url}). "
        "Your offer is to improve their existing website.\n\n"
        "Reference these specific, real observations about their current site "
        "naturally (pick the 2-3 strongest):\n"
        f"{observation_lines}\n\n"
        f"Context from their homepage (may be truncated):\n\"\"\"\n{snippet}\n\"\"\"\n\n"
        f"{_format_rules(settings)}"
    )


def build_build_prompt(business: Business, *, settings: Settings) -> str:
    """Build the prompt for a business with no website."""
    return (
        f"{_sender_block(settings)}\n\n"
        f"Write a cold email to '{business.name}', a {business.category} business "
        f"in their local area. They do NOT appear to have a website. "
        "Your offer is to build them a professional website from scratch.\n\n"
        "Explain briefly why a website would help a business like theirs "
        "(being found on Google, online bookings/menus, credibility). "
        "Keep it specific to their type of business.\n\n"
        f"{_format_rules(settings)}"
    )


def _parse_subject_body(text: str, *, fallback_subject: str) -> tuple[str, str]:
    """Split model output into ``(subject, body)`` using the SUBJECT/BODY markers."""
    subject = fallback_subject
    body = text.strip()

    lower = text.lower()
    if "subject:" in lower and "body:" in lower:
        subject_start = lower.index("subject:") + len("subject:")
        body_marker = lower.index("body:")
        subject = text[subject_start:body_marker].strip()
        body = text[body_marker + len("body:") :].strip()
    return subject or fallback_subject, body


def generate_email(business: Business, *, settings: Settings | None = None) -> Email:
    """Generate a single personalized cold email for a business.

    Args:
        business: The (clustered, website-checked) business.
        settings: Optional settings override.

    Returns:
        An :class:`Email`. On generation failure, a deterministic templated
        fallback email is returned so the pipeline never drops a lead.
    """
    cfg = settings or get_settings()

    if business.has_website:
        email_type = "improve_site"
        prompt = build_improve_prompt(business, settings=cfg)
        fallback_subject = f"A few quick ideas for {business.name}'s website"
    else:
        email_type = "build_site"
        prompt = build_build_prompt(business, settings=cfg)
        fallback_subject = f"Helping {business.name} get found online"

    try:
        result = ollama_client.generate(prompt, settings=cfg)
        subject, body = _parse_subject_body(
            result.text, fallback_subject=fallback_subject
        )
        model = result.model
    except ollama_client.OllamaError as exc:
        logger.warning("Email generation failed for %s: %s", business.name, exc)
        subject = fallback_subject
        body = _fallback_body(business, cfg, email_type=email_type)
        model = "fallback-template"

    return Email(
        business_name=business.name,
        business_url=business.website_url or business.url,
        category=business.category,
        email_type=email_type,
        subject=subject,
        body=body,
        model=model,
    )


def _fallback_body(business: Business, cfg: Settings, *, email_type: str) -> str:
    """Deterministic template used when the LLM is unavailable."""
    if email_type == "improve_site":
        opener = (
            f"I came across {business.name} online and took a look at your website. "
            "I think a few small updates could help it convert more visitors."
        )
    else:
        opener = (
            f"I was looking for {business.category} businesses in the area and "
            f"noticed {business.name} doesn't seem to have a website yet."
        )
    return (
        f"Hi there,\n\n{opener}\n\n"
        f"I build clean, fast websites for local businesses and would love to "
        f"share a couple of ideas. Open to a quick chat?\n\n"
        f"Best,\n{cfg.sender_name}\n{cfg.sender_company}\n{cfg.sender_email}"
    )


def generate_all(
    businesses: list[Business], *, settings: Settings | None = None
) -> list[Email]:
    """Generate emails for every business in ``businesses``."""
    cfg = settings or get_settings()
    emails = [generate_email(business, settings=cfg) for business in businesses]
    logger.info("Generated %d emails", len(emails))
    return emails
