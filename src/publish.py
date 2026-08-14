"""
Instagram carousel publishing via the Graph API.

The API will not accept image bytes. It fetches a PUBLIC URL that you provide,
which is why the pipeline writes rendered slides into docs/img/ and lets GitHub
Pages serve them. That is the whole reason for the Pages setup.

Publishing a carousel is a three-step dance:

  1. one container per slide   POST /{ig-user}/media  is_carousel_item=true
  2. one parent container      POST /{ig-user}/media  media_type=CAROUSEL
  3. publish the parent        POST /{ig-user}/media_publish

Hard rules enforced here:
  * NOTHING publishes unless post["status"] == "approved". The review step is
    the only thing that sets that.
  * --dry-run is the default. You have to pass --live to actually post.
  * Slides are converted to JPEG, because the Graph API rejects PNG.
  * The daily publishing quota is checked before anything is created.

    python src/publish.py data/queue/<id>.json              # dry run
    python src/publish.py data/queue/<id>.json --live       # actually posts
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import requests
from PIL import Image

from caption import build_caption, caption_stats
from config import DOCS, PUBLISHED, QUEUE, settings

TIMEOUT = 60
POLL_SECONDS = 4
MAX_POLLS = 30
MAX_CAROUSEL_ITEMS = 10


class PublishError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
def to_jpeg(png_paths: List[str], dest: Path, quality: int = 92) -> List[Path]:
    """Graph API accepts JPEG. Convert, and keep files under the 8 MB limit."""
    dest.mkdir(parents=True, exist_ok=True)
    out = []
    for p in png_paths:
        src = Path(p)
        jp = dest / (src.stem + ".jpg")
        im = Image.open(src).convert("RGB")
        q = quality
        while True:
            im.save(jp, "JPEG", quality=q, optimize=True, progressive=True)
            if jp.stat().st_size <= 8 * 1024 * 1024 or q <= 60:
                break
            q -= 8
        out.append(jp)
    return out


def public_urls(jpegs: List[Path], post_id: str) -> List[str]:
    s = settings()
    if not s.public_image_base:
        raise PublishError(
            "PUBLIC_IMAGE_BASE is not set. Instagram can only publish images it "
            "can download from a public URL.\n"
            "Set it to your GitHub Pages base, e.g.\n"
            "  PUBLIC_IMAGE_BASE=https://<your-github-username>.github.io/<repo>/img"
        )
    base = s.public_image_base.rstrip("/")
    return [f"{base}/{post_id}/{j.name}" for j in jpegs]


def stage_images(post: Dict[str, Any], png_paths: List[str]) -> List[str]:
    """Copy JPEGs into docs/img/<post-id>/ so Pages serves them."""
    dest = DOCS / "img" / post["id"]
    jpegs = to_jpeg(png_paths, dest)
    return public_urls(jpegs, post["id"])


# ---------------------------------------------------------------------------
def check_quota() -> Dict[str, Any]:
    s = settings()
    r = requests.get(f"{s.graph}/{s.ig_business_account_id}/content_publishing_limit",
                     params={"fields": "config,quota_usage",
                             "access_token": s.ig_access_token}, timeout=TIMEOUT)
    j = r.json()
    if "error" in j:
        raise PublishError(f"Quota check failed: {json.dumps(j['error'], indent=2)}")
    return (j.get("data") or [{}])[0]


def _post(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    s = settings()
    params = {**params, "access_token": s.ig_access_token}
    r = requests.post(f"{s.graph}/{path}", data=params, timeout=TIMEOUT)
    j = r.json()
    if "error" in j:
        raise PublishError(f"POST /{path} failed:\n{json.dumps(j['error'], indent=2)}")
    return j


def wait_ready(container_id: str) -> None:
    s = settings()
    for _ in range(MAX_POLLS):
        r = requests.get(f"{s.graph}/{container_id}",
                         params={"fields": "status_code,status",
                                 "access_token": s.ig_access_token}, timeout=TIMEOUT)
        j = r.json()
        code = j.get("status_code")
        if code == "FINISHED":
            return
        if code == "ERROR":
            raise PublishError(f"Container {container_id} errored: {j.get('status')}")
        time.sleep(POLL_SECONDS)
    raise PublishError(f"Container {container_id} never became ready.")


# ---------------------------------------------------------------------------
def publish(post: Dict[str, Any], image_urls: List[str], live: bool) -> Dict[str, Any]:
    s = settings()
    caption = build_caption(post)

    if post.get("status") != "approved":
        raise PublishError(
            f"Refusing to publish: status is '{post.get('status')}', not 'approved'.\n"
            f"Approve it first:  python src/review.py approve {post['id']}")

    if not (2 <= len(image_urls) <= MAX_CAROUSEL_ITEMS):
        raise PublishError(f"Carousel needs 2-{MAX_CAROUSEL_ITEMS} images, got {len(image_urls)}")

    plan = {
        "post_id": post["id"],
        "slides": len(image_urls),
        "image_urls": image_urls,
        "caption_chars": len(caption),
        "caption_preview": caption[:300],
        "caption_stats": caption_stats(caption),
    }

    if not live:
        plan["mode"] = "DRY RUN - nothing was sent to Instagram"
        return plan

    quota = check_quota()
    used = (quota.get("quota_usage") or 0)
    cap = ((quota.get("config") or {}).get("quota_total") or 25)
    if used >= cap:
        raise PublishError(f"Daily publishing quota exhausted ({used}/{cap}).")

    children = []
    for url in image_urls:
        c = _post(f"{s.ig_business_account_id}/media",
                  {"image_url": url, "is_carousel_item": "true"})
        children.append(c["id"])
    for cid in children:
        wait_ready(cid)

    parent = _post(f"{s.ig_business_account_id}/media",
                   {"media_type": "CAROUSEL",
                    "children": ",".join(children),
                    "caption": caption})
    wait_ready(parent["id"])

    result = _post(f"{s.ig_business_account_id}/media_publish",
                   {"creation_id": parent["id"]})

    plan.update({"mode": "LIVE", "children": children,
                 "container_id": parent["id"], "media_id": result.get("id"),
                 "quota_before": f"{used}/{cap}"})
    return plan


# ---------------------------------------------------------------------------
def insights(media_id: str) -> Dict[str, Any]:
    """Engagement for the Friday wildcard ranking."""
    s = settings()
    r = requests.get(f"{s.graph}/{media_id}/insights",
                     params={"metric": "reach,likes,comments,saved,shares,profile_visits",
                             "access_token": s.ig_access_token}, timeout=TIMEOUT)
    j = r.json()
    if "error" in j:
        return {"error": j["error"]}
    return {d["name"]: d["values"][0]["value"] for d in j.get("data", [])}


# ---------------------------------------------------------------------------
def _main():
    ap = argparse.ArgumentParser()
    ap.add_argument("post_json")
    ap.add_argument("--live", action="store_true",
                    help="actually publish. Without this it is a dry run.")
    ap.add_argument("--images", nargs="*", default=None,
                    help="explicit slide PNGs; defaults to out/posts/<id>/")
    a = ap.parse_args()

    post = json.loads(Path(a.post_json).read_text())
    if a.images:
        pngs = a.images
    else:
        from config import OUT
        d = OUT / "posts" / post["id"]
        pngs = sorted(str(p) for p in d.glob("*.png"))
    if not pngs:
        raise SystemExit(f"No rendered slides found for {post['id']}. "
                         f"Run: python src/pipeline.py render {post['id']}")

    urls = stage_images(post, pngs)
    res = publish(post, urls, live=a.live)
    print(json.dumps(res, indent=2))

    if a.live and res.get("media_id"):
        post["status"] = "published"
        post["published"] = {"media_id": res["media_id"],
                             "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        (PUBLISHED / f"{post['id']}.json").write_text(json.dumps(post, indent=2))
        q = QUEUE / f"{post['id']}.json"
        if q.exists():
            q.unlink()
        print(f"\nPublished. Moved to data/published/{post['id']}.json")


if __name__ == "__main__":
    _main()
