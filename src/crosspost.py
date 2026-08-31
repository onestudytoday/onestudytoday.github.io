"""
Post the finding and its DOI to Bluesky and Threads when a study publishes.

WHY
===
Instagram is where the carousel lives, but it is a bad place for a link and a
worse place to be found by researchers. Bluesky is where the science community
actually went, and a post there costs nothing and reaches people whose approval
is what makes a science account credible. Threads is Meta's own text surface,
where a link behaves like a link.

Neither is a growth channel on its own. Both are automatic side effects of
publishing, which is the only reason they are worth having: they cost no
ongoing attention.

BOTH ARE OPTIONAL AND BOTH FAIL SOFT
====================================
A crosspost failing must never fail the run that publishes to Instagram. The
Instagram post is the product; these are echoes of it. So every function here
returns a result dict with "ok": True/False and an explanation, and raises
nothing that reaches the publish path. `crosspost_all()` is the entry point and
catches everything.

Credentials absent means "not configured", which is reported and skipped - not
an error, and specifically not a silent success. The distinction matters: a
crosspost that is quietly doing nothing looks identical to one that is working
until you go and look at the account.

THE TWO PLATFORMS ARE NOT SYMMETRIC
===================================
Bluesky needs an app password and nothing else - free, open, no review.

Threads needs its OWN Meta app, its own OAuth flow, its own long-lived token
and its own user id. An Instagram token does NOT work: Meta requires an app
configured with the Threads use case, and the Threads app id/secret are
distinct from the standard Meta app credentials. That is a real setup cost, so
Threads stays entirely inert until THREADS_ACCESS_TOKEN and THREADS_USER_ID
are set, and the runbook explains what to do. Bluesky is the one worth turning
on first.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from urllib.parse import urlsplit
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

from config import _opt as _opt_setting
from secrets_guard import redact, safe_error

TIMEOUT = 30

# app.bsky.feed.post enforces BOTH of these on `text`, simultaneously.
BSKY_MAX_GRAPHEMES = 300
BSKY_MAX_BYTES = 3000
# Threads' product limit. Meta's API docs never state a number - they only note
# that emoji count as their UTF-8 byte length - so this is enforced client-side.
THREADS_MAX_CHARS = 500

BSKY_DEFAULT_PDS = "https://bsky.social"
THREADS_BASE = "https://graph.threads.net/v1.0"


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------
def _grapheme_len(text: str) -> int:
    """Count user-perceived characters, which is what Bluesky's 300 limit means.

    A flag or a family emoji is one grapheme but several code points and many
    bytes, so len() overcounts and would truncate a legal post.

    THIS IS AN APPROXIMATION AND IT UNDERCOUNTS ON HOSTILE INPUT. It skips
    combining marks, ZWJ, skin-tone modifiers and variation selectors, so a
    string of 4000 combining acute accents measures as ZERO graphemes. Journal
    names are third-party text from public databases, so that input is
    attacker-chosen, and this function is therefore NOT a safety limit on its
    own - every caller must also enforce a hard cap. `_hard_cap()` below is
    that cap, and `compose_text` applies it last, after everything else.

    An earlier version of this docstring claimed the byte check made
    undercounting harmless. That was wrong twice over: the Threads path passed
    no byte limit at all (so 4062 characters went out against a 500 limit),
    and on the Bluesky path the byte trim cut the END of the string - which is
    where the link lives - silently deleting the one thing the crosspost
    exists to carry.
    """
    n, prev_zwj = 0, False
    for ch in text:
        if unicodedata.combining(ch):
            continue
        if ch == "‍":                 # zero-width joiner
            prev_zwj = True
            continue
        if prev_zwj:
            prev_zwj = False
            continue
        if "\U0001F3FB" <= ch <= "\U0001F3FF":   # skin-tone modifiers
            continue
        if "\U000E0020" <= ch <= "\U000E007F":   # tag characters (flags)
            continue
        if "︀" <= ch <= "️":           # variation selectors
            continue
        n += 1
    return n


_DOI_SHAPE = re.compile(r"^10\.\d{4,9}/\S+$")


def study_link(post: Dict[str, Any]) -> str:
    """The URL to link to, or "" if there isn't a safe one.

    Both `doi` and `url` come from public research databases - i.e. from
    strangers - and neither was validated here. linkinbio._safe_url already
    exists for exactly this reason and its docstring names the threat, but
    this module shipped without the equivalent check, so a `javascript:` or
    `data:` URL in a database record reached three sinks unexamined: the
    Bluesky link facet uri, the embed's external.uri, and Threads'
    link_attachment.

    Today's two sources happen to construct their URLs rather than copy them,
    so this was latent rather than live. "Latent" is a statement about the
    current sources, not about the code - one new source, or one hand-edited
    JSON through this module's own CLI, and it is live.

    The DOI is shape-checked too: it is interpolated into a doi.org URL, so a
    malformed one produces a malformed link at best.
    """
    st = post.get("study") or {}
    doi = (st.get("doi") or "").strip()
    if doi and _DOI_SHAPE.match(doi):
        return f"https://doi.org/{doi}"
    raw = (st.get("url") or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if parsed.scheme.lower() in ("http", "https") and parsed.netloc:
        return raw
    return ""


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _hard_cap(text: str, limit_graphemes: int) -> str:
    """A code-point ceiling that no input can talk its way past.

    _grapheme_len can be driven to 0 by combining marks, so it cannot be the
    only gate. A grapheme is at most a handful of code points in any realistic
    text, so limit*4 code points is comfortably above any legitimate string of
    `limit` graphemes while still bounding a hostile one.
    """
    ceiling = limit_graphemes * 4
    return text if len(text) <= ceiling else text[:ceiling]


def compose_text(post: Dict[str, Any], limit_graphemes: int,
                 limit_bytes: Optional[int] = None,
                 include_link: bool = True) -> Tuple[str, str]:
    """Build the crosspost body. Returns (text, link).

    The headline is used verbatim rather than re-written, because it has
    already been through vetting, the caveat rules and a human approval. A
    second, unreviewed piece of copy generated here would be a way for claims
    to reach the public that never passed any of that - the exact seam the
    24 Aug audit warned about, where one module computes a verdict and another
    quietly does its own thing.
    """
    cover = (post.get("cover") or {})
    headline = _clean(cover.get("headline")).replace("**", "")
    st = post.get("study") or {}
    journal = _clean(st.get("journal"))
    link = study_link(post) if include_link else ""

    # A preprint must say so. The public methodology page promises "If it is a
    # preprint, the post says so on the cover slide. Always." - and a crosspost
    # IS the post, on a platform whose whole audience is researchers. The slide
    # carries a PREPRINT badge that no amount of text here reproduces, so the
    # disclosure has to be re-stated in words. Read from the vetted record
    # rather than inferred from the journal name, because the journal string is
    # attacker-controlled and inference is exactly the seam that keeps biting.
    prefix = "Preprint, not yet peer reviewed. " if st.get("is_preprint") else ""

    body = f"{prefix}{headline}"
    if journal and _grapheme_len(f"{body} ({journal})") + 2 <= limit_graphemes:
        body = f"{body} ({journal})"
    # Cap the prose BEFORE the link is attached. Byte-trimming the joined
    # string cuts its tail, and the tail is the URL.
    body = _enforce_bytes(_hard_cap(body, limit_graphemes), limit_bytes)

    if link:
        candidate = f"{body}\n\n{link}"
        if (_grapheme_len(candidate) <= limit_graphemes
                and len(candidate) <= limit_graphemes * 4
                and (limit_bytes is None
                     or len(candidate.encode("utf-8")) <= limit_bytes)):
            return candidate, link
        # Trim the prose, never the link: a truncated URL is a broken URL, and
        # the link is the entire point of the crosspost. The room calculation
        # is done in the same units as the check that follows it.
        room = limit_graphemes - _grapheme_len(link) - 4
        trimmed = _truncate(_hard_cap(body, max(0, room)), max(0, room))
        if limit_bytes is not None:
            budget = limit_bytes - len(f"\n\n{link}".encode("utf-8"))
            trimmed = _enforce_bytes(trimmed, max(0, budget))
        return (f"{trimmed}\n\n{link}" if trimmed else link), link

    return _enforce_bytes(_truncate(body, limit_graphemes), limit_bytes), link


def _truncate(text: str, limit: int) -> str:
    if _grapheme_len(text) <= limit:
        return text
    out = []
    for ch in text:
        out.append(ch)
        if _grapheme_len("".join(out)) > limit - 1:
            out.pop()
            break
    return "".join(out).rstrip() + "…"


def _enforce_bytes(text: str, limit_bytes: Optional[int]) -> str:
    if limit_bytes is None or len(text.encode("utf-8")) <= limit_bytes:
        return text
    b = text.encode("utf-8")[:limit_bytes]
    # Never split a multi-byte character - decode with errors="ignore" drops a
    # trailing partial sequence rather than emitting a replacement character.
    return b.decode("utf-8", errors="ignore")


# ---------------------------------------------------------------------------
# Bluesky
# ---------------------------------------------------------------------------
def link_facets(text: str, url: str) -> List[Dict[str, Any]]:
    """Facets making `url` clickable inside `text`.

    THE OFFSETS ARE UTF-8 BYTE OFFSETS, NOT CHARACTER OFFSETS. The lexicon
    says so explicitly, and getting it wrong is the single most common bug in
    this integration - because it fails SILENTLY. The post publishes, and the
    link is simply not clickable, or highlights the wrong span. There is no
    validation error to notice.

    So: encode the whole string first and search the bytes. Any multi-byte
    character anywhere before the URL - an em dash, an accented name, a curly
    quote, all of which this account's headlines contain routinely - shifts
    every subsequent offset. `text.index(url)` would be wrong by exactly that
    amount.

    byteStart is inclusive, byteEnd exclusive, zero-indexed.
    """
    if not url:
        return []
    raw = text.encode("utf-8")
    needle = url.encode("utf-8")
    # rfind, not find. The link is APPENDED last, while the journal name -
    # untrusted third-party text - sits earlier in the body. A journal called
    # "https://doi.org/10.1038/real.evil.example/login" makes find() match
    # inside the journal string instead, so the underlined span covers a
    # lookalike URL and the real trailing link is left un-clickable. Searching
    # backwards pins the facet to the copy this function actually appended.
    start = raw.rfind(needle)
    if start < 0:
        return []
    return [{
        "index": {"byteStart": start, "byteEnd": start + len(needle)},
        "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}],
    }]


def _safe_pds(raw: str) -> str:
    """The PDS to send the app password to, or the default.

    This is the only place in the codebase where a SECRET'S DESTINATION HOST
    comes from an environment variable - everything else talks to a compiled-in
    host. An arbitrary value here silently redirects the Bluesky app password
    to someone else's server. Requires repo-admin access to set, so this is
    hardening rather than a hole, but there is no reason to leave it open.
    """
    raw = (raw or "").strip().rstrip("/")
    if not raw:
        return BSKY_DEFAULT_PDS
    p = urlsplit(raw)
    host = (p.hostname or "").lower()
    if p.scheme == "https" and (host == "bsky.social"
                                or host.endswith(".bsky.network")
                                or host.endswith(".bsky.social")):
        return raw
    print(f"  ! ignoring BLUESKY_PDS={raw!r} - not an https bsky host; "
          f"using {BSKY_DEFAULT_PDS}")
    return BSKY_DEFAULT_PDS


def cover_headline(post: Dict[str, Any]) -> str:
    return _clean((post.get("cover") or {}).get("headline")).replace("**", "")


def bluesky_config() -> Dict[str, str]:
    return {
        "handle": _opt_setting("BLUESKY_HANDLE", ""),
        "password": _opt_setting("BLUESKY_APP_PASSWORD", ""),
        "pds": _safe_pds(_opt_setting("BLUESKY_PDS", BSKY_DEFAULT_PDS)),
    }


def bluesky_session(cfg: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    cfg = cfg or bluesky_config()
    r = requests.post(
        f"{cfg['pds'].rstrip('/')}/xrpc/com.atproto.server.createSession",
        json={"identifier": cfg["handle"], "password": cfg["password"]},
        timeout=TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"Bluesky login failed ({r.status_code}): {r.text[:300]}")
    return r.json()


def post_to_bluesky(post: Dict[str, Any], live: bool = False,
                    cfg: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    cfg = cfg or bluesky_config()
    if not cfg["handle"] or not cfg["password"]:
        return {"platform": "bluesky", "ok": False, "skipped": True,
                "reason": "BLUESKY_HANDLE / BLUESKY_APP_PASSWORD not set"}

    text, link = compose_text(post, BSKY_MAX_GRAPHEMES, BSKY_MAX_BYTES)
    facets = link_facets(text, link)
    record: Dict[str, Any] = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "langs": ["en"],
    }
    if facets:
        record["facets"] = facets
    if link:
        st = post.get("study") or {}
        # Bluesky does NOT unfurl links server-side. With no embed the link is
        # bare text with no preview card, so this is what makes a crosspost
        # look like a shared article instead of a pasted URL. title and
        # description are both required by the lexicon.
        # The card title is built from the APPROVED cover copy, not the raw
        # study title. The headline went through vetting, the caveat rules and
        # a human approval; the title went through none of that, and at 300
        # characters it is a generous place to put unreviewed text under the
        # account's own name on a platform full of researchers.
        #
        # The description does NOT fall back to "Peer-reviewed study". That
        # sentence asserted peer review from the mere ABSENCE of a journal
        # name, while post["study"]["is_preprint"] sat unread in the same
        # dict - an invented claim of exactly the kind this account exists to
        # avoid making.
        approved_title = (_clean(cover_headline(post))
                          or _clean(st.get("title")) or "Study")
        if st.get("is_preprint"):
            desc = f"Preprint · {_clean(st.get('server')) or 'preprint server'}"
        else:
            desc = _clean(st.get("journal")) or "Research paper"
        record["embed"] = {
            "$type": "app.bsky.embed.external",
            "external": {
                "uri": link,
                "title": _truncate(_hard_cap(approved_title, 60), 60),
                "description": _truncate(_hard_cap(desc, 60), 60),
            },
        }

    plan = {"platform": "bluesky", "text": text, "chars": _grapheme_len(text),
            "bytes": len(text.encode("utf-8")), "facets": facets,
            "has_embed": "embed" in record}
    if not live:
        plan.update({"ok": True, "mode": "DRY RUN"})
        return plan

    try:
        sess = bluesky_session(cfg)
        r = requests.post(
            f"{cfg['pds'].rstrip('/')}/xrpc/com.atproto.repo.createRecord",
            headers={"Authorization": f"Bearer {sess['accessJwt']}"},
            json={"repo": sess["did"], "collection": "app.bsky.feed.post",
                  "record": record},
            timeout=TIMEOUT)
        if r.status_code != 200:
            return {**plan, "ok": False,
                    "error": f"createRecord {r.status_code}: {r.text[:300]}"}
        j = r.json()
        return {**plan, "ok": True, "mode": "LIVE", "uri": j.get("uri"),
                "cid": j.get("cid")}
    except Exception as e:
        return {**plan, "ok": False, "error": safe_error(e)}


# ---------------------------------------------------------------------------
# Threads
# ---------------------------------------------------------------------------
def _api_error(body: Any) -> str:
    """A bounded, scrubbed description of a Graph API error body.

    The previous version dumped the WHOLE response object whenever it had
    neither "error" nor "id" - an unbounded upstream-body sink feeding straight
    into CI logs. Name the fields that are useful instead.
    """
    err = (body or {}).get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        keep = {k: err.get(k) for k in ("message", "type", "code", "error_subcode",
                                        "fbtrace_id") if err.get(k) is not None}
        return redact(json.dumps(keep))[:300]
    return redact(f"unexpected response shape: {type(body).__name__}")[:200]


def threads_config() -> Dict[str, str]:
    return {
        "token": _opt_setting("THREADS_ACCESS_TOKEN", ""),
        "user_id": _opt_setting("THREADS_USER_ID", ""),
    }


def post_to_threads(post: Dict[str, Any], live: bool = False,
                    cfg: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Publish a TEXT post with a link attachment. Two steps, like Instagram.

    Threads has no container status endpoint, so there is nothing to poll -
    for a TEXT container there is also nothing to transcode. Meta suggests
    waiting ~30 seconds before publishing, but that guidance is written for
    media uploads; blocking half a minute on every text post to satisfy advice
    aimed at video would be cargo-culting. Publish immediately and let the
    caller's failure handling deal with the rare rejection.
    """
    cfg = cfg or threads_config()
    if not cfg["token"] or not cfg["user_id"]:
        return {"platform": "threads", "ok": False, "skipped": True,
                "reason": "THREADS_ACCESS_TOKEN / THREADS_USER_ID not set "
                          "(Threads needs its own Meta app - see docs/RUNBOOK.md)"}

    # The link goes in link_attachment, which renders the preview card, so it
    # does not need to also sit in the text and eat characters.
    #
    # It is therefore fetched SEPARATELY rather than taken from compose_text's
    # second return value: include_link=False means "keep the URL out of the
    # body", and compose_text signals that by returning "" for the link. Using
    # that value here sent every Threads post with no link_attachment and no
    # URL in the text - a crosspost with the entire point removed, which
    # nothing would have failed on and nobody would have noticed except by
    # looking at the account.
    # A byte limit as well as a character one. Meta documents that emoji count
    # as their UTF-8 byte length and never states a numeric ceiling at all, so
    # bytes are the only unit that is safe in every case. 500 bytes is
    # conservative for accented text and always legal.
    text, _ = compose_text(post, THREADS_MAX_CHARS,
                           limit_bytes=THREADS_MAX_CHARS, include_link=False)
    link = study_link(post)
    plan = {"platform": "threads", "text": text, "chars": _grapheme_len(text),
            "link": link}
    if not live:
        plan.update({"ok": True, "mode": "DRY RUN"})
        return plan

    try:
        params = {"media_type": "TEXT", "text": text, "access_token": cfg["token"]}
        if link:
            params["link_attachment"] = link
        r = requests.post(f"{THREADS_BASE}/{cfg['user_id']}/threads",
                          data=params, timeout=TIMEOUT)
        j = r.json()
        if "error" in j or "id" not in j:
            return {**plan, "ok": False,
                    "error": f"container: {_api_error(j)}"}
        container_id = j["id"]

        r2 = requests.post(f"{THREADS_BASE}/{cfg['user_id']}/threads_publish",
                           data={"creation_id": container_id,
                                 "access_token": cfg["token"]}, timeout=TIMEOUT)
        j2 = r2.json()
        if "error" in j2 or "id" not in j2:
            return {**plan, "ok": False, "container_id": container_id,
                    "error": f"publish: {_api_error(j2)}"}
        return {**plan, "ok": True, "mode": "LIVE", "container_id": container_id,
                "media_id": j2["id"]}
    except Exception as e:
        return {**plan, "ok": False, "error": safe_error(e)}


# ---------------------------------------------------------------------------
def crosspost_all(post: Dict[str, Any], live: bool = False) -> Dict[str, Any]:
    """Crosspost everywhere configured. NEVER raises.

    Called from the publish path immediately after Instagram succeeds. By that
    point a carousel is irreversibly live, so an exception escaping this
    function would fail the workflow AFTER the thing that matters already
    happened - which, before the 24 Aug fixes, was exactly how a post got
    published twice. Nothing here is allowed to be that.
    """
    results = []
    for fn in (post_to_bluesky, post_to_threads):
        try:
            results.append(fn(post, live=live))
        except Exception as e:                                # pragma: no cover
            results.append({"platform": fn.__name__, "ok": False,
                            "error": f"uncaught {safe_error(e)}"})
    return {
        "posted": [r for r in results if r.get("ok") and not r.get("skipped")],
        "skipped": [r for r in results if r.get("skipped")],
        "failed": [r for r in results if not r.get("ok") and not r.get("skipped")],
        "results": results,
    }


def _main() -> None:
    ap = argparse.ArgumentParser(description="Crosspost a published study")
    ap.add_argument("post_json")
    ap.add_argument("--live", action="store_true",
                    help="actually post. Without this it is a dry run.")
    a = ap.parse_args()
    from pathlib import Path
    post = json.loads(Path(a.post_json).read_text())
    print(json.dumps(crosspost_all(post, live=a.live), indent=2))


if __name__ == "__main__":
    _main()
