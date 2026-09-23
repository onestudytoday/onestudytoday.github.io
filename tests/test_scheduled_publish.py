"""
Publish-time gating. Approving a post used to publish it in the same breath,
so it went out at whatever time you happened to review it - usually right at
the 6am draft slot, not when the audience is actually around.
publish_scheduled() only lets an approved post out once the configured
publish time has arrived in America/Chicago.

The hours here are DERIVED from pipeline.publish_time(), never written as
literals. These tests used to hardcode "physics = 09:00 slot", so moving the
slot to a single 15:00 broke a test that was really asserting "a post past
its slot publishes" - a true statement at any hour. Deriving keeps the test
about the behaviour instead of about the number.

    python -m pytest tests/ -q
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pipeline  # noqa: E402

CENTRAL = ZoneInfo("America/Chicago")


def _slot_on(day: datetime, niche: str = "") -> datetime:
    """The configured publish moment on `day`, whatever it is set to."""
    h, m = (int(x) for x in pipeline.publish_time(niche).split(":"))
    return day.replace(hour=h, minute=m, second=0, microsecond=0)


def _approved_post(post_id, niche):
    return {"id": post_id, "status": "approved", "niche": niche,
            "study": {"doi": f"10.1000/{post_id}"}}


def test_post_not_yet_at_its_slot_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "QUEUE", tmp_path)
    calls = []
    monkeypatch.setattr(pipeline, "_publish_one",
                        lambda f, p, live: calls.append(p["id"]) or {})

    post = _approved_post("2026-08-20-physics-deadbeef", "physics")
    (tmp_path / f"{post['id']}.json").write_text(json.dumps(post))

    day = datetime(2026, 8, 20, tzinfo=CENTRAL)
    before_slot = _slot_on(day, "physics") - timedelta(hours=1)
    pipeline.publish_scheduled(live=True, _now=before_slot)
    assert calls == []


def test_post_at_or_past_its_slot_is_published(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "QUEUE", tmp_path)
    calls = []
    monkeypatch.setattr(pipeline, "_publish_one",
                        lambda f, p, live: calls.append(p["id"]) or {})

    post = _approved_post("2026-08-20-physics-deadbeef", "physics")
    (tmp_path / f"{post['id']}.json").write_text(json.dumps(post))

    day = datetime(2026, 8, 20, tzinfo=CENTRAL)
    after_slot = _slot_on(day, "physics") + timedelta(minutes=30)
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

    post = _approved_post("2026-08-20-nature-deadbeef", "nature")
    (tmp_path / f"{post['id']}.json").write_text(json.dumps(post))

    day = datetime(2026, 8, 20, tzinfo=CENTRAL)
    late = _slot_on(day, "nature") + timedelta(hours=6)
    pipeline.publish_scheduled(live=True, _now=late)
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


# ---------------------------------------------------------------------------
# One publish time, defined once
#
# There used to be two hand-maintained copies of the slot table: a dict in
# pipeline.py and a JavaScript object in publish-on-approve.yml that told you
# when your approval would go out. Nothing made them agree, so moving the real
# slot left the bot confidently promising the old one - a wrong promise, which
# is worse than no promise because nothing looks broken.
# ---------------------------------------------------------------------------
def test_every_niche_shares_one_publish_time_from_the_yaml():
    from sources import load_niches
    configured = load_niches()["defaults"]["publish_time"]
    for niche in load_niches()["niches"]:
        assert pipeline.publish_time(niche) == configured


def test_the_workflow_no_longer_carries_its_own_copy_of_the_slot_table():
    """Regression: a second copy of the times, written in JS, in the workflow.

    If this ever fails, the bot's "it'll go out around X" comment has been
    hardcoded again and can drift from the gate that actually holds the post.
    """
    wf = (Path(__file__).resolve().parent.parent
          / ".github" / "workflows" / "publish-on-approve.yml").read_text()
    assert "const PUBLISH_TIMES" not in wf
    # It must ask the Python that owns the gate instead.
    assert "publish_time_display" in wf


def test_the_yaml_is_really_what_decides_the_time(monkeypatch):
    """Proves the YAML path is live, not decorative.

    Without this, the fallback test below passes whether or not publish_time()
    ever reads the config - because the configured value and the hardcoded
    fallback are deliberately the same string. Asserting on a DIFFERENT value
    is the only way to show the read actually happens.
    """
    monkeypatch.setattr(pipeline, "load_niches",
                        lambda: {"defaults": {"publish_time": "06:30"},
                                 "niches": {"physics": {}}})
    assert pipeline.publish_time("physics") == "06:30"
    assert pipeline.publish_time_display("physics") == "6:30am"


def test_a_per_niche_override_wins_over_the_default(monkeypatch):
    monkeypatch.setattr(pipeline, "load_niches",
                        lambda: {"defaults": {"publish_time": "15:00"},
                                 "niches": {"health": {"publish_time": "12:00"}}})
    assert pipeline.publish_time("health") == "12:00"
    assert pipeline.publish_time("physics") == "15:00"


def test_a_broken_config_does_not_publish_everything_immediately(monkeypatch):
    """Failing open here would dump the whole approved queue at once.

    Patches pipeline.load_niches, NOT sources.load_niches: pipeline imports the
    name directly, so patching the source module leaves pipeline's binding
    untouched and this test would pass without the fallback ever running.
    """
    def boom():
        raise RuntimeError("malformed yaml")
    monkeypatch.setattr(pipeline, "load_niches", boom)
    assert pipeline.publish_time("physics") == pipeline.DEFAULT_PUBLISH_TIME


def test_publish_time_display_is_human_readable():
    assert pipeline.publish_time_display() == "3:00pm"


def test_the_draft_workflow_does_not_hardcode_a_recency_window():
    """Regression: the SIXTH copy of `14`.

    daily-draft.yml's workflow_dispatch input had `default: "14"`. GitHub
    fills an input default in whether or not you touch the box, so every
    MANUAL draft passed --days 14 and silently overrode the 75 configured in
    niches.yaml - while the cron run, which sends no inputs, used 75. A
    hand-started draft therefore searched a fortnight and reported finding
    nothing, with the log confidently printing "(last 14 days)".
    """
    wf = (Path(__file__).resolve().parent.parent
          / ".github" / "workflows" / "daily-draft.yml").read_text()
    import yaml as _yaml
    inputs = _yaml.safe_load(wf)[True]["workflow_dispatch"]["inputs"]
    assert inputs["days"].get("default", "") == "", \
        "a non-empty default here silently overrides config/niches.yaml"


def test_the_python_entry_points_take_their_window_from_the_config():
    """draft.py and vet.py had their own `default=14` argparse values too."""
    import inspect
    for mod in ("draft", "vet"):
        src = inspect.getsource(__import__(mod))
        assert 'add_argument("--days", type=int, default=14)' not in src, mod


# ---------------------------------------------------------------------------
# "Nothing approved pending" said two completely different things
# ---------------------------------------------------------------------------
def _queue_post(tmp_path, monkeypatch, status, niche="nature", pid="2026-09-23-nature-aaaabbbb"):
    import json
    import pipeline
    q = tmp_path / "queue"
    q.mkdir(exist_ok=True)
    (q / f"{pid}.json").write_text(json.dumps(
        {"id": pid, "niche": niche, "status": status,
         "study": {"title": "t"}, "cover": {}, "slides": [],
         "caveats": [], "cta": {}}))
    monkeypatch.setattr(pipeline, "QUEUE", q)
    return pipeline


def test_an_approved_post_waiting_for_its_slot_says_so(tmp_path, monkeypatch, capsys):
    """"Nothing is approved" and "something is approved and its slot is two
    hours away" are different situations. Printing the same sentence for both
    makes an approval look lost - which is why approvals were being chased
    with a manual publish run instead of left to the schedule."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    pipeline = _queue_post(tmp_path, monkeypatch, "approved")
    morning = datetime(2026, 9, 23, 9, 0, tzinfo=ZoneInfo("America/Chicago"))
    assert pipeline.publish_scheduled(live=False, _now=morning) == []
    out = capsys.readouterr().out
    assert "1 approved" in out
    assert "Central" in out, "it never says WHEN"
    assert "Publish approved posts" in out, "it never says how to send it now"


def test_nothing_approved_says_that_instead(tmp_path, monkeypatch, capsys):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    pipeline = _queue_post(tmp_path, monkeypatch, "needs_review")
    evening = datetime(2026, 9, 23, 20, 0, tzinfo=ZoneInfo("America/Chicago"))
    assert pipeline.publish_scheduled(live=False, _now=evening) == []
    out = capsys.readouterr().out
    assert "Nothing is approved" in out
    assert "awaiting a decision" in out
