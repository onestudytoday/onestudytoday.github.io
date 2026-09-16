"""
Tests for the post-performance ledger.

Nothing here touches the network: metrics.collect() takes an injectable
`fetch`, so every Graph API response below is a fixture.

The properties that matter are mostly about NOT recording things - not
recording a row twice, not recording zeroes when the API failed, not silently
treating a reading taken three days late as if it were taken on time. A
metrics file that is subtly wrong is worse than no metrics file, because
decisions get made from it.

    python -m pytest tests/test_metrics.py -q
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import metrics  # noqa: E402

T0 = datetime(2026, 9, 1, 12, 0, 0)          # a post's publish time, UTC


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Isolated ledger + published dir."""
    pub = tmp_path / "published"
    pub.mkdir()
    monkeypatch.setattr(metrics, "PUBLISHED", pub)
    monkeypatch.setattr(metrics, "LEDGER", tmp_path / "metrics.jsonl")

    class Store:
        dir = pub

        def add(self, post_id="2026-09-01-nature-aaaa1111", niche="nature",
                at=T0, media_id="111"):
            post = {
                "id": post_id, "niche": niche,
                "study": {"title": "t", "journal": "j"},
                "published": {"media_id": media_id, "kind": "CAROUSEL",
                              "at": at.strftime("%Y-%m-%dT%H:%M:%SZ")},
            }
            (pub / f"{post_id}.json").write_text(json.dumps(post))
            return post

    return Store()


def ok(reach=1000, likes=50, comments=5, saved=20, shares=10, profile_visits=30):
    return lambda media_id: {"reach": reach, "likes": likes,
                             "comments": comments, "saved": saved,
                             "shares": shares, "profile_visits": profile_visits}


# ---------------------------------------------------------------------------
# What is due, and when
# ---------------------------------------------------------------------------
def test_nothing_is_due_before_the_first_bucket(store):
    store.add()
    assert metrics.due(metrics.load_published(), T0 + timedelta(hours=23)) == []


def test_the_24h_snapshot_becomes_due_at_24h(store):
    store.add()
    jobs = metrics.due(metrics.load_published(), T0 + timedelta(hours=24, minutes=1))
    assert [b for _, b, _ in jobs] == [24]


def test_both_buckets_are_due_for_an_old_post_never_measured(store):
    store.add()
    jobs = metrics.due(metrics.load_published(), T0 + timedelta(hours=100))
    assert sorted(b for _, b, _ in jobs) == [24, 72]


def test_a_post_that_never_published_is_never_measured(store):
    post = store.add()
    # strip the publish record the way an approved-but-unpublished post looks
    (store.dir / f"{post['id']}.json").write_text(json.dumps(
        {"id": post["id"], "niche": "nature", "study": {}}))
    assert metrics.due(metrics.load_published(), T0 + timedelta(hours=100)) == []


# ---------------------------------------------------------------------------
# Writing rows
# ---------------------------------------------------------------------------
def test_collect_writes_one_row_with_the_metrics_and_local_publish_hour(store):
    store.add(at=datetime(2026, 9, 1, 12, 0, 0))     # 12:00 UTC = 07:00 CDT
    out = metrics.collect(now=T0 + timedelta(hours=25), fetch=ok())
    assert out["written"] == 1

    row, = metrics.rows()
    assert row["bucket_hours"] == 24
    assert row["reach"] == 1000 and row["saved"] == 20 and row["shares"] == 10
    assert row["profile_visits"] == 30
    # The whole point of the hour column is comparing against PUBLISH_TIMES,
    # which are Central wall-clock. A UTC hour here would be useless.
    assert row["published_hour_ct"] == 7
    assert row["published_weekday_ct"] == "Tue"
    assert row["late"] is False


def test_a_snapshot_is_never_recorded_twice(store):
    store.add()
    metrics.collect(now=T0 + timedelta(hours=25), fetch=ok())
    again = metrics.collect(now=T0 + timedelta(hours=30), fetch=ok())
    assert again["written"] == 0
    assert len(metrics.rows()) == 1


def test_the_72h_snapshot_is_recorded_separately_from_the_24h_one(store):
    store.add()
    metrics.collect(now=T0 + timedelta(hours=25), fetch=ok(reach=1000))
    metrics.collect(now=T0 + timedelta(hours=73), fetch=ok(reach=4000))
    buckets = {r["bucket_hours"]: r["reach"] for r in metrics.rows()}
    assert buckets == {24: 1000, 72: 4000}


def test_an_api_error_records_nothing_at_all(store):
    """A row of zeroes is indistinguishable from a post nobody engaged with,
    and it would be permanent. Better a gap than a lie."""
    store.add()
    out = metrics.collect(now=T0 + timedelta(hours=25),
                          fetch=lambda m: {"error": "token expired"})
    assert out["written"] == 0
    assert out["failed"] and metrics.rows() == []


def test_an_exception_from_the_api_does_not_abort_the_other_posts(store):
    store.add(post_id="2026-09-01-nature-aaaa1111", media_id="boom")
    store.add(post_id="2026-09-01-psych-bbbb2222", niche="psych", media_id="fine")

    def flaky(media_id):
        if media_id == "boom":
            raise RuntimeError("network went away")
        return ok()(media_id)

    out = metrics.collect(now=T0 + timedelta(hours=25), fetch=flaky)
    assert out["written"] == 1
    assert len(out["failed"]) == 1
    assert [r["post_id"] for r in metrics.rows()] == ["2026-09-01-psych-bbbb2222"]


def test_a_late_reading_is_recorded_but_flagged(store):
    """If the collector was down, a '24h' reading can be taken at 90h. That is
    still worth having, but it is not comparable and must not silently join
    the comparison."""
    store.add()
    metrics.collect(now=T0 + timedelta(hours=90), fetch=ok())
    late = [r for r in metrics.rows() if r["bucket_hours"] == 24][0]
    assert late["late"] is True
    assert late["age_hours"] == pytest.approx(90, abs=0.1)
    # and it is excluded from summaries by default
    assert metrics.summarise(bucket=24) == {}
    assert metrics.summarise(bucket=24, include_late=True) != {}


def test_a_corrupt_line_does_not_take_out_the_whole_ledger(store):
    store.add()
    metrics.collect(now=T0 + timedelta(hours=25), fetch=ok())
    with metrics.LEDGER.open("a") as fh:
        fh.write("this is not json\n")
    assert len(metrics.rows()) == 1


# ---------------------------------------------------------------------------
# Reading it back
# ---------------------------------------------------------------------------
def test_engagement_weights_saves_and_shares_above_likes():
    saves = {"reach": 100, "likes": 0, "comments": 0, "saved": 1, "shares": 0}
    likes = {"reach": 100, "likes": 1, "comments": 0, "saved": 0, "shares": 0}
    assert metrics.engagement_rate(saves) > metrics.engagement_rate(likes)
    assert metrics.engagement_rate({"reach": 0}) is None


def test_the_report_refuses_to_name_a_winner_on_thin_but_timely_data(store):
    """The module exists to stop guessing. Ranking three posts and printing a
    'best hour' would be guessing with extra steps."""
    for i in range(3):
        store.add(post_id=f"2026-09-0{i+1}-nature-aaa{i}", media_id=str(i),
                  at=T0 + timedelta(days=i))
        metrics.collect(now=T0 + timedelta(days=i, hours=73), fetch=ok())

    s = metrics.summarise(bucket=72)
    assert s and all(not v["enough_data"] for v in s.values())
    assert f"under {metrics.MIN_N_FOR_A_CLAIM} posts" in metrics.report()


def test_all_late_readings_are_reported_as_such_and_rank_nothing(store):
    """Regression test for an inconsistency in this module's own reporting:
    the hour breakdown correctly refused to rank late readings, and then the
    boost recommendation was picked from those very same readings."""
    for i in range(3):
        store.add(post_id=f"2026-09-0{i+1}-nature-aaa{i}", media_id=str(i),
                  at=T0 + timedelta(days=i))
    metrics.collect(now=T0 + timedelta(days=10), fetch=ok())     # all late

    assert metrics.summarise(bucket=72) == {}
    assert metrics.best_post_to_boost(bucket=72) is None
    out = metrics.report()
    assert "were taken more than" in out and "not comparable" in out
    assert "best candidate to put ad budget behind" not in out
    # the data is not thrown away, just not ranked
    assert metrics.best_post_to_boost(bucket=72, include_late=True) is not None


def test_enough_data_flips_once_the_group_is_big_enough(store):
    for i in range(metrics.MIN_N_FOR_A_CLAIM):
        store.add(post_id=f"2026-09-01-nature-c{i:04d}", media_id=str(i), at=T0)
    metrics.collect(now=T0 + timedelta(hours=73), fetch=ok())
    s = metrics.summarise(bucket=72)
    assert s[7]["n"] == metrics.MIN_N_FOR_A_CLAIM
    assert s[7]["enough_data"] is True


def test_the_boost_candidate_is_chosen_on_profile_visits_not_likes(store):
    """Boosting exists to convert strangers into followers. The most-liked
    post is regularly not the one that made people tap through."""
    store.add(post_id="2026-09-01-nature-likeable", media_id="a", at=T0)
    store.add(post_id="2026-09-01-psych-converts", media_id="b", niche="psych",
              at=T0)

    def by_media(media_id):
        if media_id == "a":      # lots of likes, nobody visits the profile
            return {"reach": 5000, "likes": 900, "comments": 2, "saved": 3,
                    "shares": 1, "profile_visits": 4}
        return {"reach": 400, "likes": 20, "comments": 9, "saved": 40,
                "shares": 25, "profile_visits": 88}

    metrics.collect(now=T0 + timedelta(hours=73), fetch=by_media)
    best = metrics.best_post_to_boost(bucket=72)
    assert best["post_id"] == "2026-09-01-psych-converts"


def test_report_is_honest_when_there_is_nothing_yet(store):
    assert "No metrics recorded yet" in metrics.report()
