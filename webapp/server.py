"""FastAPI server backing the lead generation dashboard.

Serves the static single-page dashboard plus a small read-only JSON API over
the SQLite database produced by the pipeline. The API degrades gracefully when
the database does not exist yet (returns empty/zeroed payloads), so the UI can
render an elegant empty state before the first pipeline run.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import pipeline
from config import get_settings
from tasks import storage

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="lead_gen dashboard", docs_url="/api/docs")

# --- Dynamic pipeline run (background job) -----------------------------------
# A single background run at a time. State is shared across requests; the
# dashboard polls /api/run/status while a run is in progress. A run may sweep
# multiple cities sequentially.
_job_lock = threading.Lock()
_job: dict[str, Any] = {
    "status": "idle",  # idle | running | done | error
    "stage": None,
    "message": "",
    "processed": 0,
    "total": 0,
    "result": None,
    "error": None,
}
# Label of the most recent run, surfaced by /api/stats so the UI stays in sync.
_active_location: str = get_settings().location


class RunRequest(BaseModel):
    """Body for POST /api/run."""

    locations: list[str] = Field(min_length=1)
    limit: int | None = Field(default=12, ge=1, le=1000)
    reset: bool = True


class ScheduleRequest(BaseModel):
    """Body for POST /api/schedules."""

    locations: list[str] = Field(min_length=1)
    max_per_city: int | None = Field(default=20, ge=1, le=1000)
    mode: str = Field(pattern="^(once|daily)$")
    run_at: str | None = None  # ISO datetime for 'once'
    time_of_day: str | None = None  # 'HH:MM' for 'daily'
    reset: bool = True


def _set_progress(stage: str, message: str, processed: int, total: int) -> None:
    with _job_lock:
        _job.update(stage=stage, message=message, processed=processed, total=total)


def _run_job(
    locations: list[str],
    limit: int | None,
    reset: bool,
    schedule_id: int | None = None,
    schedule_mode: str | None = None,
) -> None:
    """Sweep one or more cities sequentially in a background thread.

    When triggered by a schedule, the schedule row is updated to reflect actual
    completion (a one-time schedule flips to ``done`` only when the scan really
    finishes, with a result summary).
    """
    global _active_location
    _active_location = ", ".join(locations)
    try:
        if reset:
            storage.clear_all()
        total_emails = 0
        count = len(locations)
        for index, location in enumerate(locations, start=1):
            settings = get_settings().model_copy(update={"location": location})

            def progress(stage, message, processed, total, _loc=location, _i=index):
                _set_progress(
                    stage, f"[{_i}/{count}] {_loc}: {message}", processed, total
                )

            result = pipeline.run_pipeline(
                settings, limit=limit, reset=False, progress=progress
            )
            total_emails += result["emails"]
        with _job_lock:
            _job.update(
                status="done",
                result={"emails": total_emails, "cities": count},
                error=None,
            )
        if schedule_id is not None:
            fields: dict[str, Any] = {
                "last_result": f"{total_emails} leads across {count} location(s)"
            }
            if schedule_mode == "once":
                fields["status"] = "done"
            storage.update_schedule(schedule_id, **fields)
    except Exception as exc:  # noqa: BLE001 - report any failure to the UI
        logger.exception("run failed")
        with _job_lock:
            _job.update(status="error", error=str(exc))
        if schedule_id is not None:
            storage.update_schedule(
                schedule_id,
                status="error" if schedule_mode == "once" else "pending",
                last_result=f"error: {exc}",
            )


def _begin_run(
    locations: list[str],
    limit: int | None,
    reset: bool,
    schedule_id: int | None = None,
    schedule_mode: str | None = None,
) -> bool:
    """Start a background run if none is in progress. Returns True if started."""
    with _job_lock:
        if _job["status"] == "running":
            return False
        _job.update(
            status="running",
            stage="queued",
            message=f"Starting scan of {len(locations)} location(s)",
            processed=0,
            total=0,
            result=None,
            error=None,
        )
    threading.Thread(
        target=_run_job,
        args=(locations, limit, reset, schedule_id, schedule_mode),
        daemon=True,
    ).start()
    return True


@app.post("/api/run")
def start_run(req: RunRequest) -> dict[str, Any]:
    """Kick off a pipeline run across one or more locations. Returns immediately."""
    started = _begin_run(req.locations, req.limit, req.reset)
    return {"status": "running" if started else "busy"}


@app.get("/api/run/status")
def run_status() -> dict[str, Any]:
    """Current background run status (polled by the dashboard)."""
    with _job_lock:
        return dict(_job)


# --- Scheduling --------------------------------------------------------------


@app.post("/api/schedules")
def create_schedule(req: ScheduleRequest) -> dict[str, Any]:
    """Create a one-time or daily scheduled scan."""
    if req.mode == "once" and not req.run_at:
        raise HTTPException(status_code=422, detail="run_at required for mode 'once'")
    if req.mode == "daily" and not req.time_of_day:
        raise HTTPException(
            status_code=422, detail="time_of_day required for mode 'daily'"
        )
    schedule_id = storage.create_schedule(
        locations=req.locations,
        max_per_city=req.max_per_city,
        mode=req.mode,
        run_at=req.run_at,
        time_of_day=req.time_of_day,
        reset=req.reset,
    )
    return {"id": schedule_id}


@app.get("/api/schedules")
def list_schedules() -> list[dict[str, Any]]:
    """List all schedules, decoding the locations JSON for the UI."""
    schedules = storage.list_schedules()
    for schedule in schedules:
        try:
            schedule["locations"] = json.loads(schedule["locations"])
        except (json.JSONDecodeError, TypeError):
            schedule["locations"] = []
    return schedules


@app.delete("/api/schedules/{schedule_id}")
def delete_schedule(schedule_id: int) -> dict[str, Any]:
    """Delete a schedule."""
    storage.delete_schedule(schedule_id)
    return {"deleted": schedule_id}


# --- Analytics ---------------------------------------------------------------

# Map a website observation sentence to a short, chartable label.
_OBSERVATION_LABELS: tuple[tuple[str, str], ...] = (
    ("viewport", "Not mobile-friendly"),
    ("mobile", "Not mobile-friendly"),
    ("meta description", "Missing SEO description"),
    ("page title", "Missing page title"),
    ("title tag", "Missing page title"),
    ("thin content", "Thin content"),
    ("table", "Outdated layout"),
    ("copyright", "Not recently updated"),
    ("recent", "Not recently updated"),
    ("images", "No images"),
)


def _classify_observation(text: str) -> str:
    lowered = text.lower()
    for keyword, label in _OBSERVATION_LABELS:
        if keyword in lowered:
            return label
    return "Other"


def _city_from_query(query: str | None) -> str:
    if query and " in " in query:
        return query.split(" in ", 1)[1].strip()
    return query or "unknown"


def _empty_analytics() -> dict[str, Any]:
    return {
        "total_businesses": 0,
        "with_website": 0,
        "without_website": 0,
        "total_emails": 0,
        "by_category": [],
        "by_city": [],
        "email_types": {"improve_site": 0, "build_site": 0},
        "top_issues": [],
    }


@app.get("/api/analytics")
def analytics() -> dict[str, Any]:
    """Aggregated metrics for the analytics dashboard charts."""
    connection = _connect()
    if connection is None:
        return _empty_analytics()
    try:
        businesses = connection.execute(
            "SELECT category, has_website, source_query, website_observations "
            "FROM businesses"
        ).fetchall()
        email_rows = connection.execute(
            "SELECT email_type, COUNT(*) AS c FROM emails GROUP BY email_type"
        ).fetchall()
        total_emails = connection.execute(
            "SELECT COUNT(*) AS c FROM emails"
        ).fetchone()["c"]
    finally:
        connection.close()

    by_category: dict[str, dict[str, int]] = {}
    by_city: dict[str, dict[str, int]] = {}
    issue_counts: dict[str, int] = {}

    for row in businesses:
        has = 1 if row["has_website"] else 0
        category = row["category"] or "other"
        cat = by_category.setdefault(category, {"count": 0, "with": 0})
        cat["count"] += 1
        cat["with"] += has

        city = _city_from_query(row["source_query"])
        city_entry = by_city.setdefault(city, {"count": 0, "with": 0})
        city_entry["count"] += 1
        city_entry["with"] += has

        try:
            observations = json.loads(row["website_observations"] or "[]")
        except (json.JSONDecodeError, TypeError):
            observations = []
        for observation in observations:
            label = _classify_observation(observation)
            issue_counts[label] = issue_counts.get(label, 0) + 1

    email_types = {"improve_site": 0, "build_site": 0}
    for row in email_rows:
        email_types[row["email_type"]] = row["c"]

    with_website = sum(entry["with"] for entry in by_category.values())
    total = sum(entry["count"] for entry in by_category.values())

    return {
        "total_businesses": total,
        "with_website": with_website,
        "without_website": total - with_website,
        "total_emails": total_emails,
        "by_category": [
            {
                "name": name,
                "count": entry["count"],
                "with_website": entry["with"],
                "without_website": entry["count"] - entry["with"],
            }
            for name, entry in sorted(
                by_category.items(), key=lambda kv: kv[1]["count"], reverse=True
            )
        ],
        "by_city": [
            {"city": city, "count": entry["count"], "with_website": entry["with"]}
            for city, entry in sorted(
                by_city.items(), key=lambda kv: kv[1]["count"], reverse=True
            )
        ],
        "email_types": email_types,
        "top_issues": [
            {"label": label, "count": count}
            for label, count in sorted(
                issue_counts.items(), key=lambda kv: kv[1], reverse=True
            )
        ],
    }


def _check_schedules() -> None:
    """Trigger any due schedules. Called on each scheduler tick."""
    now = datetime.now()
    today = now.date().isoformat()
    for schedule in storage.list_schedules():
        if schedule["status"] != "pending":
            continue
        due = False
        if schedule["mode"] == "once" and schedule.get("run_at"):
            try:
                due = now >= datetime.fromisoformat(schedule["run_at"])
            except ValueError:
                continue
        elif schedule["mode"] == "daily" and schedule.get("time_of_day"):
            last = schedule.get("last_run")
            last_date = last.split("T")[0] if last else None
            due = (
                now.strftime("%H:%M") == schedule["time_of_day"]
                and last_date != today
            )
        if not due:
            continue

        locations = json.loads(schedule["locations"])
        started = _begin_run(
            locations,
            schedule.get("max_per_city"),
            bool(schedule["reset"]),
            schedule_id=schedule["id"],
            schedule_mode=schedule["mode"],
        )
        if started:
            updates: dict[str, Any] = {"last_run": now.isoformat()}
            # 'running' until the scan actually completes (set in _run_job).
            if schedule["mode"] == "once":
                updates["status"] = "running"
            storage.update_schedule(schedule["id"], **updates)
            logger.info("Triggered scheduled scan %s for %s", schedule["id"], locations)


def _scheduler_loop() -> None:
    """Background loop that checks for due schedules every 30s."""
    while True:
        try:
            _check_schedules()
        except Exception:  # noqa: BLE001 - never let the scheduler thread die
            logger.exception("scheduler tick failed")
        time.sleep(30)


@app.on_event("startup")
def _start_scheduler() -> None:
    storage.init_db()
    threading.Thread(target=_scheduler_loop, daemon=True).start()
    logger.info("Scheduler started")


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
        "location": _active_location,
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
        "location": _active_location,
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
