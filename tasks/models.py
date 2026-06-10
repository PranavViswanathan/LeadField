"""Shared data models that flow through the lead generation pipeline.

Every pipeline stage accepts and returns these Pydantic models. They are JSON
serializable (via :meth:`~pydantic.BaseModel.model_dump`) so they can be passed
between Airflow tasks through XCom.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Business(BaseModel):
    """A single business lead, enriched as it moves through the pipeline.

    Fields are populated incrementally: ``search`` sets the identity fields,
    ``cluster`` sets :attr:`category`, and ``website_checker`` sets the website
    fields.
    """

    name: str = Field(description="Best-effort business name from search.")
    url: str | None = Field(
        default=None, description="Primary URL surfaced by the search result."
    )
    title: str | None = Field(default=None, description="Search result title.")
    description: str | None = Field(
        default=None, description="Search result snippet/description."
    )
    source_query: str = Field(description="Query that produced this result.")
    search_category: str = Field(
        description="Raw category searched (e.g. 'restaurants')."
    )

    # Set by cluster.py
    category: str | None = Field(
        default=None, description="Normalized business type cluster."
    )

    # Set by website_checker.py
    has_website: bool = Field(
        default=False, description="Whether a usable own-website was found."
    )
    website_url: str | None = Field(
        default=None, description="Resolved website URL that was fetched."
    )
    website_title: str | None = Field(default=None)
    website_text: str | None = Field(
        default=None, description="Extracted, truncated page text."
    )
    website_observations: list[str] = Field(
        default_factory=list,
        description="Heuristic observations about the current website.",
    )
    website_error: str | None = Field(
        default=None, description="Reason the website could not be fetched."
    )


class Email(BaseModel):
    """A generated cold email tied to a business."""

    business_name: str
    business_url: str | None = None
    category: str | None = None
    email_type: str = Field(
        description="Either 'improve_site' or 'build_site'.",
    )
    subject: str
    body: str
    model: str = Field(description="Ollama model that generated the email.")
    generated_at: datetime = Field(default_factory=_utc_now)
