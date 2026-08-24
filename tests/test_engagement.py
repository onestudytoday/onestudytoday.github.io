"""
Projected-engagement ranking tests.

Added 24 Aug 2026 alongside sources.engagement_proxy() / altmetric_score():
sourcing now leans toward whichever already-eligible candidate looks likely
to land best, using citation count + open-access status for free, or a real
Altmetric attention score when ALTMETRIC_API_KEY is configured. These pin
both paths, and specifically that no key means no network call at all.

    python -m pytest tests/ -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import sources                                                    # noqa: E402
from sources import (Study, altmetric_score, engagement_proxy,    # noqa: E402
                     fetch_candidates)


def _study(citations=0, open_access=False, pub_date="2026-08-20", doi="10.1/x"):
    return Study(
        source="europepmc", ext_id="MED:1", title="A study of things",
        abstract="x" * 900, journal="Nature", pub_date=pub_date, doi=doi,
        citations=citations, open_access=open_access,
    )


# ---------------------------------------------------------------- no key set
def test_no_altmetric_key_never_calls_the_network(monkeypatch):
    monkeypatch.delenv("ALTMETRIC_API_KEY", raising=False)

    def boom(*a, **kw):
        raise AssertionError("_get should never be called with no key configured")
    monkeypatch.setattr(sources, "_get", boom)

    assert altmetric_score("10.1/whatever") is None


def test_no_doi_never_calls_the_network(monkeypatch):
    monkeypatch.setenv("ALTMETRIC_API_KEY", "test-key")

    def boom(*a, **kw):
        raise AssertionError("_get should never be called with no DOI")
    monkeypatch.setattr(sources, "_get", boom)

    assert altmetric_score("") is None


# --------------------------------------------------------- free-signal proxy
def test_more_citations_ranks_higher(monkeypatch):
    monkeypatch.delenv("ALTMETRIC_API_KEY", raising=False)
    low = _study(citations=0)
    high = _study(citations=20)
    assert engagement_proxy(high) > engagement_proxy(low)


def test_open_access_is_a_small_boost(monkeypatch):
    monkeypatch.delenv("ALTMETRIC_API_KEY", raising=False)
    closed = _study(citations=3, open_access=False)
    open_ = _study(citations=3, open_access=True)
    assert engagement_proxy(open_) > engagement_proxy(closed)


def test_two_untracked_candidates_score_identically(monkeypatch):
    # The common case for a paper published in the last 14 days: no
    # citations, not open access. Ranking must fall back to whatever
    # fetch_candidates() does next (recency, already applied before this
    # runs) rather than this function inventing a difference.
    monkeypatch.delenv("ALTMETRIC_API_KEY", raising=False)
    a = _study(doi="10.1/a")
    b = _study(doi="10.1/b")
    assert engagement_proxy(a) == engagement_proxy(b)


# ------------------------------------------------------- altmetric overrides
def test_altmetric_signal_outranks_the_free_fallback(monkeypatch):
    monkeypatch.setattr(sources, "altmetric_score", lambda doi: 2.0 if doi == "10.1/buzzy" else None)
    buzzy = _study(citations=0, open_access=False, doi="10.1/buzzy")
    heavily_cited_but_untracked = _study(citations=50, open_access=True, doi="10.1/other")
    assert engagement_proxy(buzzy) > engagement_proxy(heavily_cited_but_untracked)


# --------------------------------------------------- wired into fetch_candidates
def test_fetch_candidates_reorders_by_engagement_within_the_recency_cut(monkeypatch):
    monkeypatch.setattr(sources, "load_niches", lambda: {
        "defaults": {"recency_days": 14, "per_source_limit": 60,
                     "min_abstract_chars": 500, "max_candidates": 25},
        "niches": {"nature": {"europepmc_query": "test", "arxiv_categories": [],
                              "exclude_terms": []}},
    })
    monkeypatch.setattr(sources, "load_ledger",
                        lambda: {"posted": {}, "rejected": {}, "seen": {}})
    monkeypatch.delenv("ALTMETRIC_API_KEY", raising=False)

    newer_but_ignored = _study(citations=0, pub_date="2026-08-21", doi="10.1/fresh")
    older_but_buzzing = _study(citations=40, pub_date="2026-08-10", doi="10.1/buzzing")
    monkeypatch.setattr(sources, "europepmc_search",
                        lambda *a, **kw: [newer_but_ignored, older_but_buzzing])

    result = fetch_candidates("nature", days=14)
    assert [s.doi for s in result] == ["10.1/buzzing", "10.1/fresh"]
