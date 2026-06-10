"""Behavior tests for incremental CSV export."""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

import export
from tasks import storage
from tests.conftest import make_business, make_email


def _seed(settings, *, name: str, has_website: bool, url: str | None) -> None:
    storage.save_businesses(
        [make_business(name=name, has_website=has_website, website_url=url, url=url)],
        settings=settings,
    )
    storage.save_emails(
        [make_email(business_name=name, business_url=url)], settings=settings
    )


def _read_csv(path: Path) -> list[list[str]]:
    with path.open() as handle:
        return list(csv.reader(handle))


def test_export_creates_csv_with_header(tmp_settings, tmp_path) -> None:
    _seed(tmp_settings, name="Joe's Pizza", has_website=True, url="https://joes.com")
    csv_path = tmp_path / "leads_export.csv"

    count = export.export_leads(settings=tmp_settings, csv_path=csv_path)

    assert count == 1
    rows = _read_csv(csv_path)
    assert rows[0] == list(export.CSV_COLUMNS)
    assert rows[1][0] == "Joe's Pizza"
    assert rows[1][1] == "https://joes.com"
    assert rows[1][3] == "true"


def test_export_is_incremental(tmp_settings, tmp_path) -> None:
    _seed(tmp_settings, name="Joe's Pizza", has_website=True, url="https://joes.com")
    csv_path = tmp_path / "leads_export.csv"

    first = export.export_leads(settings=tmp_settings, csv_path=csv_path)
    second = export.export_leads(settings=tmp_settings, csv_path=csv_path)

    assert first == 1
    assert second == 0
    # No duplicate header or rows on the second run.
    assert len(_read_csv(csv_path)) == 2


def test_export_appends_only_new_rows(tmp_settings, tmp_path) -> None:
    csv_path = tmp_path / "leads_export.csv"
    _seed(tmp_settings, name="Joe's Pizza", has_website=True, url="https://joes.com")
    export.export_leads(settings=tmp_settings, csv_path=csv_path)

    _seed(tmp_settings, name="Maria's Tacos", has_website=False, url=None)
    count = export.export_leads(settings=tmp_settings, csv_path=csv_path)

    assert count == 1
    rows = _read_csv(csv_path)
    assert len(rows) == 3  # header + 2 leads
    assert rows[2][0] == "Maria's Tacos"
    assert rows[2][3] == "false"


def test_export_adds_exported_at_column(tmp_settings, tmp_path) -> None:
    _seed(tmp_settings, name="Joe's Pizza", has_website=True, url="https://joes.com")
    export.export_leads(settings=tmp_settings, csv_path=tmp_path / "out.csv")

    connection = sqlite3.connect(tmp_settings.database_path)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(emails)")}
        exported_at = connection.execute(
            "SELECT exported_at FROM emails"
        ).fetchone()[0]
    finally:
        connection.close()

    assert "exported_at" in columns
    assert exported_at is not None


def test_export_no_rows_returns_zero(tmp_settings, tmp_path) -> None:
    storage.init_db(tmp_settings)
    count = export.export_leads(settings=tmp_settings, csv_path=tmp_path / "out.csv")
    assert count == 0
