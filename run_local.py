"""Run the full lead generation pipeline locally, without Airflow.

Usage:
    python run_local.py
    python run_local.py --location "Denver, CO" --categories restaurants gyms
    python run_local.py --limit 5 --dry-run-search

This orchestrates the same task functions the Airflow DAG calls, which makes it
the fastest way to smoke-test the pipeline end to end.
"""

from __future__ import annotations

import argparse
import logging
import sys

from config import Settings, get_settings
from tasks import (
    cluster,
    email_generator,
    ollama_client,
    search,
    storage,
    website_checker,
)
from tasks.models import Business


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the lead gen pipeline locally.")
    parser.add_argument("--location", help="Override the search location.")
    parser.add_argument(
        "--categories", nargs="+", help="Override the list of categories."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the total number of businesses processed (for quick tests).",
    )
    parser.add_argument(
        "--skip-ollama-check",
        action="store_true",
        help="Do not abort if the Ollama server is unreachable.",
    )
    return parser.parse_args(argv)


def _build_settings(args: argparse.Namespace) -> Settings:
    base = get_settings()
    overrides: dict[str, object] = {}
    if args.location:
        overrides["location"] = args.location
    if args.categories:
        overrides["categories"] = args.categories
    return base.model_copy(update=overrides) if overrides else base


def run_pipeline(settings: Settings, *, limit: int | None = None) -> None:
    """Execute search -> cluster -> website check -> email -> store."""
    log = logging.getLogger("run_local")

    raw: list[Business] = search.search_all(settings=settings)
    if limit is not None:
        raw = raw[:limit]
    log.info("Discovered %d businesses", len(raw))

    clustered = cluster.cluster_businesses(raw)
    checked = website_checker.check_all(clustered, settings=settings)
    storage.save_businesses(checked, settings=settings)

    emails = email_generator.generate_all(checked, settings=settings)
    storage.save_emails(emails, settings=settings)

    _print_summary(emails)


def _print_summary(emails: list) -> None:
    print("\n" + "=" * 70)
    print(f"GENERATED {len(emails)} EMAILS")
    print("=" * 70)
    for email in emails:
        print(f"\n[{email.email_type}] {email.business_name} ({email.category})")
        print(f"Subject: {email.subject}")
        print("-" * 70)
        print(email.body)
    print("\n" + "=" * 70)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    args = _parse_args(argv)
    settings = _build_settings(args)

    if not args.skip_ollama_check and not ollama_client.health_check(settings):
        print(
            f"ERROR: Ollama not reachable at {settings.ollama_base_url}.\n"
            "Start it with `ollama serve` and pull a model "
            f"(`ollama pull {settings.ollama_model}`),\n"
            "or pass --skip-ollama-check to use the templated fallback emails.",
            file=sys.stderr,
        )
        return 1

    run_pipeline(settings, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
