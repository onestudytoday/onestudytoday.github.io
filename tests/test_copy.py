"""
Copy-spec and rendering tests. Offline, no API calls.

    python -m pytest tests/ -q
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from caption import build_caption, build_hashtags, caption_stats  # noqa: E402
from draft import flatten, lint                                   # noqa: E402
from render import render_post                                    # noqa: E402
from review import blocking_reasons                               # noqa: E402
from vet import VetReport                                         # noqa: E402

REQUIRED_SETTINGS = ("META_APP_ID", "META_APP_SECRET",
                     "IG_ACCESS_TOKEN", "IG_BUSINESS_ACCOUNT_ID")

SAMPLES = sorted((ROOT / "samples" / "posts").glob("*.json"))


def _rep(post):
    r = VetReport(key=post["id"])
    for k, v in (post.get("vet") or {}).items():
        if k != "flags" and hasattr(r, k):
            setattr(r, k, v)
    return r


@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.stem)
def test_every_sample_passes_lint(path):
    post = json.loads(path.read_text())
    errs = lint(post, _rep(post))
    assert errs == [], f"{path.stem}: {errs}"


@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.stem)
def test_every_sample_has_a_caveats_slide(path):
    post = json.loads(path.read_text())
    assert len(post.get("caveats", [])) >= 2


@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.stem)
def test_every_sample_links_the_paper(path):
    post = json.loads(path.read_text())
    st = post["study"]
    assert st.get("doi") or st.get("url")
    assert st["doi_display"] in post["caption"]


@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.stem)
def test_caption_fits_instagram(path):
    post = json.loads(path.read_text())
    cap = build_caption(post)
    s = caption_stats(cap)
    assert s["chars"] <= 2200
    assert 3 <= s["hashtags"] <= 10


def test_hashtags_are_deterministic_and_rotate():
    posts = [json.loads(p.read_text()) for p in SAMPLES]
    blocks = [build_hashtags(p) for p in posts]
    # deterministic
    assert blocks == [build_hashtags(p) for p in posts]
    # no two posts share an identical block
    assert len(set(blocks)) == len(blocks)


def test_banned_tags_never_appear():
    import yaml
    from config import ROOT as R
    banned = yaml.safe_load((R / "config" / "hashtags.yaml").read_text())["banned"]
    for p in SAMPLES:
        block = build_hashtags(json.loads(p.read_text()))
        for b in banned:
            assert b not in block.split()


def test_preprint_without_caveat_is_blocked():
    post = json.loads(SAMPLES[0].read_text())
    post["study"]["is_preprint"] = True
    post["study"]["server"] = "bioRxiv"
    post["caveats"] = ["Something unrelated about sample size and generalisability."]
    reasons = blocking_reasons(post)
    assert any("preprint" in r.lower() for r in reasons)


def test_post_with_no_link_is_blocked():
    post = json.loads(SAMPLES[0].read_text())
    post["study"]["doi"] = ""
    post["study"]["url"] = ""
    assert any("link" in r.lower() for r in blocking_reasons(post))


def test_post_with_no_caveats_is_blocked():
    post = json.loads(SAMPLES[0].read_text())
    post["caveats"] = []
    assert any("caveat" in r.lower() for r in blocking_reasons(post))


def test_guardrail_violation_blocks_approval():
    post = json.loads(SAMPLES[0].read_text())
    post["qa"] = {"lint_errors": ["GUARDRAIL causal_verb_on_observational: nope"],
                  "blocking_claims": [], "unverified_numbers": []}
    assert any("Guardrail" in r for r in blocking_reasons(post))


def test_unverified_number_blocks_approval():
    post = json.loads(SAMPLES[0].read_text())
    post["qa"] = {"lint_errors": [], "blocking_claims": [],
                  "unverified_numbers": [{"number": "97%", "found_in_abstract": False}]}
    assert any("97%" in r for r in blocking_reasons(post))


@pytest.mark.parametrize("theme", ["neon", "block", "editorial"])
def test_renders_all_themes_at_correct_size(theme, tmp_path):
    from PIL import Image
    post = json.loads(SAMPLES[0].read_text())
    paths = render_post(post, theme, str(tmp_path / theme))
    assert len(paths) == 5
    for p in paths:
        assert Image.open(p).size == (1080, 1350)


def test_rendering_is_deterministic(tmp_path):
    post = json.loads(SAMPLES[1].read_text())
    a = render_post(post, "neon", str(tmp_path / "a"))
    b = render_post(post, "neon", str(tmp_path / "b"))
    for x, y in zip(a, b):
        assert Path(x).read_bytes() == Path(y).read_bytes()


def test_flatten_excludes_cta_when_asked():
    post = json.loads(SAMPLES[0].read_text())
    assert post["cta"]["headline"] in flatten(post)
    assert post["cta"]["headline"] not in flatten(post, include_cta=False)


def test_build_caption_does_not_require_instagram_credentials(monkeypatch):
    # Regression test for the "Draft today's post" failure of 2026-08-12:
    # `python src/issue.py <queue-file> <image-base>` runs in a workflow step
    # that deliberately does NOT get META_APP_ID/META_APP_SECRET/
    # IG_ACCESS_TOKEN/IG_BUSINESS_ACCOUNT_ID - it only builds a GitHub issue
    # body from a post already on disk, it never touches the Instagram API.
    # caption.build_caption() used to call settings() anyway (the result was
    # never even used), so that step crashed with
    # "Missing required setting: META_APP_ID" before an issue was ever opened.
    for name in REQUIRED_SETTINGS:
        monkeypatch.delenv(name, raising=False)
    post = json.loads(SAMPLES[0].read_text())
    cap = build_caption(post)  # must not raise SystemExit
    assert cap

    from issue import build as build_issue  # noqa: E402
    body = build_issue(post, "https://example.github.io/img")
    assert post["id"] in body


# ---------------------------------------------------------------------------
# Regression test for the 24 Aug 2026 production incident: a HOLD-status
# candidate ("Porcine deltacoronavirus nucleocapsid protein inhibits RIG-I
# signaling") twice in a row got a malformed tool-call response back from the
# drafting model - "cover" came back as a plain string instead of an object -
# which crashed straight through lint()'s first `.get()` call. pipeline.run()
# caught the exception and silently skipped the candidate every time, with no
# visible trace beyond one easily-missed log line. lint() and flatten() are
# now defensive about every nested field's TYPE, not just its presence, so a
# malformed field becomes a reportable lint error - feeding the normal repair
# round-trip - instead of an unhandled crash.
def test_malformed_cover_is_a_lint_error_not_a_crash():
    post = {"cover": "just a string, not an object",
            "slides": [{"eyebrow": "The setup", "title": "t", "body": "b\n\nb"}],
            "caveats": ["a caveat, long enough to pass the word check here"],
            "cta": {"headline": "h", "sub": "s"}, "caption": "c"}
    errs = lint(post, VetReport(key="x"))
    assert any("cover" in e and "expected an object" in e for e in errs)


def test_malformed_slide_element_is_a_lint_error_not_a_crash():
    post = {"cover": {"kicker": "k", "headline": "**h**"},
            "slides": ["not an object", {"eyebrow": "The setup", "title": "t", "body": "b\n\nb"}],
            "caveats": ["a caveat, long enough to pass the word check here"],
            "cta": {"headline": "h", "sub": "s"}, "caption": "c"}
    errs = lint(post, VetReport(key="x"))
    assert any(e.startswith("slide1:") for e in errs)


def test_malformed_post_shape_does_not_crash_flatten():
    # flatten() is called from inside lint() on the raw, pre-repair post, so
    # it has to survive the same malformed shapes lint() does.
    post = {"cover": "bad", "slides": "bad", "caveats": "bad",
            "cta": "bad", "caption": 12345}
    assert isinstance(flatten(post), str)  # must not raise


# ---------------------------------------------------------------------------
# Regression test for a real block: an abstract said "seven-fold" and the
# drafted copy correctly restated it as "7-fold", but the code-side number
# check only ever compared digit strings, so it flagged 7 as an unsupported
# number and blocked the post over two numbers that mean exactly the same
# thing. local_unverified_numbers() now also accepts the spelled-out word for
# 0-20 as equivalent to its digit.
def test_digit_form_of_a_spelled_out_number_is_not_flagged():
    from draft import local_unverified_numbers
    # 7 is already exempt below 10 regardless (a "small counting number"), so
    # use 12/twelve - large enough to actually need the word-equivalence
    # check, not just fall through the existing small-number exemption.
    abstract = "Risk increased twelve-fold in the exposed group compared to controls."
    assert local_unverified_numbers("The risk went up 12-fold.", abstract) == []


def test_number_word_boundary_does_not_match_inside_a_longer_number_word():
    # Unit-level, bypassing local_unverified_numbers()'s own <=10 exemption
    # (which would otherwise mask this): "seven" must not match inside
    # "seventeen" - a different, larger number that just happens to contain
    # it as a substring.
    from draft import _number_word_appears
    assert _number_word_appears(7.0, "The study followed seventeen participants.") is False
    assert _number_word_appears(7.0, "Risk rose seven-fold in the exposed group.") is True


def test_word_equivalence_is_scoped_to_zero_through_twenty():
    from draft import local_unverified_numbers
    # 25 has no digit form in the (word-spelled) abstract and is above the
    # scoped range, so this should still be flagged rather than silently
    # matched against something like "twenty-five" via partial logic.
    abstract = "Response rates were twenty-five percent in the treated arm."
    bad = local_unverified_numbers("Response rates hit 25%.", abstract)
    assert [n["number"] for n in bad] == ["25%"]
