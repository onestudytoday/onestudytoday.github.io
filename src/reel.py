"""
Turn the carousel slides into a Reel.

WHY THIS EXISTS
===============
A carousel is shown mostly to people who already follow the account. Reels are
the format Instagram pushes to people who do not, and at a standing start that
difference is the whole growth story - it is the one lever that raises reach
without anyone doing anything by hand every day.

The slides are already rendered and already committed to docs/img/<id>/ so the
Graph API can fetch them. This module makes a 9:16 video out of exactly those
JPEGs and writes it beside them, so it is served by the same GitHub Pages
setup, over the same public URL, with no new hosting anywhere.

HOW THE VIDEO IS BUILT, AND WHY THIS WAY
========================================
Every frame is composed in PIL and handed to ffmpeg as a finished image
sequence. ffmpeg only encodes; it does no compositing, no scaling and no
transitions.

That is a deliberate trade. The obvious alternative - one `filter_complex`
with `zoompan` and a chain of `xfade`s - is faster and considerably more
fragile: the filter graph has to be built as a string whose shape depends on
the number of slides, it fails with errors that point at the graph rather than
at the cause, and none of it can be unit-tested without invoking ffmpeg. Frame
composition in PIL is pure Python: `compose_frames()` is tested directly, the
geometry is inspectable, and a failure is a Python traceback. At ~500 frames a
run the cost is a few seconds, which is nothing against a job that already
waits on network calls.

Slides are 1080x1350 (4:5). Reels want 1080x1920 (9:16), so each slide is
centred on a canvas painted in the post's own background colour, with a slow
vertical drift across its hold. The drift matters: a video of perfectly static
images reads as a slideshow, and slideshows do not travel.

THE SPEC IS NOT ADVISORY
========================
Meta's Reels requirements that this file exists to satisfy, each enforced
below rather than hoped for:

  * MP4, H.264, yuv420p, progressive, closed GOP
  * `-movflags +faststart` - the moov atom must be at the FRONT of the file.
    Meta fetches the video with its own crawler, and without faststart the
    fetch can fail with a bare `status_code: ERROR` carrying no explanation.
    This is the single most common self-inflicted Reels failure.
  * An AAC audio track, 48 kHz, stereo, 128 kbps - silent, but present.
    Whether Reels strictly REQUIRE audio is not documented either way by Meta;
    the spec table prescribes audio parameters without ever saying a track is
    mandatory. A silent track cannot violate anything documented and removes
    the question, so one is always muxed in.
  * 3 seconds minimum, 15 minutes maximum, 23-60 fps, <= 300 MB.
    Duration and size are asserted after encoding, because a Reel that
    silently comes out 2.8 seconds long fails at the API with an error that
    does not mention duration.

    python src/reel.py <post-id>            # build from docs/img/<id>/
    python src/reel.py <post-id> --open     # ...and print the path
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PIL import Image

from config import DOCS, PUBLISHED, QUEUE, ROOT

# --- Reel geometry -----------------------------------------------------------
W, H = 1080, 1920
FPS = 30

# Per-slide timing. Six slides at 3.2s plus five 0.45s crossfades lands at
# about 17 seconds, which is comfortably inside the 3s-15min window and about
# where short-form retention still holds up.
SLIDE_SECONDS = 3.2
XFADE_SECONDS = 0.45
DRIFT_PX = 26          # vertical travel of a slide across its own hold

# --- Encoder settings --------------------------------------------------------
# Capped CRF, not constant bitrate. These frames are mostly large flat areas of
# one colour with crisp text over them, which x264 compresses extremely well -
# a fixed 6 Mbps spends about 11 MB on a 16-second Reel and looks identical to
# CRF 21, which spends around 1 MB. That matters because every Reel is
# COMMITTED to the repo so GitHub Pages can serve it to Meta's crawler, and the
# publish workflow clones with fetch-depth: 0 every 15 minutes. At 11 MB a post
# the repo would grow by roughly 2.8 GB a year and drag every clone with it.
# maxrate/bufsize stay as a ceiling so a busy frame cannot spike past Meta's
# 25 Mbps limit.
VIDEO_CRF = "21"
VIDEO_MAXRATE = "8M"
VIDEO_BUFSIZE = "12M"
AUDIO_BITRATE = "128k"
AUDIO_RATE = 48000

# --- Meta's documented limits, asserted after encode -------------------------
MIN_DURATION_S = 3.0
MAX_DURATION_S = 15 * 60
MAX_BYTES = 300 * 1024 * 1024

DEFAULT_BG = "#0B0B0F"


class ReelError(RuntimeError):
    pass


# A post id becomes a directory name, a URL path segment and a temp-dir prefix.
# It is built in draft.py as f"{pub_date}-{niche}-{key[:8]}" - and `pub_date` is
# copied raw from a source record and is never validated as a date anywhere
# (vet.py's STALE check reads age_days, which is None for an unparseable date,
# so a garbage date passes vetting rather than failing it). Nothing downstream
# checked the id before joining it onto a path, so "../../.." would have
# escaped the repo. linkinbio._og_image already rejects ids containing a slash;
# this is the same guard for the modules that grew later.
SAFE_POST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def check_post_id(post_id: str) -> str:
    if not SAFE_POST_ID.match(str(post_id or "")) or ".." in str(post_id):
        raise ReelError(
            f"Refusing to use {post_id!r} as a path segment: a post id must be "
            f"letters, digits, dot, dash or underscore.")
    return post_id


# ---------------------------------------------------------------------------
def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _require_ffmpeg() -> None:
    if not ffmpeg_available():
        raise ReelError(
            "ffmpeg/ffprobe not found on PATH.\n"
            "Do not assume a CI runner has it: ubuntu-latest has shipped "
            "without ffmpeg, which is why daily-draft.yml installs it "
            "explicitly rather than hoping. Install it with:\n"
            "  sudo apt-get install -y ffmpeg      (Debian/Ubuntu)\n"
            "  brew install ffmpeg                 (macOS)")


def _hex_rgb(s: str) -> Tuple[int, int, int]:
    s = (s or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return _hex_rgb(DEFAULT_BG)
    try:
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return _hex_rgb(DEFAULT_BG)


# ---------------------------------------------------------------------------
def slide_paths(post_id: str) -> List[Path]:
    """The committed JPEGs for a post, in slide order.

    Reads docs/img/<id>/ and NOT out/posts/<id>/. That is the same distinction
    that kept the account from publishing anything for its first two weeks:
    out/posts/ is gitignored scratch that exists only on the runner that
    rendered it, while docs/img/ is committed and therefore still there on the
    separate, later runner that publishes. A Reel is built on that later
    runner, so out/posts/ is empty by construction.
    """
    check_post_id(post_id)
    d = DOCS / "img" / post_id
    if not d.is_dir():
        return []
    return sorted(d.glob("*.jpg"))


def compose_frames(slides: Sequence[Image.Image], bg: str = DEFAULT_BG,
                   fps: int = FPS, slide_seconds: float = SLIDE_SECONDS,
                   xfade_seconds: float = XFADE_SECONDS,
                   drift_px: int = DRIFT_PX):
    """Yield finished 1080x1920 RGB frames, in order.

    A generator, not a list: 500-odd full-resolution frames held in memory at
    once is roughly 3 GB, which a CI runner will not survive. The caller
    writes each frame to disk and drops it.
    """
    if not slides:
        return
    bg_rgb = _hex_rgb(bg)
    hold = max(1, int(round(slide_seconds * fps)))
    xf = max(0, int(round(xfade_seconds * fps)))

    def canvas_for(img: Image.Image, drift_t: float) -> Image.Image:
        """One slide on the 9:16 canvas, drifted by `drift_t` in 0..1."""
        c = Image.new("RGB", (W, H), bg_rgb)
        im = img.convert("RGB")
        if im.width != W:
            im = im.resize((W, round(im.height * W / im.width)), Image.LANCZOS)
        # Centre, then drift upward across the hold. Clamped so a slide taller
        # than the canvas can never expose background at an edge.
        y0 = (H - im.height) // 2
        offset = int(round((drift_t - 0.5) * drift_px))
        y = y0 - offset
        if im.height >= H:
            y = min(0, max(H - im.height, y))
        c.paste(im, ((W - im.width) // 2, y))
        return c

    for i, img in enumerate(slides):
        last = i == len(slides) - 1
        # Hold. The final slide keeps the frames a crossfade would have eaten,
        # so the video does not end on a half-finished transition.
        n_hold = hold if (last or xf == 0) else max(1, hold - xf)
        for f in range(n_hold):
            yield canvas_for(img, f / max(1, hold - 1))
        if last or xf == 0:
            continue
        nxt = slides[i + 1]
        for f in range(xf):
            t = (f + 1) / (xf + 1)
            a = canvas_for(img, (n_hold + f) / max(1, hold - 1))
            b = canvas_for(nxt, 0.0)
            yield Image.blend(a, b, t)


def _probe(path: Path) -> Dict[str, Any]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise ReelError(f"ffprobe failed on {path.name}: {out.stderr.strip()[:400]}")
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError as e:
        raise ReelError(f"ffprobe returned unparseable JSON for {path.name}: {e}")


def verify(path: Path) -> Dict[str, Any]:
    """Check an encoded file against Meta's documented Reels limits.

    Run every time, not just in tests. Each of these failures surfaces at the
    API as a bare `status_code: ERROR` with no diagnostics, hours later and in
    a different workflow run - so they are worth catching here, where the
    message can say which limit was missed.
    """
    info = _probe(path)
    streams = info.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise ReelError(f"{path.name} has no video stream.")
    if audio is None:
        raise ReelError(
            f"{path.name} has no audio stream. Meta's Reels spec prescribes audio "
            f"parameters, and a silent AAC track is the safe reading of that.")

    duration = float((info.get("format") or {}).get("duration") or 0.0)
    size = int((info.get("format") or {}).get("size") or path.stat().st_size)

    if duration < MIN_DURATION_S:
        raise ReelError(
            f"{path.name} is {duration:.2f}s; Reels require at least "
            f"{MIN_DURATION_S:g}s. Raise SLIDE_SECONDS or add slides.")
    if duration > MAX_DURATION_S:
        raise ReelError(f"{path.name} is {duration:.0f}s; the maximum is "
                        f"{MAX_DURATION_S:.0f}s.")
    if size > MAX_BYTES:
        raise ReelError(f"{path.name} is {size / 1e6:.0f} MB; the maximum is 300 MB.")
    if video.get("pix_fmt") != "yuv420p":
        raise ReelError(f"{path.name} pix_fmt is {video.get('pix_fmt')}, expected "
                        f"yuv420p - other formats are rejected or silently mangled.")
    if video.get("codec_name") not in ("h264", "hevc"):
        raise ReelError(f"{path.name} video codec is {video.get('codec_name')}, "
                        f"expected h264 or hevc.")
    if audio.get("codec_name") != "aac":
        raise ReelError(f"{path.name} audio codec is {audio.get('codec_name')}, "
                        f"expected aac.")

    return {"duration": round(duration, 2), "bytes": size,
            "width": video.get("width"), "height": video.get("height"),
            "fps": video.get("r_frame_rate"), "pix_fmt": video.get("pix_fmt"),
            "vcodec": video.get("codec_name"), "acodec": audio.get("codec_name")}


def encode(frames_dir: Path, dest: Path, fps: int = FPS,
           audio: Optional[Path] = None) -> Path:
    """Encode a numbered frame directory to a Reels-compatible MP4.

    `audio` is an optional real soundtrack. When absent the track is silent,
    which is what every carousel-derived Reel has used so far.

    WHY THE SOUNDTRACK CANNOT BE A TRENDING SONG
    ============================================
    Meta's Content Publishing API gives no access to Instagram's licensed
    audio library, and a Reel's audio cannot be changed after it is published.
    So an API-published Reel can only carry audio that is already inside the
    file, which in practice means music you hold a licence to - CC0 or
    similar. Trending audio is a real ranking signal and is simply not
    reachable this way; the only route to it is posting by hand in the app.

    That is not a limitation to work around quietly. It is the reason
    build_feature_reel() writes a file that is equally usable either way:
    committed for the API to fetch, or downloaded and posted by hand with a
    trending sound picked in the app.
    """
    # Arguments before environment, the same order build_reel() uses and for
    # the same reason: "install ffmpeg" is a wrong answer to "your audio file
    # is missing". This function had them the other way round until the
    # ffmpeg-less test run caught it - which is precisely the configuration
    # that made the original ordering bug visible, one commit earlier.
    if audio is not None and not Path(audio).is_file():
        raise ReelError(f"audio track {audio} does not exist")
    _require_ffmpeg()
    dest.parent.mkdir(parents=True, exist_ok=True)
    audio_in = (["-stream_loop", "-1", "-i", str(audio)] if audio else
                ["-f", "lavfi", "-i",
                 f"anullsrc=channel_layout=stereo:sample_rate={AUDIO_RATE}"])
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-framerate", str(fps), "-i", str(frames_dir / "%06d.jpg"),
        # Either a real track (looped, then trimmed by -shortest) or silence.
        # -stream_loop -1 matters: a 12-second song under a 16-second still
        # would otherwise leave four seconds of silence, which reads as the
        # video having broken.
        *audio_in,
        "-c:v", "libx264", "-profile:v", "high", "-preset", "medium",
        "-pix_fmt", "yuv420p", "-r", str(fps),
        # Closed GOP, keyframe every second. Meta's spec asks for closed GOP
        # explicitly and open GOP is libx264's default in some builds.
        "-g", str(fps * 2), "-keyint_min", str(fps), "-sc_threshold", "0",
        "-flags", "+cgop",
        "-crf", VIDEO_CRF, "-maxrate", VIDEO_MAXRATE, "-bufsize", VIDEO_BUFSIZE,
        "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ar", str(AUDIO_RATE), "-ac", "2",
        "-shortest",
        # The moov atom to the front. Without this Meta's fetch of the file can
        # fail with an unexplained ERROR - see the module docstring.
        "-movflags", "+faststart",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if proc.returncode != 0:
        raise ReelError(f"ffmpeg failed:\n{proc.stderr.strip()[:1200]}")
    if not dest.exists() or dest.stat().st_size == 0:
        raise ReelError(f"ffmpeg reported success but {dest} is missing or empty.")
    return dest


# ---------------------------------------------------------------------------
# The Friday feature reel: ONE image, held.
# ---------------------------------------------------------------------------
FEATURE_SECONDS = 12.0
FEATURE_ZOOM = 0.06          # 6% push across the whole hold
AUDIO_DIR = ROOT / "assets" / "audio"
AUDIO_EXTS = (".mp3", ".m4a", ".aac", ".wav", ".ogg")


def feature_frames(image: Image.Image, bg: str = DEFAULT_BG,
                   seconds: float = FEATURE_SECONDS, fps: int = FPS,
                   zoom: float = FEATURE_ZOOM):
    """Yield frames for a single still, slowly pushing in.

    WHY A SEPARATE SHAPE FROM compose_frames()
    ==========================================
    The carousel-derived Reel shows six 4:5 slides drifting on a 9:16 canvas
    with crossfades between them. This module's own docstring predicted the
    problem and the first published one confirmed it: that reads as a
    slideshow, and slideshows do not travel. Every cut resets the viewer's
    attention and invites a swipe.

    One image held for twelve seconds is the opposite bet - nothing to swipe
    past, the whole frame doing one job. The slow push exists only so the
    video is not literally static, which some players and some viewers treat
    as a broken upload.

    A single still with audio is a perfectly ordinary Reel as far as Instagram
    is concerned; "a video" is a container requirement, not an editorial one.
    """
    total = max(1, int(round(seconds * fps)))
    canvas_bg = _hex_rgb(bg)
    src = image.convert("RGB")
    # Pre-scale once to the largest size any frame will need, then crop per
    # frame. Resizing the ORIGINAL every frame is both slower and visibly
    # softer, because each frame resamples from scratch at a different ratio.
    big_w = int(W * (1 + zoom))
    big_h = int(H * (1 + zoom))
    fitted = _cover(src, big_w, big_h)
    for i in range(total):
        t = i / max(1, total - 1)
        # Ease in and out so the motion has no hard start or stop.
        e = 0.5 - 0.5 * math.cos(math.pi * t)
        cur_w = int(big_w - (big_w - W) * e)
        cur_h = int(big_h - (big_h - H) * e)
        x = (fitted.width - cur_w) // 2
        y = (fitted.height - cur_h) // 2
        frame = fitted.crop((x, y, x + cur_w, y + cur_h)).resize(
            (W, H), Image.LANCZOS)
        if frame.size != (W, H):                       # paranoia
            canvas = Image.new("RGB", (W, H), canvas_bg)
            canvas.paste(frame, (0, 0))
            frame = canvas
        yield frame


def _cover(im: Image.Image, w: int, h: int) -> Image.Image:
    """Scale to COVER w x h, centre-cropping the overflow."""
    scale = max(w / im.width, h / im.height)
    out = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))),
                    Image.LANCZOS)
    left = (out.width - w) // 2
    top = (out.height - h) // 2
    return out.crop((left, top, left + w, top + h))


def pick_audio(name: str = "") -> Optional[Path]:
    """A licensed track from assets/audio/, or None for silence.

    Nothing is bundled with the repo: there is no track here until you put one
    there, and it must be one you are entitled to publish - CC0, or licensed
    to you. Instagram's own library is not reachable through the API (see
    encode()), and shipping "some music" with a tool that publishes to a real
    account would be handing you a copyright strike with extra steps.

    assets/audio/README.md spells this out next to the empty folder.
    """
    if not AUDIO_DIR.is_dir():
        return None
    tracks = sorted(p for p in AUDIO_DIR.iterdir()
                    if p.suffix.lower() in AUDIO_EXTS)
    if not tracks:
        return None
    if name:
        for t in tracks:
            if t.stem.lower() == name.lower():
                return t
        raise ReelError(
            f"no track called {name!r} in assets/audio/ "
            f"(found: {', '.join(t.stem for t in tracks) or 'nothing'})")
    return tracks[0]


def build_feature_reel(post_id: str, image: Path, audio: Optional[Path] = None,
                       bg: str = DEFAULT_BG, seconds: float = FEATURE_SECONDS,
                       dest: Optional[Path] = None) -> Dict[str, Any]:
    """One still + optional music, as docs/img/<id>/reel.mp4.

    Argument validation before the environment check, for the same reason
    build_reel() does it: telling someone to install ffmpeg when their real
    problem is a missing image is a wrong answer to a question they did not
    ask.
    """
    check_post_id(post_id)
    image = Path(image)
    if not image.is_file():
        raise ReelError(f"no such image: {image}")
    if seconds < MIN_DURATION_S:
        raise ReelError(
            f"a Reel must be at least {MIN_DURATION_S:g}s; {seconds:g}s was asked for.")
    _require_ffmpeg()

    dest = Path(dest) if dest else (DOCS / "img" / post_id / "reel.mp4")
    im = Image.open(image)
    try:
        with tempfile.TemporaryDirectory(prefix=f"reel-{post_id}-") as td:
            frames_dir = Path(td)
            n = 0
            for n, frame in enumerate(
                    feature_frames(im, bg=bg, seconds=seconds), start=1):
                frame.save(frames_dir / f"{n:06d}.jpg", "JPEG",
                           quality=94, optimize=False, progressive=False)
            if n == 0:
                raise ReelError("No frames were composed.")
            encode(frames_dir, dest, audio=audio)
    finally:
        im.close()

    info = verify(dest)
    info.update({"post_id": post_id, "path": str(dest), "slides": 1,
                 "kind": "feature", "audio": str(audio) if audio else ""})
    return info


def normalise_clip(src: Path, dest: Path, seconds: float = 15.0,
                   audio: Optional[Path] = None,
                   bg: str = DEFAULT_BG) -> Dict[str, Any]:
    """Turn a free-source video into a Reels-shaped MP4.

    For the other half of the Friday idea: a public-domain clip of the thing
    a study was about. NASA's video library is the obvious well for anything
    astronomical - it is public domain, no attribution legally required
    (though it is polite), and the footage is genuinely good. ESA, Wikimedia
    and Pexels are usable too but carry licence conditions, so whatever you
    download, keep the licence note with it.

    Scales to fit 1080x1920 and pads rather than cropping: a centre-crop of
    landscape footage throws away most of the frame, which for an orbital
    diagram or a labelled figure usually removes the actual subject.
    """
    src, dest = Path(src), Path(dest)
    if not src.is_file():
        raise ReelError(f"no such clip: {src}")
    if seconds < MIN_DURATION_S:
        raise ReelError(f"a Reel must be at least {MIN_DURATION_S:g}s.")
    _require_ffmpeg()
    dest.parent.mkdir(parents=True, exist_ok=True)
    r, g, b = _hex_rgb(bg)
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
          f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x{r:02x}{g:02x}{b:02x},"
          f"setsar=1,fps={FPS}")
    audio_in = (["-stream_loop", "-1", "-i", str(audio)] if audio else
                ["-f", "lavfi", "-i",
                 f"anullsrc=channel_layout=stereo:sample_rate={AUDIO_RATE}"])
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-stream_loop", "-1", "-i", str(src), *audio_in,
           "-map", "0:v:0", "-map", "1:a:0",
           "-t", f"{seconds:g}", "-vf", vf,
           "-c:v", "libx264", "-profile:v", "high", "-preset", "medium",
           "-pix_fmt", "yuv420p", "-r", str(FPS),
           "-g", str(FPS * 2), "-keyint_min", str(FPS), "-sc_threshold", "0",
           "-flags", "+cgop",
           "-crf", VIDEO_CRF, "-maxrate", VIDEO_MAXRATE, "-bufsize", VIDEO_BUFSIZE,
           "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ar", str(AUDIO_RATE), "-ac", "2",
           "-movflags", "+faststart", str(dest)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if proc.returncode != 0:
        raise ReelError(f"ffmpeg failed on {src.name}:\n{proc.stderr.strip()[:1200]}")
    info = verify(dest)
    info.update({"path": str(dest), "kind": "clip",
                 "audio": str(audio) if audio else ""})
    return info


def build_reel(post_id: str, bg: str = DEFAULT_BG,
               images: Optional[Sequence[Path]] = None,
               dest: Optional[Path] = None) -> Dict[str, Any]:
    """Build docs/img/<id>/reel.mp4 from that post's slides.

    `images` accepts the freshly rendered PNGs as well as the committed JPEGs -
    PIL opens either. That matters because of WHEN this runs: the Reel has to
    be built and committed during DRAFTING, not publishing.

    Instagram does not accept uploaded bytes; it fetches video_url with its own
    crawler. So the file has to already be committed and already served by
    GitHub Pages before the publish step names that URL. Building it on the
    publish runner would mean pointing Meta at a file that is still sitting on
    that runner's disk, unpushed and un-served - a 404 to the crawler, surfacing
    as a bare `status_code: ERROR` hours later. It is the same cross-runner
    mistake that stopped this account publishing anything for its first two
    weeks (out/posts/ vs docs/img/), in a new place.
    """
    # Argument validation FIRST, environment check second. The reverse order
    # means a caller who passed one slide is told to install ffmpeg, which is
    # not their problem and not the reason the call cannot succeed. It also
    # made the "refuses fewer than two slides" test fail on any machine
    # without ffmpeg - including CI, which does not install it - so the test
    # was reporting on the runner's packages rather than on this function.
    check_post_id(post_id)
    paths = list(images) if images is not None else slide_paths(post_id)
    if len(paths) < 2:
        raise ReelError(
            f"Need at least 2 slides to build a Reel for {post_id}, found "
            f"{len(paths)}. Looked in {DOCS / 'img' / post_id}. If the post has "
            f"not published yet its JPEGs are not committed there.")
    _require_ffmpeg()

    dest = dest or (DOCS / "img" / post_id / "reel.mp4")
    slides = [Image.open(p) for p in paths]
    try:
        with tempfile.TemporaryDirectory(prefix=f"reel-{post_id}-") as td:
            frames_dir = Path(td)
            n = 0
            for n, frame in enumerate(
                    compose_frames(slides, bg=bg), start=1):
                frame.save(frames_dir / f"{n:06d}.jpg", "JPEG",
                           quality=94, optimize=False, progressive=False)
            if n == 0:
                raise ReelError("No frames were composed.")
            encode(frames_dir, dest)
    finally:
        for im in slides:
            im.close()

    info = verify(dest)
    info.update({"post_id": post_id, "path": str(dest), "slides": len(paths),
                 "frames": n})
    return info


def reel_public_url(post_id: str, public_image_base: str) -> str:
    """Where Meta will fetch the video from.

    Same base as the slide JPEGs, so it is the same GitHub Pages origin that
    already serves images to the Graph API successfully. Nothing new to
    configure, and nothing new that can be misconfigured.
    """
    check_post_id(post_id)
    if not public_image_base:
        raise ReelError(
            "PUBLIC_IMAGE_BASE is not set. Instagram fetches the video from a "
            "public URL - it does not accept uploaded bytes.")
    return f"{public_image_base.rstrip('/')}/{post_id}/reel.mp4"


# ---------------------------------------------------------------------------
def _main() -> None:
    ap = argparse.ArgumentParser(description="Build a Reel from a post's slides")
    ap.add_argument("post_id")
    ap.add_argument("--bg", default=DEFAULT_BG)
    ap.add_argument("--open", action="store_true", help="print the output path only")
    ap.add_argument("--feature", metavar="IMAGE",
                    help="one still held for the whole Reel, instead of the "
                         "slide sequence (the Friday format)")
    ap.add_argument("--clip", metavar="VIDEO",
                    help="a free-source video clip, letterboxed to 9:16")
    ap.add_argument("--audio", metavar="NAME", default=None,
                    help="track name from assets/audio/ (default: the first "
                         "one there; omit the flag entirely for silence)")
    ap.add_argument("--seconds", type=float, default=None)
    a = ap.parse_args()

    if a.feature or a.clip:
        audio = pick_audio(a.audio) if a.audio is not None else None
        try:
            if a.feature:
                info = build_feature_reel(
                    a.post_id, Path(a.feature), audio=audio, bg=a.bg,
                    seconds=a.seconds or FEATURE_SECONDS)
            else:
                check_post_id(a.post_id)
                info = normalise_clip(
                    Path(a.clip), DOCS / "img" / a.post_id / "reel.mp4",
                    seconds=a.seconds or 15.0, audio=audio, bg=a.bg)
        except ReelError as e:
            raise SystemExit(f"\n{e}\n")
        print(info["path"] if a.open else json.dumps(info, indent=2))
        return

    # Use the post's own background colour when we can find its record.
    bg = a.bg
    for d in (PUBLISHED, QUEUE):
        f = d / f"{a.post_id}.json"
        if f.exists():
            try:
                post = json.loads(f.read_text())
                bg = (post.get("theme") or {}).get("bg") or bg
            except Exception:
                pass
            break

    try:
        info = build_reel(a.post_id, bg=bg)
    except ReelError as e:
        raise SystemExit(f"\n{e}\n")
    if a.open:
        print(info["path"])
    else:
        print(json.dumps(info, indent=2))


if __name__ == "__main__":
    _main()
