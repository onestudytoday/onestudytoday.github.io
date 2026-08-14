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
