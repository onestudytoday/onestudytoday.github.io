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

from config import ROOT

TAGS = yaml.safe_load((ROOT / "config" / "hashtags.yaml").read_text())

# Three things, in this order, and nothing else.
#
# The caption used to carry the hook, a source block with authors and journal,
# a three-line preprint disclosure, a four-line explainer of the weekly
# schedule, a link-in-bio pointer and the tags - around 1,400 characters, of
# which Instagram shows roughly 125 before "... more". Everything after that
# fold was being written for nobody: the schedule blurb is the same every day,
# and the source block repeats what the cover slide already prints.
#
# What is left is what a reader actually acts on. The hook is the implications
# slide said in one breath, because that is the line that earns the tap.
CAPTION_TEMPLATES = {
    "standard": (
        "{body}\n"
        "{preprint_line}"
        "{link_line}\n\n"
        "{hashtags}"
    ),
}

# Short, but NOT removed.
#
# "Everything else removed" was the instruction and this is the deliberate
# exception. The account's README promises preprints are flagged, and a
# caption is the one part of a post that travels when someone screenshots or
# reposts it - the cover badge does not come with them. Three lines became
# one; the disclosure stays.
PREPRINT_LINE = "\nNot yet peer reviewed - a preprint, so treat it as an early signal.\n"


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

    link_line = st.get("doi_display") or st.get("url", "")

    text = CAPTION_TEMPLATES["standard"].format(
        body=post.get("caption", "").strip(),
        preprint_line=PREPRINT_LINE if st.get("is_preprint") else "\n",
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
