"""
Tests for Reel generation and Reel publishing.

Weighted toward the failure modes that are EXPENSIVE rather than the ones that
are easy to test. A Reel that is malformed does not fail loudly at build time -
it fails hours later, in a different workflow run, as a bare
`status_code: ERROR` from Meta carrying no explanation at all. So the checks
that matter are the ones that catch a bad file before it is ever named to the
API, and the cross-runner assumption about WHERE the file has to exist.

    python -m pytest tests/ -q
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pipeline                      # noqa: E402
import publish as publish_mod        # noqa: E402
import reel                          # noqa: E402

HAS_FFMPEG = reel.ffmpeg_available()
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")


def _slides(n=3, size=(1080, 1350)):
    out = []
    for i in range(n):
        im = Image.new("RGB", size, (10 + i * 40, 20, 30))
        out.append(im)
    return out


def _slide_files(tmp_path, n=3, ext="jpg"):
    d = tmp_path / "slides"
    d.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, im in enumerate(_slides(n)):
        p = d / f"{i:02d}_slide.{ext}"
        im.save(p, "JPEG" if ext == "jpg" else "PNG")
        paths.append(p)
    return paths


# ---------------------------------------------------------------------------
# Frame composition — pure PIL, no ffmpeg needed
# ---------------------------------------------------------------------------
def test_frames_are_9x16_regardless_of_slide_shape():
    """Slides are 4:5. Reels are 9:16. Every frame must be the target shape."""
    frames = list(reel.compose_frames(_slides(2), slide_seconds=0.2,
                                      xfade_seconds=0.1))
    assert frames, "no frames composed"
    assert all(f.size == (reel.W, reel.H) for f in frames)
    assert (reel.W, reel.H) == (1080, 1920)


def test_frame_count_matches_requested_duration():
    fps, hold, xf = 30, 1.0, 0.2
    n = len(list(reel.compose_frames(_slides(3), fps=fps, slide_seconds=hold,
                                     xfade_seconds=xf)))
    # 2 slides shortened by the crossfade, 1 full-length final slide, plus the
    # crossfade frames themselves.
    expected = 2 * (30 - 6) + 30 + 2 * 6
    assert n == expected, f"expected {expected} frames, got {n}"


def test_video_is_long_enough_to_be_a_legal_reel():
    """Meta's floor is 3 seconds. The defaults must clear it for a short post.

    A 2-slide post is the minimum this pipeline will build, so it is the case
    that decides whether the defaults are safe.
    """
    n = len(list(reel.compose_frames(_slides(2))))
    assert n / reel.FPS >= reel.MIN_DURATION_S


def test_compose_frames_is_a_generator_not_a_list():
    """500 full-res frames in memory at once is ~3GB and kills a CI runner."""
    g = reel.compose_frames(_slides(2))
    assert hasattr(g, "__next__") and not isinstance(g, list)


def test_no_frames_for_no_slides():
    assert list(reel.compose_frames([])) == []


def test_bad_background_colour_falls_back_instead_of_crashing():
    for bad in ("", "not-a-colour", "#ZZZ", None):
        frames = list(reel.compose_frames(_slides(2), bg=bad, slide_seconds=0.1,
                                          xfade_seconds=0.0))
        assert frames and frames[0].size == (reel.W, reel.H)


# ---------------------------------------------------------------------------
# Where the slides are read from — the cross-runner seam
# ---------------------------------------------------------------------------
def test_slide_paths_reads_committed_docs_img_not_gitignored_out_posts(tmp_path,
                                                                      monkeypatch):
    """The bug that stopped this account publishing for two weeks, in a new place.

    out/posts/ is gitignored scratch that exists only on the runner that
    rendered it. docs/img/ is committed and therefore still present on the
    later publish runner. A Reel must be built from the committed copy.
    """
    docs = tmp_path / "docs"
    (docs / "img" / "POST").mkdir(parents=True)
    for name in ("01.jpg", "02.jpg"):
        Image.new("RGB", (10, 12)).save(docs / "img" / "POST" / name)
    (tmp_path / "out" / "posts" / "POST").mkdir(parents=True)

    monkeypatch.setattr(reel, "DOCS", docs)
    got = reel.slide_paths("POST")
    assert [p.name for p in got] == ["01.jpg", "02.jpg"]
    assert all("docs" in str(p) for p in got)


def test_slide_paths_empty_for_unknown_post(tmp_path, monkeypatch):
    monkeypatch.setattr(reel, "DOCS", tmp_path)
    assert reel.slide_paths("nope") == []


def test_build_reel_refuses_fewer_than_two_slides(tmp_path, monkeypatch):
    monkeypatch.setattr(reel, "DOCS", tmp_path)
    with pytest.raises(reel.ReelError) as e:
        reel.build_reel("POST", images=_slide_files(tmp_path, n=1))
    assert "at least 2" in str(e.value)


def test_reel_public_url_matches_the_slide_image_base():
    url = reel.reel_public_url("2026-08-27-wildcard-abc",
                               "https://onestudytoday.github.io/img")
    assert url == ("https://onestudytoday.github.io/img/"
                   "2026-08-27-wildcard-abc/reel.mp4")


def test_reel_public_url_requires_a_base():
    with pytest.raises(reel.ReelError):
        reel.reel_public_url("x", "")


# ---------------------------------------------------------------------------
# Real encode — the only test that proves the file Meta receives is valid
# ---------------------------------------------------------------------------
@needs_ffmpeg
def test_encoded_reel_meets_every_documented_meta_requirement(tmp_path):
    dest = tmp_path / "reel.mp4"
    info = reel.build_reel("POST", images=_slide_files(tmp_path, n=3), dest=dest)

    assert dest.exists()
    assert (info["width"], info["height"]) == (1080, 1920)
    assert info["vcodec"] == "h264"
    assert info["acodec"] == "aac", "a silent AAC track must always be muxed in"
    assert info["pix_fmt"] == "yuv420p"
    assert reel.MIN_DURATION_S <= info["duration"] <= reel.MAX_DURATION_S
    assert info["bytes"] <= reel.MAX_BYTES


@needs_ffmpeg
def test_moov_atom_is_at_the_front(tmp_path):
    """-movflags +faststart. Without it Meta's fetch can fail with a bare ERROR.

    This is the single most common self-inflicted Reels failure and it is
    invisible in every other check - the file plays perfectly locally.
    """
    dest = tmp_path / "reel.mp4"
    reel.build_reel("POST", images=_slide_files(tmp_path, n=2), dest=dest)
    head = dest.read_bytes()[:400_000]
    moov, mdat = head.find(b"moov"), head.find(b"mdat")
    assert moov >= 0, "moov atom not found near the start of the file"
    assert mdat < 0 or moov < mdat, "moov must precede mdat (faststart)"


@needs_ffmpeg
def test_reel_stays_small_enough_to_commit(tmp_path):
    """Every Reel is committed so Pages can serve it, and the publish workflow
    clones with fetch-depth: 0 every 15 minutes. Size is a repo-health issue,
    not just an API limit."""
    dest = tmp_path / "reel.mp4"
    info = reel.build_reel("POST", images=_slide_files(tmp_path, n=5), dest=dest)
    assert info["bytes"] < 8 * 1024 * 1024, (
        f"{info['bytes'] / 1e6:.1f}MB per Reel is too heavy to commit weekly")


@needs_ffmpeg
def test_verify_rejects_a_file_with_no_audio_stream(tmp_path):
    silent = tmp_path / "noaudio.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "color=c=black:s=1080x1920:d=4", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", str(silent)], check=True, timeout=180)
    with pytest.raises(reel.ReelError) as e:
        reel.verify(silent)
    assert "audio" in str(e.value).lower()


@needs_ffmpeg
def test_verify_rejects_a_video_shorter_than_the_three_second_floor(tmp_path):
    short = tmp_path / "short.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=black:s=640x480:d=1.5",
         "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
         str(short)], check=True, timeout=180)
    with pytest.raises(reel.ReelError) as e:
        reel.verify(short)
    assert "at least" in str(e.value)


def test_missing_ffmpeg_gives_an_actionable_error(monkeypatch):
    monkeypatch.setattr(reel.shutil, "which", lambda _: None)
    with pytest.raises(reel.ReelError) as e:
        reel._require_ffmpeg()
    assert "apt-get install" in str(e.value)


# ---------------------------------------------------------------------------
# Publishing a Reel
# ---------------------------------------------------------------------------
def _approved(post_id="2026-08-28-wildcard-abc", niche="wildcard"):
    return {"id": post_id, "status": "approved", "niche": niche,
            "cover": {"headline": "A finding", "kicker": "Nature"},
            "study": {"doi": f"10.1000/{post_id}", "title": "T", "journal": "Nature"}}


def test_publish_reel_refuses_an_unapproved_post():
    post = _approved()
    post["status"] = "draft"
    with pytest.raises(publish_mod.PublishError) as e:
        publish_mod.publish_reel(post, "https://x.test/reel.mp4", live=False)
    assert "not 'approved'" in str(e.value)


def test_publish_reel_refuses_without_a_video_url():
    with pytest.raises(publish_mod.PublishError):
        publish_mod.publish_reel(_approved(), "", live=False)


def test_publish_reel_dry_run_sends_nothing(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("no network call may happen on a dry run")
    monkeypatch.setattr(publish_mod, "_post", boom)
    monkeypatch.setattr(publish_mod, "check_quota", boom)
    monkeypatch.setattr(publish_mod, "build_caption", lambda p: "caption")
    monkeypatch.setattr(publish_mod, "caption_stats", lambda c: {})
    res = publish_mod.publish_reel(_approved(), "https://x.test/reel.mp4", live=False)
    assert res["mode"].startswith("DRY RUN")
    assert res["kind"] == "REELS"


def test_wait_ready_treats_published_as_terminal_not_as_a_timeout(monkeypatch):
    """PUBLISHED means this media is ALREADY LIVE.

    The old loop polled through it to "never became ready", which reads as a
    transient failure and invites a retry - i.e. posting the same thing twice.
    That is the exact class of bug the 24 Aug audit was written about.
    """
    class FakeResp:
        @staticmethod
        def json():
            return {"status_code": "PUBLISHED"}

    monkeypatch.setattr(publish_mod.requests, "get", lambda *a, **kw: FakeResp())
    monkeypatch.setattr(publish_mod, "settings", lambda: _FakeSettings())
    with pytest.raises(publish_mod.PublishError) as e:
        publish_mod.wait_ready("123", max_polls=2, poll_seconds=0)
    assert "ALREADY LIVE" in str(e.value)


def test_wait_ready_treats_expired_as_terminal(monkeypatch):
    class FakeResp:
        @staticmethod
        def json():
            return {"status_code": "EXPIRED"}

    monkeypatch.setattr(publish_mod.requests, "get", lambda *a, **kw: FakeResp())
    monkeypatch.setattr(publish_mod, "settings", lambda: _FakeSettings())
    with pytest.raises(publish_mod.PublishError) as e:
        publish_mod.wait_ready("123", max_polls=99, poll_seconds=0)
    assert "EXPIRED" in str(e.value)


class _FakeSettings:
    graph = "https://graph.test/v23.0"
    ig_access_token = "tok"
    ig_business_account_id = "17841400000000000"
    public_image_base = "https://onestudytoday.github.io/img"


# ---------------------------------------------------------------------------
# Pipeline wiring: which format goes out
# ---------------------------------------------------------------------------
def test_reel_niches_default_to_the_friday_wildcard(monkeypatch):
    monkeypatch.delenv("REEL_NICHES", raising=False)
    assert pipeline.wants_reel("wildcard") is True
    assert pipeline.wants_reel("nature") is False


def test_reel_niches_configurable_and_disableable(monkeypatch):
    monkeypatch.setenv("REEL_NICHES", "nature,health")
    assert pipeline.wants_reel("nature") is True
    assert pipeline.wants_reel("wildcard") is False
    # "off" is the sentinel, NOT "". An empty env var means "unset" throughout
    # this codebase (config._opt), because Actions sets undefined vars to "" -
    # so "" has to keep meaning "use the default" or the ALLOW_PREPRINTS class
    # of silent-wrong-behaviour bug comes straight back.
    monkeypatch.setenv("REEL_NICHES", "off")
    assert pipeline.wants_reel("wildcard") is False
    assert pipeline.wants_reel("nature") is False


def test_empty_reel_niches_means_unset_not_off(monkeypatch):
    """Guards the _opt empty-vs-unset contract at this call site."""
    monkeypatch.setenv("REEL_NICHES", "")
    assert pipeline.wants_reel("wildcard") is True, (
        "an empty env var must fall back to the default, as _opt documents; "
        "use REEL_NICHES=off to disable")


def test_reel_niche_without_a_committed_mp4_falls_back_to_carousel(tmp_path,
                                                                   monkeypatch,
                                                                   capsys):
    """A missing Reel must not stop the post going out.

    Wrong format is a bad day. Nothing published is a broken account.
    """
    docs = tmp_path / "docs"
    post = _approved()
    img = docs / "img" / post["id"]
    img.mkdir(parents=True)
    for n in ("01.jpg", "02.jpg"):
        (img / n).write_bytes(b"x")
    # deliberately NO reel.mp4

    monkeypatch.setenv("REEL_NICHES", "wildcard")
    monkeypatch.setattr(pipeline, "DOCS", docs)
    monkeypatch.setattr(pipeline, "OUT", tmp_path / "out")
    monkeypatch.setattr(publish_mod, "public_urls",
                        lambda j, pid: [f"https://x.test/{pid}/{p.name}" for p in j])
    called = {}
    monkeypatch.setattr(publish_mod, "publish",
                        lambda p, urls, live: called.setdefault("carousel", True)
                        or {"mode": "DRY RUN"})
    monkeypatch.setattr(publish_mod, "publish_reel",
                        lambda *a, **kw: pytest.fail("must not publish a Reel"))

    f = tmp_path / "q.json"
    f.write_text(json.dumps(post))
    pipeline._publish_one(f, post, live=False)
    assert called.get("carousel")
    assert "no reel.mp4" in capsys.readouterr().out


def test_reel_niche_with_a_committed_mp4_publishes_a_reel(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    post = _approved()
    img = docs / "img" / post["id"]
    img.mkdir(parents=True)
    for n in ("01.jpg", "02.jpg"):
        (img / n).write_bytes(b"x")
    (img / "reel.mp4").write_bytes(b"fake-mp4")

    monkeypatch.setenv("REEL_NICHES", "wildcard")
    monkeypatch.setattr(pipeline, "DOCS", docs)
    monkeypatch.setattr(pipeline, "OUT", tmp_path / "out")
    monkeypatch.setattr(pipeline, "settings", lambda: _FakeSettings())
    monkeypatch.setattr(publish_mod, "public_urls",
                        lambda j, pid: [f"https://x.test/{pid}/{p.name}" for p in j])
    monkeypatch.setattr(publish_mod, "publish",
                        lambda *a, **kw: pytest.fail("must not publish a carousel"))
    seen = {}

    def fake_reel(p, url, live, **kw):
        seen["url"] = url
        return {"mode": "DRY RUN", "kind": "REELS"}

    monkeypatch.setattr(publish_mod, "publish_reel", fake_reel)

    f = tmp_path / "q.json"
    f.write_text(json.dumps(post))
    res = pipeline._publish_one(f, post, live=False)
    assert res["kind"] == "REELS"
    assert seen["url"].endswith(f"/{post['id']}/reel.mp4")
    assert seen["url"].startswith("https://")
