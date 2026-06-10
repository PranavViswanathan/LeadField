"""FastAPI server backing the lead generation dashboard.

Serves the static single-page dashboard plus a small read-only JSON API over
the SQLite database produced by the pipeline. The API degrades gracefully when
the database does not exist yet (returns empty/zeroed payloads), so the UI can
render an elegant empty state before the first pipeline run.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config import get_settings

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="lead_gen dashboard", docs_url="/api/docs")


def _connect() -> sqlite3.Connection | None:
    """Open the leads DB read-only, or return None if it does not exist."""
    db_path = get_settings().database_path
    if not db_path.exists():
        return None
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def _empty_stats() -> dict[str, Any]:
    return {
        "total_businesses": 0,
        "with_website": 0,
        "without_website": 0,
        "total_emails": 0,
        "location": get_settings().location,
        "categories": [],
        "models": [],
    }


@app.get("/api/stats")
def stats() -> dict[str, Any]:
    """Aggregate counts for the hero, stat band, and category signal."""
    connection = _connect()
    if connection is None:
        return _empty_stats()
    try:
        total = connection.execute("SELECT COUNT(*) AS c FROM businesses").fetchone()["c"]
        with_site = connection.execute(
            "SELECT COUNT(*) AS c FROM businesses WHERE has_website = 1"
        ).fetchone()["c"]
        emails = connection.execute("SELECT COUNT(*) AS c FROM emails").fetchone()["c"]
        category_rows = connection.execute(
            """
            SELECT COALESCE(category, 'other') AS category,
                   COUNT(*) AS count,
                   SUM(has_website) AS with_site
            FROM businesses
            GROUP BY category
            ORDER BY count DESC
            """
        ).fetchall()
        model_rows = connection.execute(
            "SELECT DISTINCT model FROM emails WHERE model IS NOT NULL"
        ).fetchall()
    finally:
        connection.close()

    return {
        "total_businesses": total,
        "with_website": with_site,
        "without_website": total - with_site,
        "total_emails": emails,
        "location": get_settings().location,
        "categories": [
            {
                "name": row["category"],
                "count": row["count"],
                "with_website": row["with_site"] or 0,
                "without_website": row["count"] - (row["with_site"] or 0),
            }
            for row in category_rows
        ],
        "models": [row["model"] for row in model_rows],
    }


@app.get("/api/leads")
def leads() -> list[dict[str, Any]]:
    """Every generated email joined with its business context."""
    connection = _connect()
    if connection is None:
        return []
    try:
        rows = connection.execute(
            """
            SELECT
                e.business_name AS business_name,
                e.category AS category,
                e.email_type AS email_type,
                e.subject AS subject,
                e.body AS body,
                e.model AS model,
                e.generated_at AS generated_at,
                (SELECT b.has_website FROM businesses b
                   WHERE b.name = e.business_name LIMIT 1) AS has_website,
                (SELECT b.website_url FROM businesses b
                   WHERE b.name = e.business_name LIMIT 1) AS website_url,
                (SELECT b.url FROM businesses b
                   WHERE b.name = e.business_name LIMIT 1) AS url,
                (SELECT b.website_observations FROM businesses b
                   WHERE b.name = e.business_name LIMIT 1) AS observations
            FROM emails e
            ORDER BY e.generated_at DESC
            """
        ).fetchall()
    finally:
        connection.close()

    return [_row_to_lead(row) for row in rows]


def _row_to_lead(row: sqlite3.Row) -> dict[str, Any]:
    try:
        observations = json.loads(row["observations"]) if row["observations"] else []
    except (json.JSONDecodeError, TypeError):
        observations = []
    return {
        "business_name": row["business_name"],
        "category": row["category"] or "other",
        "email_type": row["email_type"],
        "subject": row["subject"],
        "body": row["body"],
        "model": row["model"],
        "generated_at": row["generated_at"],
        "has_website": bool(row["has_website"]),
        "website_url": row["website_url"] or row["url"],
        "observations": observations,
    }


@app.get("/")
def index() -> FileResponse:
    """Serve the dashboard shell."""
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
