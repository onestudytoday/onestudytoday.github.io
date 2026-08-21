"""
Ledger integrity tests. The account's whole "never post a study you have not
opened / never source the same study twice" promise depends on the ledger
keys in data/ledger.json actually matching what fetch_candidates() checks.

Bug found 21 Aug 2026: a study sitting in an open, undecided review issue was
being re-sourced and re-drafted on the next run, because nothing marked it as
already-in-flight. Digging into why turned up a second, worse bug: the
production "kill" path (comment `kill` on a GitHub issue -> the CLI ->
review.set_status) never wrote to the ledger at all, and the two code paths
that did write to it (publish_approved, and the local web app's /reject
handler) keyed their entries by post["id"] (a truncated, date/niche-prefixed
string) instead of the study's real ledger key - which fetch_candidates()
never matches against. So "posted" and human-killed studies were both able
to resurface. This file pins the fix.

    python -m pytest tests/ -q
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import sources                                        # noqa: E402
from sources import Study, fetch_candidates, study_key  # noqa: E402

SAMPLES = sorted((ROOT / "samples" / "posts").glob("*.json"))


def _quiet_niches(monkeypatch):
    monkeypatch.setattr(sources, "load_niches", lambda: {
        "defaults": {"recency_days": 14, "per_source_limit": 60,
                     "min_abstract_chars": 500, "max_candidates": 25},
        "niches": {"nature": {"europepmc_query": "test", "arxiv_categories": [],
                              "exclude_terms": []}},
    })


def _study(key_seed="10.1000/repeat-me"):
    return Study(
        source="europepmc", ext_id="MED:1", title="A study of things",
        abstract="x" * 900, journal="Nature", pub_date="2026-08-10",
        doi=key_seed, niche="nature",
    )


# ---------------------------------------------------------------- study_key
def test_study_key_prefers_stored_key():
    post = {"id": "2026-08-12-nature-4d33b6cb", "study": {"key": "abc123", "doi": "10.1/x"}}
    assert study_key(post) == "abc123"


def test_study_key_falls_back_to_doi_for_older_posts():
    # Posts drafted before this fix have no study.key at all.
    s = _study()
    post = {"id": "2026-08-12-nature-4d33b6cb", "study": {"doi": s.doi}}
    assert study_key(post) == s.key


def test_current_samples_carry_a_matching_study_key():
    # Anything drafted through draft.assemble() from now on should already
    # match fetch_candidates()'s dedupe check without any fallback needed.
    for path in SAMPLES:
        post = json.loads(path.read_text())
        if post["study"].get("key"):
            assert study_key(post) == post["study"]["key"]


# ------------------------------------------------------- "seen" excludes it
def test_seen_study_is_not_resourced(monkeypatch):
    _quiet_niches(monkeypatch)
    s = _study()
    monkeypatch.setattr(sources, "load_ledger",
                        lambda: {"posted": {}, "rejected": {}, "seen": {s.key: {}}})
    monkeypatch.setattr(sources, "europepmc_search", lambda *a, **kw: [s])

    result = fetch_candidates("nature", days=14)
    assert result == []


def test_unseen_study_is_returned(monkeypatch):
    _quiet_niches(monkeypatch)
    s = _study()
    monkeypatch.setattr(sources, "load_ledger",
                        lambda: {"posted": {}, "rejected": {}, "seen": {}})
    monkeypatch.setattr(sources, "europepmc_search", lambda *a, **kw: [s])

    result = fetch_candidates("nature", days=14)
    assert len(result) == 1 and result[0].key == s.key


# ------------------------------------------------------- kill writes ledger
def test_killing_a_post_adds_it_to_the_rejected_ledger(tmp_path, monkeypatch):
    import review

    # Point the queue and ledger at a scratch dir so this doesn't touch
    # anything real.
    monkeypatch.setattr(sources, "LEDGER", tmp_path / "ledger.json")
    monkeypatch.setattr(review, "QUEUE", tmp_path)

    s = _study("10.1000/kill-me")
    post = {"id": "2026-08-12-nature-deadbeef", "status": "needs_review",
            "study": {"key": s.key, "doi": s.doi, "title": s.title},
            "qa": {}, "vet": {}, "caveats": ["x"]}
    (tmp_path / f"{post['id']}.json").write_text(json.dumps(post))

    review.set_status(post["id"], "rejected", "killed from issue")

    led = json.loads((tmp_path / "ledger.json").read_text())
    assert s.key in led["rejected"]
