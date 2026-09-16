"""
Tests for the house-style gate and post-format rotation.

Two things are being pinned here, and they are different in kind:

  * style.style_flags() - that each check fires on the construction it is
    supposed to catch, and does NOT fire on ordinary copy. The false-positive
    half matters as much as the other: every spurious flag burns one of the two
    repair rounds a draft gets, so an over-eager check makes the copy worse.

  * draft.pick_format() / build_post_schema() - that rotation is real. The
    eyebrow labels used to be a module-level constant, which is why every post
    on the account has the same four. If the schema enum ever stops being
    per-format, the rotation silently becomes cosmetic.

    python -m pytest tests/test_style.py -q
"""

import datetime
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import draft  # noqa: E402
import style  # noqa: E402
from sources import Study  # noqa: E402

SAMPLES = sorted((ROOT / "samples" / "posts").glob("*.json"))
EM = "—"


def post(cover="Sleep loss **hits memory** harder than anyone expected here",
         slides=None, caveats=None, cta_h="Send this to whoever brags about sleep",
         cta_s="One real study, every weekday. The full paper is linked in bio."):
    return {
        "cover": {"kicker": "Nature - 40 adults", "headline": cover},
        "slides": slides or [
            {"eyebrow": "The setup", "title": "Forty adults gave up sleep.",
             "body": "They slept four hours.\n\nThen they took a memory test."},
        ],
        "caveats": caveats or ["Only 40 people took part.", "Short study."],
        "cta": {"headline": cta_h, "sub": cta_s},
    }


def _study(title="Sleep restriction and memory"):
    return Study(source="europepmc", ext_id="x1", title=title,
                 abstract="a", journal="Nature", pub_date="2026-09-16",
                 niche="psych")


# ---------------------------------------------------------------------------
# The checks fire when they should
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("phrase", [
    "swipe through", "follow for", "the science behind", "dive into",
    "sheds light on", "paves the way", "stay tuned",
])
def test_filler_phrases_are_caught(phrase):
    p = post(cta_s=f"{phrase} the rest of it, every weekday, linked in bio here")
    assert any(phrase in f for f in style.style_flags(p))


def test_not_just_x_but_y_is_caught():
    p = post(cover="Sleep loss is **not just tiredness** but a memory problem too")
    assert any("not just X but Y" in f for f in style.style_flags(p))


def test_too_many_em_dashes_are_caught():
    body = (f"Sleep{EM}the restorative kind{EM}matters.\n\n"
            f"Memory{EM}specifically recall{EM}suffers badly here.")
    p = post(slides=[{"eyebrow": "The setup", "title": "A title that is long "
                      "enough to look like a real one here", "body": body}])
    flags = style.style_flags(p)
    assert any("em-dashes" in f for f in flags)


def test_two_em_dashes_are_allowed():
    """The check targets the reflex, not the punctuation mark."""
    body = (f"Sleep{EM}the restorative kind{EM}matters a lot.\n\n"
            "Memory suffers when it is short.")
    p = post(slides=[{"eyebrow": "The setup", "title": "A title that is long "
                      "enough to look like a real one here", "body": body}])
    assert not any("em-dashes" in f for f in style.style_flags(p))


def test_repeated_knowledge_gap_framing_is_caught_but_one_is_fine():
    one = post(slides=[{"eyebrow": "The setup", "title": "Why sleep matters "
                        "for memory remains poorly understood by science",
                        "body": "A.\n\nB."}])
    assert not any("poorly understood" in f for f in style.style_flags(one))

    two = post(slides=[{"eyebrow": "The setup",
                        "title": "The mechanism remains poorly understood",
                        "body": "Little is known about it.\n\nB."}])
    assert any("framing" in f for f in style.style_flags(two))


def test_a_cta_that_names_nothing_is_caught():
    p = post(cta_h="Science that feeds your curiosity",
             cta_s="One real study, every weekday. The paper is linked in bio.")
    assert any("cta names nothing" in f for f in style.style_flags(p))


def test_a_cta_that_names_the_subject_passes():
    p = post(cover="Sleep loss **hits memory** harder than anyone expected here",
             cta_h="Send this to whoever brags about skipping sleep")
    assert not any("cta names nothing" in f for f in style.style_flags(p))


def test_the_cta_check_matches_singular_and_plural():
    """Regression: 'fossils' vs 'fossil' was reported as naming nothing, which
    would have sent a perfectly specific CTA back for a pointless rewrite."""
    p = post(cover="A **fossil** sat lost in a drawer for twenty long years",
             cta_h="Send this to whoever thinks fossils need fieldwork")
    assert not any("cta names nothing" in f for f in style.style_flags(p))


def test_the_cta_check_also_reads_the_study_title():
    p = post(cover="A result **nobody expected** turned up in the data here",
             cta_h="Send this to whoever doubts melatonin does anything")
    assert any("cta names nothing" in f for f in style.style_flags(p))
    assert not any("cta names nothing" in f for f in
                   style.style_flags(p, _study("Melatonin and sleep onset")))


def test_metronomic_sentences_are_caught():
    body = " ".join(["Sleep is good for you." for _ in range(6)])
    p = post(slides=[{"eyebrow": "The setup",
                      "title": "Sleep is good for you and this is a title.",
                      "body": f"{body}\n\n{body}"}])
    assert any("same length" in f for f in style.style_flags(p))


# ---------------------------------------------------------------------------
# ...and stay quiet when they should
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.stem)
def test_every_shipped_sample_is_style_clean(path):
    """The samples are this repo's reference copy. If they violate the house
    style, the house style is documentation nobody follows."""
    assert style.style_flags(json.loads(path.read_text())) == []


def test_a_malformed_post_does_not_raise():
    for bad in ({}, {"cover": None, "slides": "nope", "cta": 3},
                {"slides": [None, 7]}, {"cta": {"headline": None}}):
        assert isinstance(style.style_flags(bad), list)


def test_style_flags_are_never_guardrails():
    """A tone note must not land in the same bucket as 'this post implies a
    mouse result applies to humans'. review.blockers() counts GUARDRAIL."""
    p = post(cta_h="Science that feeds your curiosity")
    for f in style.style_flags(p):
        assert not f.startswith("GUARDRAIL")


# ---------------------------------------------------------------------------
# Format rotation
# ---------------------------------------------------------------------------
def test_four_formats_are_configured():
    names = [f["name"] for f in draft.FORMATS]
    assert len(names) == len(set(names)) >= 3


def test_consecutive_days_get_different_formats():
    s = _study()
    seen = [draft.pick_format(s, date(2026, 9, 16) + datetime.timedelta(days=i))["name"]
            for i in range(len(draft.FORMATS))]
    assert len(set(seen)) == len(draft.FORMATS), seen


def test_two_studies_drafted_the_same_day_can_differ():
    """The drafting workflow gets re-run several times in a day when the first
    study is not interesting enough. Handing back the same shape every time
    would defeat the point."""
    day = date(2026, 9, 16)
    names = {draft.pick_format(_study(), day)["name"]}
    for i in range(12):
        s = Study(source="europepmc", ext_id=f"id{i}", title=f"Study {i}",
                  abstract="a", journal="N", pub_date="2026-09-16", niche="psych")
        names.add(draft.pick_format(s, day)["name"])
    assert len(names) > 1


def test_the_same_study_on_the_same_day_is_stable():
    s, day = _study(), date(2026, 9, 16)
    assert draft.pick_format(s, day)["name"] == draft.pick_format(s, day)["name"]


def test_the_schema_enum_is_per_format_not_global():
    """This is what makes rotation real. If the enum goes back to being a
    module constant the labels silently collapse to one skeleton again."""
    enums = []
    for fmt in draft.FORMATS:
        sch = draft.build_post_schema(fmt)
        enum = (sch["input_schema"]["properties"]["slides"]["items"]
                   ["properties"]["eyebrow"]["enum"])
        assert enum == list(fmt["eyebrows"])
        enums.append(tuple(enum))
    assert len(set(enums)) == len(draft.FORMATS)


def test_building_a_schema_does_not_mutate_the_template():
    before = json.dumps(draft._POST_SCHEMA_TEMPLATE, sort_keys=True)
    for fmt in draft.FORMATS:
        draft.build_post_schema(fmt)
    assert json.dumps(draft._POST_SCHEMA_TEMPLATE, sort_keys=True) == before


def test_every_format_eyebrow_is_renderable():
    """render.py lowercases the eyebrow into a PNG filename, and lint checks it
    against fields.slide.eyebrow.fixed_values. Both have to know the label."""
    allowed = set(draft.SPEC["fields"]["slide.eyebrow"]["fixed_values"])
    for fmt in draft.FORMATS:
        for eb in fmt["eyebrows"]:
            assert eb in allowed, f"{fmt['name']}: {eb!r} missing from spec"
            assert eb.isascii() and "/" not in eb and "." not in eb


def test_the_prompt_names_the_chosen_format_and_asks_for_a_send():
    from vet import VetReport
    s = _study()
    for fmt in draft.FORMATS:
        p = draft.build_prompt(s, VetReport(key="k"), fmt)
        assert f'"{fmt["name"]}" format' in p
        assert fmt["eyebrows"][0] in p
        # and never another format's opening label
        for other in draft.FORMATS:
            if other["name"] != fmt["name"]:
                assert f'Slide 2 must use eyebrow "{other["eyebrows"][0]}"' not in p
        assert "ask for a send, not a follow" in p
        assert "Never write \"Follow for" in p


def test_the_caption_is_checked_too():
    """The caption is the longest piece of copy on the post and is written by
    the same model in the same breath as the slides, so it picks up the same
    habits. Leaving it out would have left the most-read text unchecked."""
    p = post()
    p["caption"] = "Swipe through for the science behind all of this today."
    flags = style.style_flags(p)
    assert any("swipe through" in f for f in flags)
    assert any("the science behind" in f for f in flags)
