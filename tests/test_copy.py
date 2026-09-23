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
    """The link has to be in the caption that gets POSTED.

    This used to assert the link was in post["caption"], the raw field the
    drafting model writes. That stopped being the right place to look when the
    caption was cut to a single hook line and caption.py took over appending
    the link - and the assertion would have gone on passing for months on any
    post whose model-written text happened to mention a DOI, while saying
    nothing about what Instagram actually receives. build_caption() is what
    Instagram actually receives.
    """
    post = json.loads(path.read_text())
    st = post["study"]
    assert st.get("doi") or st.get("url")
    assert st["doi_display"] in build_caption(post)


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
    # cover + every body slide + caveats + cta. Derived, not the literal 5 this
    # used to assert: that number was only true while every sample happened to
    # have two body slides, so adding the implications slide broke a test that
    # is not about how many slides a post has.
    assert len(paths) == len(post["slides"]) + 3
    for p in paths:
        assert Image.open(p).size == (1080, 1350)


def test_rendering_is_deterministic(tmp_path):
    post = json.loads(SAMPLES[1].read_text())
    a = render_post(post, "neon", str(tmp_path / "a"))
    b = render_post(post, "neon", str(tmp_path / "b"))
    for x, y in zip(a, b):
        assert Path(x).read_bytes() == Path(y).read_bytes()


# ---------------------------------------------------------------------------
# Accent runs and the punctuation that follows them
# ---------------------------------------------------------------------------
def _words(s, width=900):
    from PIL import Image, ImageDraw
    import render
    d = ImageDraw.Draw(Image.new("RGB", (render.W, render.H)))
    lines = render.wrap_runs(d, s, render.font("sans_black", 60), width, 0)
    return [w for line in lines for w in line]


def test_punctuation_after_an_accent_run_is_not_pushed_off_the_word():
    """Shipped on the cover of any post whose **highlighted phrase** did not
    end a sentence, as `switches on , not just firing it.`

    The runs are split on the asterisks and each run was then split on spaces
    independently, so the comma became a word of its own and the drawer put a
    space in front of it. The implication-first cover makes this the common
    case: the accent now lands on the stake mid-line rather than on the
    finding at the end of it.
    """
    words = _words("Brain stimulation may work by **rewriting which genes a "
                   "cell switches on**, not just firing it.")
    comma = [w for w in words if w[0] == ","]
    assert comma, "the comma vanished"
    assert comma[0][2] is True, "the comma is a free-standing word again"
    assert comma[0][1] is False, "the comma got the accent colour"


def test_a_glued_piece_never_starts_a_line():
    """A line beginning with a comma is the same defect wearing a hat."""
    from PIL import Image, ImageDraw
    import render
    d = ImageDraw.Draw(Image.new("RGB", (render.W, render.H)))
    s = ("Brain stimulation may work by **rewriting which genes a cell "
         "switches on**, not just firing it.")
    # Sweep widths so the wrap point lands everywhere in the sentence.
    for width in range(140, 1000, 10):
        for line in render.wrap_runs(d, s, render.font("sans_black", 60),
                                     width, 0):
            if line:
                assert line[0][0] not in ",.;:", f"width {width}: {line}"


def test_a_glued_word_is_still_measured_against_the_box():
    """THE one the first version of the glue fix got wrong.

    A glued piece was appended without the width test, on the assumption that
    it is always a stray comma. `**the drug works**—unimaginably` glues a
    whole word: that headline ran 490px past the right edge of a 1080px canvas
    and was drawn off the image. fit_runs only binary-searches on height, so
    nothing downstream caught it.
    """
    from PIL import Image, ImageDraw
    import render
    d = ImageDraw.Draw(Image.new("RGB", (render.W, render.H)))
    f = render.font("sans_black", 60)
    box = 912
    s = "A study says the **drug works**—unimaginably everything about recovery."
    for line in render.wrap_runs(d, s, f, box, 0):
        text = ""
        for i, (w, _acc, glue) in enumerate(line):
            text += w if (glue and i) else ((" " if i else "") + w)
        assert render.text_w(d, text, f, 0) <= box, f"{text!r} overflows"


def test_an_accent_run_at_the_very_end_keeps_its_full_stop_attached():
    words = _words("The ocean gives **half of it back**.")
    assert words[-1][0] == "." and words[-1][2] is True


def test_ordinary_spacing_is_untouched():
    words = _words("Gut bacteria **made a precursor** inside.")
    assert [w[0] for w in words] == ["Gut", "bacteria", "made", "a",
                                     "precursor", "inside."]
    assert not any(w[2] for w in words)


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


def test_linkinbio_build_does_not_require_instagram_credentials(monkeypatch, tmp_path):
    # Regression test for the "Publish approved posts" outage of 2026-08-21
    # through 2026-08-24: scheduled-publish.yml's "Rebuild the link-in-bio
    # page" step (`python src/linkinbio.py`) deliberately only passes HANDLE
    # into its env, not META_APP_ID/META_APP_SECRET/IG_ACCESS_TOKEN/
    # IG_BUSINESS_ACCOUNT_ID - rebuilding docs/index.html never touches the
    # Instagram API. linkinbio.build() used to call settings() anyway just to
    # reach s.handle (the exact same shape of bug as build_caption() above),
    # so this step raised "Missing required setting: META_APP_ID" and failed
    # the job on *every* run of the every-15-minutes cron, whether or not
    # anything was even queued to publish - silently, because nothing else
    # in the pipeline surfaces a single CI step failing that way.
    import config
    import linkinbio
    for name in REQUIRED_SETTINGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "PUBLISHED", tmp_path)
    monkeypatch.setattr(linkinbio, "PUBLISHED", tmp_path)
    monkeypatch.setattr(linkinbio, "DOCS", tmp_path)

    out = linkinbio.build()  # must not raise SystemExit
    assert Path(out).exists()


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


# ---------------------------------------------------------------------------
# Rendering must not require the ability to publish
# ---------------------------------------------------------------------------
_CREDS = ("META_APP_ID", "META_APP_SECRET", "IG_ACCESS_TOKEN",
          "IG_BUSINESS_ACCOUNT_ID")


def test_rendering_needs_no_publishing_credentials(monkeypatch, tmp_path):
    """`python -m pytest tests/ -q` on a clean checkout used to fail twenty-one
    rendering tests on a missing META_APP_ID.

    render._handle() reads the handle through config.settings(), which is
    all-or-nothing, inside a `try/except Exception` - and config._req signals a
    missing setting with SystemExit, which derives from BaseException and is
    therefore not caught. The guard read correctly and did nothing, so drawing
    an account handle demanded four publishing credentials.

    CI sets placeholders, so nothing on CI would ever notice this coming back.
    This test clears them itself.
    """
    for k in _CREDS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("HANDLE", raising=False)
    post = json.loads(SAMPLES[0].read_text())
    paths = render_post(post, "neon", str(tmp_path / "bare"))
    assert len(paths) == len(post["slides"]) + 3


def test_a_reel_dry_run_needs_no_publishing_credentials(monkeypatch):
    """A dry run sends nothing, so it cannot need the ability to send."""
    import publish as publish_mod
    for k in _CREDS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(publish_mod, "_post", lambda *a, **k: pytest.fail("sent"))
    monkeypatch.setattr(publish_mod, "check_quota", lambda *a, **k: pytest.fail("sent"))
    post = json.loads(SAMPLES[0].read_text())
    post["status"] = "approved"
    res = publish_mod.publish_reel(post, "https://x.test/reel.mp4", live=False)
    assert res["mode"].startswith("DRY RUN")
