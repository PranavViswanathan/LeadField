"""Behavior tests for SQLite persistence."""

from __future__ import annotations

import json
import sqlite3

from tasks import storage
from tests.conftest import make_business, make_email


def _count(settings, table: str) -> int:
    connection = sqlite3.connect(settings.database_path)
    try:
        return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        connection.close()


def test_init_db_creates_tables(tmp_settings) -> None:
    storage.init_db(tmp_settings)
    connection = sqlite3.connect(tmp_settings.database_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        connection.close()
    assert {"businesses", "emails"}.issubset(tables)


def test_save_businesses_persists_observations_as_json(tmp_settings) -> None:
    business = make_business(
        has_website=True,
        website_observations=["no viewport", "thin content"],
    )
    storage.save_businesses([business], settings=tmp_settings)

    connection = sqlite3.connect(tmp_settings.database_path)
    try:
        row = connection.execute(
            "SELECT website_observations, has_website FROM businesses"
        ).fetchone()
    finally:
        connection.close()
    assert json.loads(row[0]) == ["no viewport", "thin content"]
    assert row[1] == 1


def test_save_businesses_is_idempotent_on_natural_key(tmp_settings) -> None:
    business = make_business()
    storage.save_businesses([business], settings=tmp_settings)
    storage.save_businesses([business], settings=tmp_settings)
    assert _count(tmp_settings, "businesses") == 1


def test_save_emails_round_trip(tmp_settings) -> None:
    storage.save_emails([make_email()], settings=tmp_settings)
    fetched = storage.fetch_emails(settings=tmp_settings)
    assert len(fetched) == 1
    assert fetched[0]["business_name"] == "Joe's Pizza"
    assert fetched[0]["email_type"] == "improve_site"


def test_schedule_create_list_update_delete(tmp_settings) -> None:
    schedule_id = storage.create_schedule(
        locations=["Boston, MA", "Cambridge, MA"],
        max_per_city=75,
        mode="daily",
        run_at=None,
        time_of_day="08:00",
        reset=True,
        settings=tmp_settings,
    )
    schedules = storage.list_schedules(settings=tmp_settings)
    assert len(schedules) == 1
    assert schedules[0]["id"] == schedule_id
    assert schedules[0]["mode"] == "daily"
    assert schedules[0]["status"] == "pending"

    storage.update_schedule(
        schedule_id, status="done", last_run="2026-06-10T08:00:00", settings=tmp_settings
    )
    assert storage.list_schedules(settings=tmp_settings)[0]["status"] == "done"

    storage.delete_schedule(schedule_id, settings=tmp_settings)
    assert storage.list_schedules(settings=tmp_settings) == []


def test_save_emails_idempotent_on_business_key(tmp_settings) -> None:
    storage.save_emails([make_email()], settings=tmp_settings)
    storage.save_emails(
        [make_email(subject="Updated subject")], settings=tmp_settings
    )
    fetched = storage.fetch_emails(settings=tmp_settings)
    assert len(fetched) == 1
    assert fetched[0]["subject"] == "Updated subject"
