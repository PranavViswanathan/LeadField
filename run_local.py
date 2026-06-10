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

import pipeline
from config import Settings, get_settings
from tasks import ollama_client, storage


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
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear existing results before running (fresh location).",
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


def run_pipeline(
    settings: Settings, *, limit: int | None = None, reset: bool = False
) -> None:
    """Execute the full pipeline and print a summary of the drafted emails."""
    pipeline.run_pipeline(settings, limit=limit, reset=reset)
    _print_summary(storage.fetch_emails(settings=settings))


def _print_summary(emails: list[dict]) -> None:
    print("\n" + "=" * 70)
    print(f"GENERATED {len(emails)} EMAILS")
    print("=" * 70)
    for email in emails:
        print(
            f"\n[{email['email_type']}] {email['business_name']} ({email['category']})"
        )
        print(f"Subject: {email['subject']}")
        print("-" * 70)
        print(email["body"])
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

    run_pipeline(settings, limit=args.limit, reset=args.reset)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
