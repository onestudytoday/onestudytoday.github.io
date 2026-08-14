"""
Caption assembly + hashtag rotation.

The caption body comes from the drafting step. This module handles everything
structural around it: the source line, the link-in-bio pointer, the preprint
disclosure (which is mandatory and cannot be switched off), and the hashtag
block.

Hashtags rotate deterministically off the post id, so the same post always
produces the same tags, but consecutive posts never share an identical block.
"""

from __future__ import annotations

import hashlib
import random
from typing import Any, Dict, List

import yaml

from config import ROOT, settings

TAGS = yaml.safe_load((ROOT / "config" / "hashtags.yaml").read_text())

CAPTION_TEMPLATES = {
    # The drafting model writes `body`. These wrap it.
    "standard": (
        "{body}\n"
        "{preprint_line}"
        "———\n"
        "SOURCE: {authors_short}, \"{title}\"\n"
        "{journal_line}\n"
        "{link_line}\n"
        "———\n"
        "New study every weekday. Monday nature, Tuesday mind, Wednesday health, "
        "Thursday space, Friday whatever was best.\n"
        "Every paper we cover is linked in bio.\n\n"
        "{hashtags}"
    ),
}

PREPRINT_LINE = (
    "\nHEADS UP: this is a preprint. It has been posted publicly but has not "
    "been peer reviewed, so no independent expert has checked it yet. Treat it "
    "as an early signal, not a settled result.\n"
)


def _rng(post_id: str) -> random.Random:
    return random.Random(int(hashlib.sha1(post_id.encode()).hexdigest()[:8], 16))


def build_hashtags(post: Dict[str, Any], total: int = None) -> str:
    """Interleaves tiers so a small `total` still gets one of each size band.

    Default count comes from config/hashtags.yaml (5). Instagram removed
    hashtag following in Dec 2024 and now points at 3-5 relevant tags, with
    discovery driven by caption keywords - see the header of that file.
    """
    total = total or int(TAGS.get("hashtag_count", 5))
    key = post.get("hashtag_set") or post.get("niche") or "wildcard"
    s = TAGS["sets"].get(key, TAGS["sets"]["wildcard"])
    r = _rng(post["id"])

    anchor = r.sample(list(s["anchor"]) + TAGS["core"]["anchor"],
                      min(2, len(s["anchor"]) + len(TAGS["core"]["anchor"])))
    target = r.sample(s["target"], min(2, len(s["target"])))
    niche = r.sample(s["niche"], min(2, len(s["niche"]))) + \
        r.sample(TAGS["core"]["niche"], 1)
    rot = r.sample(TAGS["rotating"], 1)

    # round-robin the tiers so truncation never drops a whole band
    tiers = [anchor, target, niche, rot]
    tags: List[str] = []
    while any(tiers) and len(tags) < total:
        for tier in tiers:
            if not tier or len(tags) >= total:
                continue
            t = tier.pop(0)
            if t in TAGS["banned"] or t in tags:
                continue
            tags.append(t)
    return " ".join(tags[:total])


def build_caption(post: Dict[str, Any]) -> str:
    st = post["study"]
    s = settings()

    authors = st.get("authors") or []
    if len(authors) > 2:
        authors_short = f"{authors[0]} et al."
    elif authors:
        authors_short = " & ".join(authors)
    else:
        authors_short = "See paper"

    if st.get("is_preprint"):
        journal_line = f"{st.get('server') or 'Preprint server'} · {st['pub_date_display']} · PREPRINT"
    else:
        journal_line = f"{st['journal']} · {st['pub_date_display']}"

    link_line = f"LINK: {st.get('doi_display') or st.get('url', '')}"

    text = CAPTION_TEMPLATES["standard"].format(
        body=post.get("caption", "").strip(),
        preprint_line=PREPRINT_LINE if st.get("is_preprint") else "\n",
        authors_short=authors_short,
        title=st["title"][:180],
        journal_line=journal_line,
        link_line=link_line,
        hashtags=build_hashtags(post),
    )

    # Instagram hard-caps captions at 2,200 characters.
    if len(text) > 2200:
        over = len(text) - 2200
        body = post.get("caption", "").strip()
        post = {**post, "caption": body[: max(0, len(body) - over - 4)].rstrip() + "..."}
        return build_caption(post)
    return text


def caption_stats(caption: str) -> Dict[str, Any]:
    return {
        "chars": len(caption),
        "chars_remaining": 2200 - len(caption),
        "hashtags": caption.count("#"),
        "first_125": caption[:125],   # what shows before "more"
    }


if __name__ == "__main__":
    import json
    import sys
    p = json.loads(open(sys.argv[1]).read())
    c = build_caption(p)
    print(c)
    print("\n---")
    print(json.dumps(caption_stats(c), indent=2))
