"""Behavior tests for business type clustering."""

from __future__ import annotations

import pytest

from tasks import cluster
from tests.conftest import make_business


@pytest.mark.parametrize(
    ("search_category", "title", "expected"),
    [
        ("restaurants", "Joe's Pizza Kitchen", "restaurant"),
        ("dentists", "Smith Family Dental Clinic", "medical"),
        ("law firms", "Acme Attorneys at Law", "legal"),
        ("hair salons", "Glow Beauty Salon", "beauty"),
        ("gyms", "Iron Fitness CrossFit", "fitness"),
        ("plumbers", "Reliable Plumbing & HVAC", "construction"),
        ("auto repair", "Joe's Auto Mechanic Shop", "automotive"),
        ("accountants", "Downtown Accounting & Tax", "professional_services"),
        ("clothing", "Bella Boutique Store", "retail"),
    ],
)
def test_classify_assigns_expected_cluster(
    search_category: str, title: str, expected: str
) -> None:
    business = make_business(search_category=search_category, title=title, name=title)
    assert cluster.classify(business) == expected


def test_classify_falls_back_to_other_when_no_keywords_match() -> None:
    business = make_business(
        search_category="widgets",
        name="Zzyzx Holdings",
        title="Zzyzx Holdings",
        description="A nondescript entity.",
    )
    assert cluster.classify(business) == cluster.DEFAULT_CATEGORY


def test_classify_uses_strongest_signal_on_ambiguous_text() -> None:
    business = make_business(
        search_category="restaurants",
        name="The Coffee Bar & Grill Cafe Bistro",
        title="Restaurant Cafe Diner",
    )
    assert cluster.classify(business) == "restaurant"


def test_cluster_businesses_populates_category_without_mutating_input() -> None:
    original = make_business(search_category="dentists", title="City Dental")
    clustered = cluster.cluster_businesses([original])

    assert clustered[0].category == "medical"
    assert original.category is None


def test_cluster_businesses_preserves_count() -> None:
    businesses = [
        make_business(name="A", title="Pizza Place"),
        make_business(name="B", title="Dental Clinic"),
        make_business(name="C", title="Unknown Thing", search_category="misc"),
    ]
    clustered = cluster.cluster_businesses(businesses)
    assert len(clustered) == 3
