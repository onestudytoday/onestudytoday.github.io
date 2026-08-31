"""
Regression test for the production incident of 24 Aug 2026: even after
scheduled-publish.yml itself was fixed (an invalid workflow file) and the
linkinbio.py credentials bug was fixed, "Publish approved posts" ran clean
and green but still did not publish anything - the log read

    2026-08-23-nature-e102a437: no rendered slides, skipping

pipeline._publish_one() looked for rendered slides in out/posts/<id>/*.png.
But out/posts/ is gitignored working-file scratch (see .gitignore -
"Rendered PNGs are working files. The JPEGs in docs/img/ ARE committed"),
produced fresh by whichever runner drafted and rendered the post
(daily-draft.yml) and never committed. scheduled-publish.yml runs on a
separate, later, freshly-checked-out runner, so out/posts/<id>/ is always
empty there - even though the JPEGs daily-draft.yml already staged and
committed to docs/img/<id>/ are sitting right there. _publish_one() now
checks docs/img/<id>/ first and only falls back to converting fresh
out/posts/ PNGs for the same-session local flow.

    python -m pytest tests/ -q
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pipeline                      # noqa: E402
import publish as publish_mod        # noqa: E402


def _approved_post(post_id="2026-08-23-nature-e102a437", niche="nature"):
    return {"id": post_id, "status": "approved", "niche": niche,
            "study": {"doi": f"10.1000/{post_id}"}}


def test_publish_uses_already_staged_jpegs_when_out_posts_is_empty(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    out = tmp_path / "out"          # out/posts/<id>/ deliberately never created
    post = _approved_post()
    img_dir = docs / "img" / post["id"]
    img_dir.mkdir(parents=True)
    for name in ("01_cover.jpg", "02_setup.jpg", "03_caveats.jpg", "04_cta.jpg"):
        (img_dir / name).write_bytes(b"fake-jpeg-bytes")

    monkeypatch.setattr(pipeline, "DOCS", docs)
    monkeypatch.setattr(pipeline, "OUT", out)
    monkeypatch.setattr(
        publish_mod, "public_urls",
        lambda jpegs, post_id: [f"https://example.test/{post_id}/{j.name}" for j in jpegs])
    monkeypatch.setattr(publish_mod, "publish",
                        lambda post, urls, live: {"mode": "DRY RUN", "urls": urls})

    f = tmp_path / f"{post['id']}.json"
    f.write_text(json.dumps(post))

    res = pipeline._publish_one(f, post, live=False)
    assert res["urls"] == [f"https://example.test/{post['id']}/{n}"
                           for n in ("01_cover.jpg", "02_setup.jpg",
                                     "03_caveats.jpg", "04_cta.jpg")]


def test_publish_still_works_from_same_session_out_posts_pngs(tmp_path, monkeypatch):
    # The local flow README documents - `pipeline.py run` then
    # `publish-approved` in the same shell - has no staged docs/img/ yet.
    # That must still work exactly as before.
    docs = tmp_path / "docs"
    out = tmp_path / "out"
    post = _approved_post("2026-08-24-psych-cafef00d", "psych")
    png_dir = out / "posts" / post["id"]
    png_dir.mkdir(parents=True)
    (png_dir / "01_cover.png").write_bytes(b"fake-png-bytes")
    (png_dir / "02_setup.png").write_bytes(b"fake-png-bytes")

    monkeypatch.setattr(pipeline, "DOCS", docs)
    monkeypatch.setattr(pipeline, "OUT", out)
    monkeypatch.setattr(
        publish_mod, "stage_images",
        lambda post, pngs: [f"https://example.test/{post['id']}/{Path(p).stem}.jpg"
                            for p in pngs])
    monkeypatch.setattr(publish_mod, "publish",
                        lambda post, urls, live: {"mode": "DRY RUN", "urls": urls})

    f = tmp_path / f"{post['id']}.json"
    f.write_text(json.dumps(post))

    res = pipeline._publish_one(f, post, live=False)
    assert len(res["urls"]) == 2


def test_publish_raises_loudly_when_nothing_is_rendered_anywhere(tmp_path, monkeypatch):
    """A post with no slides anywhere must FAIL, not quietly return {}.

    This test previously asserted `res == {}` - that skipping silently was
    correct. It was not, and the 24 Aug audit said so in as many words: it
    listed "a test asserted that 'no slides found, skip quietly' was correct"
    as one of two tests actively pinning a bug in place. The audit changed
    other things and this one survived.

    Why the quiet skip is wrong: `return {}` raises nothing, so
    _publish_batch counts it as a success, the step exits 0, no post id shows
    up in `git status --porcelain data/published`, the `if: failure()` alert
    never fires, and the queue file is left in place with status still
    "approved". The scheduled workflow therefore retries it every 15 minutes,
    green, forever, while the review issue stays open and nobody is told. A
    post that can never publish is exactly the thing that has to be loud.
    """
    import pytest
    from publish import PublishError

    docs = tmp_path / "docs"
    out = tmp_path / "out"
    post = _approved_post("2026-08-24-health-deadfeed", "health")

    monkeypatch.setattr(pipeline, "DOCS", docs)
    monkeypatch.setattr(pipeline, "OUT", out)

    f = tmp_path / f"{post['id']}.json"
    f.write_text(json.dumps(post))

    with pytest.raises(PublishError) as e:
        pipeline._publish_one(f, post, live=False)
    assert "nothing to publish" in str(e.value)
    assert f.exists(), "the queue file must survive so the post can be re-rendered"
