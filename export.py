"""Incrementally export generated emails from leads.db to a CSV file.

Only rows that have not been exported yet (``emails.exported_at IS NULL``) are
appended to ``leads_export.csv``. After a successful append, those rows are
stamped with the current timestamp so subsequent runs skip them.

Uses only the Python standard library for all data handling (``csv``,
``sqlite3``, ``datetime``). ``config`` is imported solely to resolve the
database path so the same overrides used by the rest of the pipeline (including
``LEADGEN_DATABASE_PATH`` inside Docker) apply here too.
"""

from __future__ import annotations

import csv
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from config import Settings, get_settings

logger = logging.getLogger(__name__)

CSV_COLUMNS: tuple[str, ...] = (
    "business_name",
    "website_url",
    "category",
    "has_website",
    "email_subject",
    "email_body",
    "generated_at",
)

# website_url / has_website live on the businesses table; pull them via
# correlated subqueries (matched on business name) so a business with multiple
# rows can never multiply the exported email rows.
_SELECT_UNEXPORTED = """
SELECT
    e.id AS id,
    e.business_name AS business_name,
    (SELECT b.website_url FROM businesses b
       WHERE b.name = e.business_name LIMIT 1) AS website_url,
    e.category AS category,
    COALESCE(
        (SELECT b.has_website FROM businesses b
           WHERE b.name = e.business_name LIMIT 1), 0) AS has_website,
    e.subject AS email_subject,
    e.body AS email_body,
    e.generated_at AS generated_at
FROM emails e
WHERE e.exported_at IS NULL
ORDER BY e.id
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_exported_column(connection: sqlite3.Connection) -> None:
    """Add the ``exported_at`` column to the emails table if it is missing."""
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(emails)")
    }
    if "exported_at" not in columns:
        connection.execute("ALTER TABLE emails ADD COLUMN exported_at TEXT")
        logger.info("Added exported_at column to emails table")


def _csv_row(record: sqlite3.Row) -> dict[str, object]:
    """Map a DB row to the CSV schema, normalizing has_website to a bool string."""
    return {
        "business_name": record["business_name"],
        "website_url": record["website_url"] or "",
        "category": record["category"] or "",
        "has_website": "true" if record["has_website"] else "false",
        "email_subject": record["email_subject"],
        "email_body": record["email_body"],
        "generated_at": record["generated_at"] or "",
    }


def _append_to_csv(rows: list[dict[str, object]], csv_path: Path) -> None:
    """Append rows to the CSV, writing the header first if the file is new."""
    write_header = not csv_path.exists()
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


def export_leads(
    *, settings: Settings | None = None, csv_path: Path | None = None
) -> int:
    """Export not-yet-exported emails to CSV and stamp them as exported.

    Args:
        settings: Optional settings override (defaults to :func:`get_settings`).
        csv_path: Optional output path. Defaults to ``leads_export.csv`` beside
            the database file.

    Returns:
        The number of newly exported leads.
    """
    cfg = settings or get_settings()
    db_path = cfg.database_path
    output = csv_path or (db_path.parent / "leads_export.csv")

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        _ensure_exported_column(connection)
        records = connection.execute(_SELECT_UNEXPORTED).fetchall()

        if not records:
            connection.commit()
            logger.info("No new leads to export")
            print(f"Exported 0 new leads to {output.name}")
            return 0

        _append_to_csv([_csv_row(record) for record in records], output)

        timestamp = _utc_now_iso()
        connection.executemany(
            "UPDATE emails SET exported_at = ? WHERE id = ?",
            [(timestamp, record["id"]) for record in records],
        )
        connection.commit()
    finally:
        connection.close()

    count = len(records)
    logger.info("Exported %d new leads to %s", count, output)
    print(f"Exported {count} new leads to {output.name}")
    return count


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    export_leads()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
