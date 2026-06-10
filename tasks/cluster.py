"""Cluster businesses into normalized type categories via keyword matching.

This is intentionally simple and dependency-free: each business is scored
against a keyword map drawn from its search category, name, title, and
description, and assigned to the best-matching cluster.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from tasks.models import Business

logger = logging.getLogger(__name__)

# Cluster -> keywords. Order matters only for tie-breaking (first wins).
CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "restaurant": (
        "restaurant", "cafe", "coffee", "bar", "grill", "diner", "bistro",
        "pizza", "sushi", "taco", "bakery", "eatery", "kitchen", "brewery",
        "food", "steakhouse", "deli",
    ),
    "retail": (
        "shop", "store", "boutique", "retail", "market", "clothing",
        "furniture", "jewelry", "florist", "bookstore", "grocery", "apparel",
    ),
    "medical": (
        "dental", "dentist", "doctor", "clinic", "medical", "health",
        "physician", "orthodontic", "chiropractic", "pediatric", "veterinary",
        "vet", "pharmacy", "optometry", "dermatology",
    ),
    "legal": (
        "law", "lawyer", "attorney", "legal", "firm", "counsel", "litigation",
        "paralegal", "notary",
    ),
    "construction": (
        "construction", "plumbing", "plumber", "electric", "electrician",
        "roofing", "hvac", "contractor", "remodeling", "builder", "landscaping",
        "painting", "concrete", "carpentry",
    ),
    "beauty": (
        "salon", "spa", "beauty", "hair", "nail", "barber", "cosmetic",
        "makeup", "lash", "skincare", "esthetic",
    ),
    "fitness": (
        "gym", "fitness", "yoga", "pilates", "crossfit", "training",
        "wellness", "martial arts", "dance", "cycling",
    ),
    "automotive": (
        "auto", "car", "mechanic", "tire", "automotive", "repair shop",
        "body shop", "detailing", "dealership",
    ),
    "professional_services": (
        "accounting", "accountant", "consulting", "insurance", "real estate",
        "realtor", "marketing", "agency", "financial", "tax", "bookkeeping",
    ),
}

DEFAULT_CATEGORY = "other"


def _searchable_text(business: Business) -> str:
    """Concatenate the business fields used for keyword matching."""
    parts = [
        business.search_category,
        business.name,
        business.title or "",
        business.description or "",
    ]
    return " ".join(parts).lower()


def classify(business: Business) -> str:
    """Return the best-matching cluster for a single business.

    Args:
        business: The business to classify.

    Returns:
        The normalized cluster name, or :data:`DEFAULT_CATEGORY` if no keyword
        matches.
    """
    text = _searchable_text(business)
    scores: dict[str, int] = {}
    for cluster, keywords in CATEGORY_KEYWORDS.items():
        score = sum(1 for keyword in keywords if keyword in text)
        if score:
            scores[cluster] = score

    if not scores:
        return DEFAULT_CATEGORY
    return max(scores, key=lambda cluster: scores[cluster])


def cluster_businesses(businesses: list[Business]) -> list[Business]:
    """Assign a :attr:`~tasks.models.Business.category` to each business.

    Args:
        businesses: Businesses to cluster (not mutated; copies are returned).

    Returns:
        New list of businesses with the ``category`` field populated.
    """
    clustered = [
        business.model_copy(update={"category": classify(business)})
        for business in businesses
    ]

    counts: dict[str, int] = defaultdict(int)
    for business in clustered:
        counts[business.category or DEFAULT_CATEGORY] += 1
    logger.info("Clustered %d businesses: %s", len(clustered), dict(counts))

    return clustered
