"""Airflow DAG wiring the lead generation pipeline.

Each pipeline stage is its own task. Data flows between tasks via XCom as lists
of plain dicts (Pydantic models are dumped to dicts on the way out and
re-validated on the way in), keeping payloads JSON serializable.

The repository root is added to ``sys.path`` so the ``tasks`` package and
``config`` module resolve the same way they do for ``run_local.py``.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

from airflow.decorators import dag, task

# Make `config` and `tasks` importable inside the Airflow workers.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import export  # noqa: E402
from config import get_settings  # noqa: E402
from tasks import (  # noqa: E402
    cluster,
    email_generator,
    search,
    storage,
    website_checker,
)
from tasks.models import Business, Email  # noqa: E402

DEFAULT_ARGS = {
    "owner": "lead_gen",
    "retries": 2,
    "retry_delay": timedelta(minutes=2),
}


@dag(
    dag_id="lead_gen_pipeline",
    description="Discover local businesses and draft personalized cold emails.",
    schedule=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    default_args=DEFAULT_ARGS,
    tags=["lead-gen", "ollama"],
)
def lead_gen_pipeline() -> None:
    """Define the task graph: search -> cluster -> check -> email -> store."""

    @task
    def search_businesses() -> list[dict]:
        """Search Google for every configured category."""
        settings = get_settings()
        results = search.search_all(settings=settings)
        return [business.model_dump() for business in results]

    @task
    def cluster_businesses(raw: list[dict]) -> list[dict]:
        """Assign a normalized category to each business."""
        businesses = [Business.model_validate(item) for item in raw]
        clustered = cluster.cluster_businesses(businesses)
        return [business.model_dump() for business in clustered]

    @task
    def check_websites(clustered: list[dict]) -> list[dict]:
        """Determine which businesses have a usable website and analyze it."""
        settings = get_settings()
        businesses = [Business.model_validate(item) for item in clustered]
        checked = website_checker.check_all(businesses, settings=settings)
        return [business.model_dump() for business in checked]

    @task
    def persist_businesses(checked: list[dict]) -> list[dict]:
        """Save checked businesses to SQLite, passing them through unchanged."""
        settings = get_settings()
        businesses = [Business.model_validate(item) for item in checked]
        storage.save_businesses(businesses, settings=settings)
        return checked

    @task
    def generate_emails(checked: list[dict]) -> list[dict]:
        """Generate a personalized cold email for each business via Ollama."""
        settings = get_settings()
        businesses = [Business.model_validate(item) for item in checked]
        emails = email_generator.generate_all(businesses, settings=settings)
        return [email.model_dump(mode="json") for email in emails]

    @task
    def persist_emails(emails: list[dict]) -> int:
        """Save generated emails to SQLite. Returns the count written."""
        settings = get_settings()
        records = [Email.model_validate(item) for item in emails]
        return storage.save_emails(records, settings=settings)

    @task
    def export_leads(_saved_count: int) -> int:
        """Append newly generated emails to leads_export.csv. Returns count."""
        settings = get_settings()
        return export.export_leads(settings=settings)

    raw = search_businesses()
    clustered = cluster_businesses(raw)
    checked = check_websites(clustered)
    persisted = persist_businesses(checked)
    emails = generate_emails(persisted)
    saved = persist_emails(emails)
    export_leads(saved)


lead_gen_pipeline()
