"""Shared pipeline orchestrator used by both the CLI and the web dashboard.

Runs the stages in order and reports progress through an optional callback so a
caller (e.g. the dashboard's background job) can surface live status. Keeping
this in one place means ``run_local.py`` and ``webapp/server.py`` drive the
exact same logic.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Callable
from itertools import chain, zip_longest

from config import Settings, get_settings
from tasks import cluster, email_generator, search, storage, website_checker
from tasks.models import Business, Email

logger = logging.getLogger(__name__)

# A progress callback: (stage, message, processed, total) -> None.
ProgressFn = Callable[[str, str, int, int], None]


def _noop(stage: str, message: str, processed: int, total: int) -> None:
    """Default progress sink that does nothing."""


def _interleave_by_category(businesses: list[Business]) -> list[Business]:
    """Round-robin businesses across their search categories.

    ``search_all`` returns results grouped by category (all restaurants, then
    all dentists, ...). Interleaving ensures a ``limit`` produces an even spread
    across categories instead of filling up from the first one.
    """
    groups: "OrderedDict[str, list[Business]]" = OrderedDict()
    for business in businesses:
        groups.setdefault(business.search_category, []).append(business)
    rounds = zip_longest(*groups.values())
    return [business for business in chain.from_iterable(rounds) if business is not None]


def run_pipeline(
    settings: Settings | None = None,
    *,
    limit: int | None = None,
    reset: bool = False,
    progress: ProgressFn | None = None,
) -> dict[str, int]:
    """Run search -> cluster -> audit -> draft -> store.

    Args:
        settings: Settings to use (defaults to :func:`get_settings`).
        limit: Optional cap on businesses processed after search.
        reset: If True, clear existing rows before the run (fresh location).
        progress: Optional callback invoked at each stage with
            ``(stage, message, processed, total)``.

    Returns:
        A dict with the ``businesses`` and ``emails`` counts written.
    """
    cfg = settings or get_settings()
    emit = progress or _noop

    if reset:
        emit("reset", "Clearing previous results", 0, 0)
        storage.clear_all(settings=cfg)

    emit("search", f"Searching {cfg.location}", 0, 0)
    raw: list[Business] = _interleave_by_category(search.search_all(settings=cfg))
    if limit is not None:
        raw = raw[:limit]
    total = len(raw)
    emit("cluster", f"Found {total} businesses, clustering", 0, total)

    clustered = cluster.cluster_businesses(raw)

    emit("audit", "Auditing websites", 0, total)
    checked: list[Business] = []
    for index, business in enumerate(clustered, start=1):
        checked.append(website_checker.check_business(business, settings=cfg))
        emit("audit", f"Audited {index}/{total}", index, total)
    storage.save_businesses(checked, settings=cfg)

    emit("draft", "Drafting emails", 0, total)
    emails: list[Email] = []
    for index, business in enumerate(checked, start=1):
        emails.append(email_generator.generate_email(business, settings=cfg))
        emit("draft", f"Drafted {index}/{total}", index, total)
    storage.save_emails(emails, settings=cfg)

    emit("done", f"Done: {len(emails)} leads ready", total, total)
    logger.info("Pipeline complete: %d businesses, %d emails", len(checked), len(emails))
    return {"businesses": len(checked), "emails": len(emails)}
