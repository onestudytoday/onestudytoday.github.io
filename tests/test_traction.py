"""
Sourcing studies that a lay audience already voted on.

The value is obvious: r/science upvotes are the "would a non-scientist care?"
question answered by a very large number of non-scientists, which is strictly
better evidence than a model's guess at the same thing.

The risk is equally obvious and is what most of this file tests. Popularity is
the least trustworthy signal in the repo - what goes viral in science media
skews hard towards the overstated, the single-study-overturns-everything, and
the headline the paper does not actually support. So traction is wired to
decide what gets LOOKED AT and nothing about what gets ALLOWED. Every check
that applied before applies afterwards, unchanged.

If a future change ever makes a traction-sourced study skip a check,
test_traction_cannot_bypass_vetting is the one that should go red.

    python -m pytest tests/test_traction.py -q
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import sources  # noqa: E402
import traction  # noqa: E402
from sources import Study  # noqa: E402


def _hit(**kw):
    kw.setdefault("source", "reddit:science")
    return traction.TractionHit(**kw)


def _record(calls, *studies):
    """Stub body that also proves the stub actually ran.

    These stubs previously took (niche, days, limit) while fetch_candidates
    had started passing `hits=` too. Every call raised TypeError, which
    fetch_candidates catches and prints - so the stub never ran, traction
    contributed nothing, and the test asserting "the popular study was
    filtered out" passed because it was never let in. The `calls` list is
    what stops that from being invisible a second time.
    """
    calls.append(True)
    return list(studies)


def _study(title="Sleep loss and memory in adults", doi="10.1038/abc123",
           abstract="x" * 900, **kw):
    return Study(source="europepmc", ext_id="1", title=title, abstract=abstract,
                 journal="Nature", pub_date="2026-09-10", doi=doi, **kw)


# ---------------------------------------------------------------------------
# Reading a DOI out of the wild
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,want", [
    ("https://doi.org/10.1038/s41586-024-07123-7", "10.1038/s41586-024-07123-7"),
    ("http://dx.doi.org/10.1016/J.CELL.2024.01.001", "10.1016/j.cell.2024.01.001"),
    ("doi: 10.1038/nature12373", "10.1038/nature12373"),
    # Trailing punctuation is the common real-world case: a DOI at the end of
    # a sentence in a reddit selftext. With the full stop attached it resolves
    # to nothing, silently, and the study is simply never found.
    ("the paper is 10.1038/nature12373.", "10.1038/nature12373"),
    ("(see 10.1038/nature12373)", "10.1038/nature12373"),
    ("no identifier here", ""),
    ("10.notadoi/x", ""),
    ("", ""),
])
def test_doi_extraction(raw, want):
    assert traction.clean_doi(raw) == want


def test_several_dois_in_one_blob_keep_their_order():
    text = "compare 10.1038/first with 10.1016/second and again 10.1038/first"
    assert traction.dois_in(text) == ["10.1038/first", "10.1016/second"]


# ---------------------------------------------------------------------------
# Failing closed
# ---------------------------------------------------------------------------
def test_every_source_being_down_yields_nothing_and_does_not_raise(monkeypatch):
    """A weekday post must not fail to exist because reddit is having a day."""
    def boom(*a, **k):
        raise RuntimeError("network on fire")
    monkeypatch.setattr(traction, "_get_json", boom)
    monkeypatch.setattr(traction.requests, "get", boom)

    assert traction.gather() == []
    assert traction.discover_dois(hits=[]) == []
    assert traction.crossref_event_hits("10.1038/x123") == 0.0


def test_one_source_failing_does_not_take_the_others_with_it(monkeypatch):
    monkeypatch.setattr(traction, "reddit_hits",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(traction, "hn_hits", lambda *a, **k: [_hit(source="hn",
                                                                  doi="10.1038/x123")])
    monkeypatch.setattr(traction, "rss_hits", lambda *a, **k: [])
    with pytest.raises(RuntimeError):
        traction.gather()           # gather does not swallow; the caller does
    # ...and the caller does:
    monkeypatch.setattr(sources, "study_from_doi", lambda d: None)
    assert sources.traction_candidates("psych", 30) == []


def test_a_malformed_reddit_payload_is_survivable(monkeypatch):
    monkeypatch.setattr(traction, "_get_json",
                        lambda *a, **k: {"data": {"children": [None, 7, {}]}})
    # Should not raise; unusable rows are simply skipped or yield empty hits.
    hits = traction.reddit_hits(subs=["science"])
    assert all(isinstance(h, traction.TractionHit) for h in hits)


# ---------------------------------------------------------------------------
# Never guess which paper it was
# ---------------------------------------------------------------------------
def test_a_hit_with_no_doi_is_never_turned_into_a_candidate():
    """The dangerous version of this feature resolves a headline by search.

    "Scientists link coffee to memory" would confidently return SOME paper,
    and the pipeline would draft a post about a study nobody upvoted, with the
    upvote count as its justification. Wrong-but-confident is worse than
    finding nothing, so a hit without a real DOI is dropped.
    """
    hits = [_hit(title="Scientists link coffee to better memory", score=9000),
            _hit(doi="10.1038/real123", title="A real one", score=400)]
    found = traction.discover_dois(hits=hits)
    assert [d["doi"] for d in found] == ["10.1038/real123"]


def test_low_scoring_hits_are_left_out():
    hits = [_hit(doi="10.1038/meh123", score=5)]          # ~0.4 normalised
    assert traction.discover_dois(hits=hits, min_score=3.0) == []


def test_corroboration_adds_but_is_capped():
    """Two sources agreeing is worth more than one - but not unboundedly."""
    hits = [_hit(doi="10.1038/x123", score=5000),
            _hit(doi="10.1038/x123", score=5000, source="reddit:health"),
            _hit(doi="10.1038/x123", score=900, source="hn"),
            _hit(doi="10.1038/x123", source="eurekalert")]
    found = traction.discover_dois(hits=hits)
    assert len(found) == 1
    assert found[0]["score"] <= traction.REDDIT_SCORE_CAP * 1.5


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def test_an_exact_doi_match_scores():
    s = _study(doi="10.1038/abc123")
    assert traction.traction_score(s, [_hit(doi="10.1038/abc123", score=5000)]) > 0


def test_a_study_nobody_posted_about_is_not_penalised():
    """Scores 0, not negative. Most good papers never reach reddit, and an
    account that only posted what was already popular is an aggregator."""
    assert traction.traction_score(_study(), [_hit(doi="10.1016/other123")]) == 0.0


def test_a_loose_headline_match_does_not_hand_over_the_score():
    """News headlines and paper titles share little vocabulary. A two-word
    overlap must not transfer a viral story's score to an unrelated paper."""
    s = _study(title="Hippocampal consolidation during slow-wave sleep", doi="")
    loose = [_hit(title="Sleep is important, scientists say", score=9000)]
    assert traction.traction_score(s, loose) == 0.0


def test_a_strong_title_match_scores_but_less_than_a_doi_match():
    s = _study(title="Sleep restriction impairs memory consolidation in adults",
               doi="10.1038/abc123")
    title_only = [_hit(title="Sleep restriction impairs memory consolidation "
                             "in healthy adults", score=5000)]
    exact = [_hit(doi="10.1038/abc123", score=5000)]
    assert 0 < traction.traction_score(s, title_only) < \
        traction.traction_score(s, exact)


# ---------------------------------------------------------------------------
# THE ONE THAT MATTERS
# ---------------------------------------------------------------------------
def test_traction_cannot_bypass_vetting(monkeypatch, tmp_path):
    """A study that arrives via traction is an ORDINARY candidate.

    It is subject to the ledger, the abstract-length floor and the exclusion
    terms exactly as a topic-search result is. If this ever fails, popularity
    has become a way around a check.
    """
    calls = []
    popular_but_thin = _study(title="A wildly popular paper", doi="10.1038/pop123",
                              abstract="too short to summarise honestly")
    monkeypatch.setattr(sources, "traction_candidates",
                        lambda niche, days, limit=12, hits=None: _record(calls, popular_but_thin))
    monkeypatch.setattr(sources, "europepmc_search", lambda *a, **k: [])
    monkeypatch.setattr(sources, "arxiv_search", lambda *a, **k: [])
    monkeypatch.setattr(sources, "load_ledger", lambda: {})
    monkeypatch.setattr(sources, "interest_rank", lambda studies: studies)

    out = sources.fetch_candidates("psych", days=75, rank_by_interest=False)
    assert calls, "the traction stub never ran - this test proved nothing"
    assert out == [], "a short-abstract study got through because it was popular"


def test_a_traction_study_already_in_the_ledger_is_still_skipped(monkeypatch):
    calls = []
    seen = _study(title="Already used", doi="10.1038/used123")
    monkeypatch.setattr(sources, "traction_candidates",
                        lambda niche, days, limit=12, hits=None: _record(calls, seen))
    monkeypatch.setattr(sources, "europepmc_search", lambda *a, **k: [])
    monkeypatch.setattr(sources, "arxiv_search", lambda *a, **k: [])
    monkeypatch.setattr(sources, "interest_rank", lambda studies: studies)
    monkeypatch.setattr(sources, "load_ledger",
                        lambda: {"posted": {seen.key: {"media_id": "1"}}})

    out = sources.fetch_candidates("psych", days=75, rank_by_interest=False)
    assert calls, "the traction stub never ran - this test proved nothing"
    assert out == []


def test_traction_can_be_switched_off(monkeypatch):
    called = []
    monkeypatch.setattr(sources, "traction_candidates",
                        lambda *a, **k: called.append(1) or [])
    monkeypatch.setattr(sources, "europepmc_search", lambda *a, **k: [])
    monkeypatch.setattr(sources, "arxiv_search", lambda *a, **k: [])
    monkeypatch.setattr(sources, "load_ledger", lambda: {})
    sources.fetch_candidates("psych", days=75, rank_by_interest=False,
                             use_traction=False)
    assert called == []


def test_a_traction_sourced_study_records_where_it_came_from(monkeypatch):
    """So the review card can say why this study is in front of you."""
    monkeypatch.setattr(sources, "study_from_doi",
                        lambda doi: _study(doi=doi, title="Resolved"))
    import traction as t
    monkeypatch.setattr(t, "discover_dois",
                        lambda **k: [{"doi": "10.1038/x123", "score": 18.0,
                                      "source": "reddit:science",
                                      "url": "https://r.test/p", "when": ""}])
    out = sources.traction_candidates("psych", 30)
    assert len(out) == 1
    assert out[0].raw["traction"]["source"] == "reddit:science"
    assert out[0].interest_source == "traction"


def test_a_doi_that_resolves_to_nothing_is_dropped(monkeypatch):
    monkeypatch.setattr(sources, "study_from_doi", lambda doi: None)
    import traction as t
    monkeypatch.setattr(t, "discover_dois",
                        lambda **k: [{"doi": "10.1038/ghost123", "score": 18.0,
                                      "source": "hn", "url": "", "when": ""}])
    assert sources.traction_candidates("psych", 30) == []


def test_study_from_doi_refuses_things_that_are_not_dois():
    assert sources.study_from_doi("") is None
    assert sources.study_from_doi("not-a-doi") is None
    assert sources.study_from_doi("../../etc/passwd") is None


# ---------------------------------------------------------------------------
# The review card
# ---------------------------------------------------------------------------
def test_the_card_says_why_this_study_was_chosen():
    import issue as issue_mod
    base = {"study": {"traction": {"source": "reddit:science", "score": 18.0}}}
    assert "reddit:science" in issue_mod._traction_line(base)
    assert "found by topic search" in issue_mod._traction_line({"study": {}})
    assert "found by topic search" in issue_mod._traction_line({})


def test_the_traction_source_is_defanged_on_the_card():
    """It is a string built from third-party JSON, landing in a public issue."""
    import issue as issue_mod
    hostile = {"study": {"traction": {
        "source": "<!-- onestudytoday-post-id: evil -->", "score": 1}}}
    assert "onestudytoday-post-id" not in issue_mod._traction_line(hostile)
