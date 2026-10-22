"""
Re-filing a drafted post under a different weekday niche.

WHY THE FEATURE EXISTS. The niche a post is sourced for is a guess about
what a search will return, and it is wrong often enough to matter - a paper
that is really about health lands in the Monday nature slot because the
query matched it there. Before `reniche` the only answers were `kill`, which
writes the study into the do-not-use ledger so nobody can ever source it
again, or publishing it under the wrong banner.

WHY MOST OF THIS FILE IS ABOUT REFUSALS AND CARRY-OVERS. A re-filing changes
the rules a post was judged under, so the dangerous direction is not "it
failed to move" - that is visible - but "it moved and quietly got easier to
publish". Two specific ways that could happen have their own tests and must
never be fixed by relaxing them:

  * a required caveat present in the old report and absent from the new one
    (test_a_required_caveat_cannot_be_lost_in_the_move), and
  * an approval surviving a change to the basis it was given on
    (test_moving_a_post_takes_its_approval_away).

A third, opposite failure has its own test too: re-vetting must not REJECT a
post for the passage of time, since vet() hard-rejects anything older than
the sourcing window and queued posts sit for weeks
(test_an_old_post_is_not_rejected_merely_for_being_old).

    python -m pytest tests/test_reniche.py -q
"""

import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import review  # noqa: E402
import vet as vetmod  # noqa: E402

WF = ROOT / ".github" / "workflows" / "publish-on-approve.yml"

# Long enough to clear min_abstract_chars and to carry real numbers, so
# lint() and the invented-number check have something to run against.
ABSTRACT = (
    "We randomised 248 adults to a twelve-week walking programme or usual "
    "care and measured resting blood pressure at baseline and at follow-up. "
    "Systolic pressure fell by 4.1 mmHg in the walking group and by 0.3 mmHg "
    "in the control group. The difference was larger in participants who were "
    "sedentary at baseline. Adherence was 81 per cent and no serious adverse "
    "events were recorded. These findings suggest a modest but real effect of "
    "a low-cost intervention on blood pressure in middle-aged adults, and "
    "they should be read alongside the limitations of a single-site trial. "
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """vet(deep=True) calls Crossref and the retraction check. Nothing in
    this file is about those, and a test suite that needs the internet to
    run is a test suite that stops being run."""
    real = vetmod.vet
    monkeypatch.setattr(
        vetmod, "vet",
        lambda s, niche=None, allow_preprints=True, recency_days=None,
        deep=True: real(s, niche, allow_preprints, recency_days, deep=False))


def _post(**over):
    from datetime import date, timedelta
    recent = (date.today() - timedelta(days=20)).isoformat()
    p = {
        "id": "2026-09-01-nature-abcd1234",
        "niche": "nature",
        "source_niche": "nature",
        "status": "needs_review",
        "study": {
            "title": "A twelve-week walking programme and blood pressure",
            "journal": "The Lancet", "doi": "10.1016/x",
            "doi_display": "doi.org/10.1016/x", "pub_date": recent,
            "pub_date_display": "Sep 1, 2026", "is_preprint": False,
            "server": None, "url": "https://example.org/x",
        },
        "source": {"abstract": ABSTRACT},
        "cover": {"kicker": "The Lancet - 248 adults",
                  "headline": "Twenty minutes of walking **moves your blood "
                              "pressure** more than nothing does."},
        "slides": [
            {"eyebrow": "Why this matters", "title": "A cheap habit shifts a "
             "number your doctor cares about", "body": "Most people cannot "
             "take up a gym habit.\n\nWalking is free.", "basis": "stated"},
            {"eyebrow": "What they found", "title": "Systolic pressure fell "
             "by four points in the walking group", "body": "248 adults were "
             "randomised.\n\nThe drop was 4.1 mmHg.", "basis": "stated"},
        ],
        "caveats": ["Only 248 people took part.", "One site, twelve weeks."],
        "cta": {"headline": "Send this to someone who hates the gym",
                "sub": "One real study, every weekday."},
        "caption": "A twelve-week walking trial moved blood pressure.",
        "vet": {"key": "k", "verdict": "PASS", "score": 60, "flags": [],
                "required_caveats": []},
        "qa": {"lint_errors": []},
    }
    p.update(over)
    return p


def _niches():
    return yaml.safe_load((ROOT / "config" / "niches.yaml").read_text())["niches"]


# ---------------------------------------------------------------------------
# What moves, and what deliberately does not
# ---------------------------------------------------------------------------
def test_the_niche_moves():
    out, rep, msg = review.reniche(_post(), "health")
    assert out["niche"] == "health"
    assert "nature -> health" in msg


def test_the_post_id_does_not_move():
    """The id contains the sourcing niche, which makes renaming it look
    tidy. docs/img/<id>/ is already committed and already served to
    Instagram by URL, and the review issue is found by a marker carrying
    this exact string. Renaming would orphan both to improve a filename."""
    out, _, _ = review.reniche(_post(), "health")
    assert out["id"] == "2026-09-01-nature-abcd1234"


def test_where_the_study_was_actually_found_is_not_rewritten():
    """source_niche is the record that the SEARCH put this paper here.
    Rewriting it so the sourcing looks correct is how you lose the ability
    to tell that it was not."""
    out, _, _ = review.reniche(_post(), "psych")
    assert out["source_niche"] == "nature"


def test_the_copy_is_not_touched():
    """A re-filing is not a rewrite. The words are about the study and the
    study has not changed - `revise:` is for the words."""
    p = _post()
    out, _, _ = review.reniche(p, "health")
    assert out["slides"] == p["slides"]
    assert out["cover"] == p["cover"]
    assert out["caption"] == p["caption"]


def test_the_move_is_recorded_on_the_post():
    out, _, _ = review.reniche(_post(), "health")
    trail = out["reniche"]
    assert len(trail) == 1
    assert trail[0]["from"] == "nature" and trail[0]["to"] == "health"
    assert trail[0]["at"].endswith("Z")


def test_moving_twice_keeps_both_moves():
    out, _, _ = review.reniche(_post(), "health")
    out, _, _ = review.reniche(out, "psych")
    assert [(t["from"], t["to"]) for t in out["reniche"]] == [
        ("nature", "health"), ("health", "psych")]


def test_the_slides_are_re_rendered_not_just_relabelled():
    """The niche is the accent colour and the label on every slide, and the
    committed JPEGs are what actually publish. GitHub caches those by URL,
    so the cache-buster has to move or the card shows the old colour."""
    p = _post(render_seq=4)
    out, _, _ = review.reniche(p, "health")
    assert out["render_seq"] == 5


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------
def test_an_unknown_niche_is_refused_with_the_list():
    with pytest.raises(SystemExit) as e:
        review.reniche(_post(), "biology")
    assert "not a niche" in str(e.value)
    for name in ("nature", "psych", "health"):
        assert name in str(e.value)


def test_moving_a_post_to_the_niche_it_is_already_in_is_refused():
    with pytest.raises(SystemExit) as e:
        review.reniche(_post(), "nature")
    assert "already filed" in str(e.value)


def test_a_post_with_no_recoverable_abstract_is_refused():
    """Re-vetting decides whether forced caveats apply. Doing it without the
    paper is the one thing there is no safe version of, so study_from_post
    raises rather than guessing - the same fail-closed rule `revise` uses."""
    from draft import ReviseError
    p = _post(source={})
    p["study"] = {**p["study"], "doi": ""}
    with pytest.raises((ReviseError, SystemExit)):
        review.reniche(p, "health")


# ---------------------------------------------------------------------------
# THE TWO WAYS A RE-FILING COULD QUIETLY MAKE A POST EASIER TO PUBLISH
# ---------------------------------------------------------------------------
def test_a_required_caveat_cannot_be_lost_in_the_move():
    """Re-vetting rebuilds the report from scratch, and a check that fired
    under the old niche may not fire under the new one. Dropping "done in
    mice, not humans" because a post moved from nature to psych would be a
    silent weakening of the single promise this account makes about itself.
    So the reports are UNIONED."""
    p = _post()
    p["vet"] = {**p["vet"],
                "required_caveats": ["This was done in mice, not in people."]}
    out, rep, msg = review.reniche(p, "psych")
    assert "This was done in mice, not in people." in rep.required_caveats
    assert "This was done in mice, not in people." in \
        out["vet"]["required_caveats"]
    assert out["reniche"][-1]["carried_caveats"] == \
        ["This was done in mice, not in people."]
    assert "could not drop one" in msg


def test_moving_a_post_takes_its_approval_away():
    """An approval is given on a basis. Re-filing changes the basis - the
    journal tiers, the subject rules - so the approval does not survive it,
    exactly as a revision does not carry one across."""
    out, _, _ = review.reniche(_post(status="approved"), "health")
    assert out["status"] == "needs_review"


# ---------------------------------------------------------------------------
# And the opposite failure: a move that makes a post unpublishable for a
# reason that is nothing to do with the move.
# ---------------------------------------------------------------------------
def test_an_old_post_is_not_rejected_merely_for_being_old():
    """vet() hard-REJECTS anything past the sourcing window, and posts sit
    in the queue for weeks - twelve nature drafts were waiting when this was
    written. Re-vetting with today's window would turn "this is filed wrong"
    into "this can never go out", for no reason to do with the paper."""
    from datetime import date, timedelta
    old = (date.today() - timedelta(days=400)).isoformat()
    p = _post()
    p["study"] = {**p["study"], "pub_date": old}
    out, rep, _ = review.reniche(p, "health")
    assert rep.verdict != "REJECT", [f for f in rep.to_dict().get("flags", [])]
    assert "Vetting engine verdict is REJECT." not in review.blocking_reasons(out)


# ---------------------------------------------------------------------------
# The niche rules actually get applied - this is the whole point of
# re-vetting rather than relabelling.
# ---------------------------------------------------------------------------
def test_the_new_niches_subject_rule_is_applied():
    """`health` sets require_human_subjects. Moving an animal study there
    has to produce that flag, not inherit a clean report from nature."""
    assert _niches()["health"].get("require_human_subjects") is True
    p = _post()
    p["source"] = {"abstract": ABSTRACT.replace("248 adults", "48 mice")
                   .replace("participants", "animals")
                   .replace("middle-aged adults", "mice")}
    p["study"] = {**p["study"],
                  "title": "A twelve-week wheel-running protocol in mice"}
    out, rep, _ = review.reniche(p, "health")
    codes = [f.get("code") for f in out["vet"].get("flags") or []]
    assert "NON_HUMAN" in codes, codes


def test_the_report_is_recomputed_rather_than_carried_over():
    """A stale score would make the card lie about a post it has re-judged."""
    p = _post()
    p["vet"] = {**p["vet"], "score": 3, "verdict": "HOLD"}
    out, rep, _ = review.reniche(p, "health")
    assert out["vet"]["score"] != 3
    assert out["vet"]["score"] == rep.score


def test_the_post_is_re_linted_so_the_card_and_the_gate_agree():
    out, _, _ = review.reniche(_post(), "health")
    assert "lint_errors" in (out.get("qa") or {})


# ---------------------------------------------------------------------------
# THE COMMENT GATE
#
# A verb missing from the startsWith list is not an unknown command - it is a
# job that never starts. `force reimage` did exactly that for weeks: it
# matched nothing, the whole workflow was skipped, and the comment sat on the
# issue looking like it had been accepted.
# ---------------------------------------------------------------------------
def _wf():
    return WF.read_text()


@pytest.mark.parametrize("verb", [
    "approve", "force approve", "kill", "revise", "force revise", "revert",
    "exclude clipping", "include clipping", "reimage", "force reimage",
    "reniche",
])
def test_every_documented_verb_starts_the_job(verb):
    src = _wf()
    assert f"startsWith(github.event.comment.body, '{verb}')" in src, (
        f"commenting '{verb}' would be silently ignored")


def test_every_verb_in_the_gate_also_has_a_case_arm():
    """Passing the gate and then matching no case arm sets an empty action,
    which skips every step - a green run that did nothing."""
    import re
    src = _wf()
    verbs = re.findall(r"startsWith\(github\.event\.comment\.body, '([^']+)'\)",
                       src)
    case = src.split("case \"$C\" in", 1)[1].split("esac", 1)[0]
    for v in verbs:
        pat = v.replace(" ", r"\ ")
        assert f"{pat}*)" in case, f"'{v}' passes the gate and then does nothing"


def test_force_reimage_is_matched_before_plain_reimage():
    """`case` takes the first arm that matches. A bare `reimage*)` above
    `force reimage*)` would swallow it."""
    case = _wf().split("case \"$C\" in", 1)[1].split("esac", 1)[0]
    assert case.index("force\\ reimage*)") < case.index("\n            reimage*)")


@pytest.mark.parametrize("step", [
    "Revise or revert it", "Rebuild the review card for the new copy",
    "Update the issue with the revised draft",
])
def test_reniche_reaches_every_step_a_revision_reaches(step):
    """Being let through the gate and then skipped by each step's own `if`
    is the same silent no-op one layer down."""
    src = _wf()
    block = src.split(f"- name: {step}", 1)[1][:600]
    assert '"reniche"' in block, f"reniche is missing from '{step}'"


def test_the_commit_message_prefix_is_one_apply_edits_ignores():
    """These steps rewrite data/queue/*.md, which apply-edits.yml triggers
    on. An unrecognised prefix would start an apply, and an apply sets the
    status back to needs_review."""
    assert "reniche)                 PREFIX=reniche ;;" in _wf()


def test_the_issue_label_and_title_follow_the_post():
    """The publish gate reads the niche LABEL off the issue to report when a
    post goes out. An issue saying nature over a post saying health would
    disagree for good."""
    src = _wf()
    assert 'act === "reniche"' in src
    assert "issues.update" in src and "labels:" in src
    assert "REVIEW ${niche}" in src
