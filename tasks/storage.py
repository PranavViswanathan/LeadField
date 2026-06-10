"""Persist businesses and generated emails to SQLite.

The schema is two tables: ``businesses`` and ``emails`` (linked by business
name + url). Writes are idempotent on re-run via ``INSERT OR REPLACE`` on a
natural key, so re-running the pipeline refreshes rather than duplicates.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path

from config import Settings, get_settings
from tasks.models import Business, Email

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS businesses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url TEXT,
    category TEXT,
    search_category TEXT,
    source_query TEXT,
    has_website INTEGER NOT NULL DEFAULT 0,
    website_url TEXT,
    website_title TEXT,
    website_observations TEXT,
    website_error TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(name, url)
);

CREATE TABLE IF NOT EXISTS emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_name TEXT NOT NULL,
    business_url TEXT,
    category TEXT,
    email_type TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    model TEXT,
    generated_at TEXT,
    UNIQUE(business_name, business_url)
);
"""


@contextmanager
def _connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Yield a SQLite connection, ensuring the parent directory exists."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db(settings: Settings | None = None) -> None:
    """Create the database tables if they do not already exist."""
    cfg = settings or get_settings()
    with _connect(cfg.database_path) as connection:
        connection.executescript(_SCHEMA)
    logger.info("Initialized database at %s", cfg.database_path)


def save_businesses(
    businesses: list[Business], *, settings: Settings | None = None
) -> int:
    """Insert or update businesses. Returns the number written."""
    cfg = settings or get_settings()
    init_db(cfg)
    with _connect(cfg.database_path) as connection:
        connection.executemany(
            """
            INSERT OR REPLACE INTO businesses (
                name, url, category, search_category, source_query,
                has_website, website_url, website_title,
                website_observations, website_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    business.name,
                    business.url,
                    business.category,
                    business.search_category,
                    business.source_query,
                    int(business.has_website),
                    business.website_url,
                    business.website_title,
                    json.dumps(business.website_observations),
                    business.website_error,
                )
                for business in businesses
            ],
        )
    logger.info("Saved %d businesses", len(businesses))
    return len(businesses)


def save_emails(emails: list[Email], *, settings: Settings | None = None) -> int:
    """Insert or update emails. Returns the number written."""
    cfg = settings or get_settings()
    init_db(cfg)
    with _connect(cfg.database_path) as connection:
        connection.executemany(
            """
            INSERT OR REPLACE INTO emails (
                business_name, business_url, category, email_type,
                subject, body, model, generated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    email.business_name,
                    email.business_url,
                    email.category,
                    email.email_type,
                    email.subject,
                    email.body,
                    email.model,
                    email.generated_at.isoformat(),
                )
                for email in emails
            ],
        )
    logger.info("Saved %d emails", len(emails))
    return len(emails)


def clear_all(settings: Settings | None = None) -> None:
    """Delete all rows from both tables (used for a fresh location switch)."""
    cfg = settings or get_settings()
    init_db(cfg)
    with _connect(cfg.database_path) as connection:
        connection.execute("DELETE FROM emails")
        connection.execute("DELETE FROM businesses")
    logger.info("Cleared businesses and emails tables")


def fetch_emails(settings: Settings | None = None) -> list[dict[str, object]]:
    """Return all stored emails as a list of dicts (newest first)."""
    cfg = settings or get_settings()
    init_db(cfg)
    with _connect(cfg.database_path) as connection:
        rows = connection.execute(
            "SELECT * FROM emails ORDER BY generated_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]
