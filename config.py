"""Central configuration for the lead generation pipeline.

All settings are sourced from environment variables (with sensible defaults)
so the same code runs locally via ``run_local.py`` and inside the Airflow
containers, where overrides like ``OLLAMA_BASE_URL`` differ.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent


class Settings(BaseSettings):
    """Runtime configuration, overridable via environment variables.

    Environment variables are prefixed with ``LEADGEN_`` (e.g.
    ``LEADGEN_LOCATION="Austin, TX"``). ``OLLAMA_BASE_URL`` is read without a
    prefix so it can be set the same way the Ollama tooling expects.
    """

    model_config = SettingsConfigDict(
        env_prefix="LEADGEN_",
        env_file=".env",
        extra="ignore",
    )

    # --- Search configuration -------------------------------------------------
    location: str = Field(
        default="Austin, TX",
        description="Geographic location appended to every search query.",
    )
    categories: list[str] = Field(
        default_factory=lambda: [
            "restaurants",
            "dentists",
            "law firms",
            "hair salons",
            "gyms",
            "plumbers",
        ],
        description="Business categories to search for in the location.",
    )
    results_per_category: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Number of search results to request per category query.",
    )
    search_delay_seconds: float = Field(
        default=2.5,
        ge=0.0,
        description="Delay between Google searches to avoid rate limiting.",
    )
    search_lang: str = Field(default="en", description="Search language code.")

    # --- Website fetching -----------------------------------------------------
    http_timeout_seconds: float = Field(
        default=10.0,
        gt=0.0,
        description="Timeout for fetching candidate business websites.",
    )
    max_website_chars: int = Field(
        default=6000,
        gt=0,
        description="Max characters of extracted page text passed to the LLM.",
    )

    # --- Ollama ---------------------------------------------------------------
    ollama_base_url: str = Field(
        default="http://localhost:11434",
        validation_alias="OLLAMA_BASE_URL",
        description="Base URL of the Ollama REST API.",
    )
    ollama_model: str = Field(
        default="llama3.2",
        description="Primary Ollama model to use for generation.",
    )
    ollama_fallback_model: str = Field(
        default="llama3.1",
        description="Fallback model if the primary model is unavailable.",
    )
    ollama_timeout_seconds: float = Field(
        default=120.0,
        gt=0.0,
        description="Timeout for a single Ollama generation request.",
    )
    ollama_max_retries: int = Field(
        default=3,
        ge=0,
        description="Number of retries for transient Ollama failures.",
    )

    # --- Storage --------------------------------------------------------------
    database_path: Path = Field(
        default=PROJECT_ROOT / "data" / "leads.db",
        description="Path to the SQLite database file.",
    )

    # --- Sender identity (used to personalize cold emails) --------------------
    sender_name: str = Field(default="Alex Rivera")
    sender_company: str = Field(default="Pixel & Pulse Web Studio")
    sender_email: str = Field(default="alex@pixelpulse.studio")

    def search_queries(self) -> list[tuple[str, str]]:
        """Build ``(category, query_string)`` pairs for every category.

        Returns:
            A list of tuples pairing the raw category with the full search
            query string (e.g. ``("restaurants", "restaurants in Austin, TX")``).
        """
        return [
            (category, f"{category} in {self.location}")
            for category in self.categories
        ]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance."""
    return Settings()
