"""
Peak-time publish gating. Approving a post used to publish it in the same
breath, so it went out at whatever time you happened to review it - usually
right at the 6am draft slot, not when the audience is actually around.
publish_scheduled() only lets an approved post out once its niche's
peak-engagement time (docs/GROWTH.md) has arrived in America/Chicago.

    python -m pytest tests/ -q
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pipeline  # noqa: E402


def _approved_post(post_id, niche):
    return {"id": post_id, "status": "approved", "niche": niche,
            "study": {"doi": f"10.1000/{post_id}"}}


def test_post_not_yet_at_its_slot_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "QUEUE", tmp_path)
    calls = []
    monkeypatch.setattr(pipeline, "_publish_one",
                        lambda f, p, live: calls.append(p["id"]) or {})

    post = _approved_post("2026-08-20-physics-deadbeef", "physics")  # 09:00 slot
    (tmp_path / f"{post['id']}.json").write_text(json.dumps(post))

    before_slot = datetime(2026, 8, 20, 8, 0, tzinfo=ZoneInfo("America/Chicago"))
    pipeline.publish_scheduled(live=True, _now=before_slot)
    assert calls == []


def test_post_at_or_past_its_slot_is_published(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "QUEUE", tmp_path)
    calls = []
    monkeypatch.setattr(pipeline, "_publish_one",
                        lambda f, p, live: calls.append(p["id"]) or {})

    post = _approved_post("2026-08-20-physics-deadbeef", "physics")  # 09:00 slot
    (tmp_path / f"{post['id']}.json").write_text(json.dumps(post))

    after_slot = datetime(2026, 8, 20, 9, 30, tzinfo=ZoneInfo("America/Chicago"))
    pipeline.publish_scheduled(live=True, _now=after_slot)
    assert calls == [post["id"]]


def test_approved_late_still_publishes_on_next_poll(tmp_path, monkeypatch):
    # A post approved well after its niche's slot has already passed for the
    # day must not be silently skipped forever - it should go out on the very
    # next poll instead.
    monkeypatch.setattr(pipeline, "QUEUE", tmp_path)
    calls = []
    monkeypatch.setattr(pipeline, "_publish_one",
                        lambda f, p, live: calls.append(p["id"]) or {})

    post = _approved_post("2026-08-20-nature-deadbeef", "nature")  # 07:00 slot
    (tmp_path / f"{post['id']}.json").write_text(json.dumps(post))

    late_afternoon = datetime(2026, 8, 20, 15, 0, tzinfo=ZoneInfo("America/Chicago"))
    pipeline.publish_scheduled(live=True, _now=late_afternoon)
    assert calls == [post["id"]]


def test_unapproved_posts_are_never_published(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "QUEUE", tmp_path)
    calls = []
    monkeypatch.setattr(pipeline, "_publish_one",
                        lambda f, p, live: calls.append(p["id"]) or {})

    post = {"id": "2026-08-20-nature-deadbeef", "status": "needs_review",
            "niche": "nature", "study": {"doi": "10.1/x"}}
    (tmp_path / f"{post['id']}.json").write_text(json.dumps(post))

    pipeline.publish_scheduled(
        live=True, _now=datetime(2026, 8, 20, 23, 0, tzinfo=ZoneInfo("America/Chicago")))
    assert calls == []


def test_publish_approved_ignores_the_time_gate(tmp_path, monkeypatch):
    # The manual override (workflow_dispatch on scheduled-publish.yml) has to
    # keep working regardless of what time it is.
    monkeypatch.setattr(pipeline, "QUEUE", tmp_path)
    calls = []
    monkeypatch.setattr(pipeline, "_publish_one",
                        lambda f, p, live: calls.append(p["id"]) or {})

    post = _approved_post("2026-08-20-physics-deadbeef", "physics")  # 09:00 slot
    (tmp_path / f"{post['id']}.json").write_text(json.dumps(post))

    pipeline.publish_approved(live=True)  # no time argument at all
    assert calls == [post["id"]]
