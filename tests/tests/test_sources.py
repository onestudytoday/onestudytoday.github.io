"""
Sourcing resilience tests. Europe PMC and arXiv are outside our control -
a timeout or a 500 from either one used to take the whole weekday job down
with an uncaught exception, before vetting or drafting ever ran. That is a
silent failure: no queued post, no "no post today" issue, just a red X and
whoever set this up left wondering why.

These prove `fetch_candidates` survives a broken source and keeps going with
whatever else it has, instead of crashing the run.

    python -m pytest tests/ -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import sources                                  # noqa: E402
from sources import fetch_candidates             # noqa: E402


def _quiet_ledger(monkeypatch):
    # fetch_candidates consults the ledger to drop already-used studies;
    # keep it empty and inert so these tests don't touch data/ledger.json.
    monkeypatch.setattr(sources, "load_ledger",
                         lambda: {"posted": {}, "rejected": {}, "seen": {}})


def test_europepmc_failure_does_not_crash_the_run(monkeypatch):
    _quiet_ledger(monkeypatch)

    def boom(*a, **kw):
        raise sources.requests.exceptions.ConnectionError("EPMC is down")
    monkeypatch.setattr(sources, "europepmc_search", boom)

    # "nature" has no arxiv_categories, so europepmc is its only source -
    # if the isolation works this returns [] instead of raising.
    result = fetch_candidates("nature", days=14)
    assert result == []


def test_arxiv_failure_does_not_crash_the_run(monkeypatch):
    _quiet_ledger(monkeypatch)

    def boom(*a, **kw):
        raise sources.requests.exceptions.Timeout("arXiv timed out")
    monkeypatch.setattr(sources, "arxiv_search", boom)

    # "physics" has no europepmc_query, so arXiv is its only source.
    result = fetch_candidates("physics", days=14)
    assert result == []


def test_one_source_failing_does_not_drop_the_other(monkeypatch):
    _quiet_ledger(monkeypatch)

    def boom(*a, **kw):
        raise sources.requests.exceptions.HTTPError("502 from Europe PMC")
    monkeypatch.setattr(sources, "europepmc_search", boom)

    good = [sources.Study(
        source="arxiv", ext_id="9999.99999", title="A physics thing",
        abstract="x" * 900, journal="arXiv", pub_date="2026-08-10",
    )]
    monkeypatch.setattr(sources, "arxiv_search", lambda *a, **kw: list(good))

    # Fabricate a niche with both an EPMC query and arxiv categories so both
    # branches actually run - no shipped niche does both at once.
    monkeypatch.setattr(sources, "load_niches", lambda: {
        "defaults": {"recency_days": 14, "per_source_limit": 60,
                     "min_abstract_chars": 500, "max_candidates": 25},
        "niches": {"both": {"europepmc_query": "test", "arxiv_categories": ["astro-ph.GA"],
                             "exclude_terms": []}},
    })

    result = fetch_candidates("both", days=14)
    assert len(result) == 1
    assert result[0].source == "arxiv"
