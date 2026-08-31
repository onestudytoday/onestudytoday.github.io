"""
Tests for Bluesky and Threads crossposting.

Two things dominate here.

**Byte offsets.** Bluesky link facets are indexed in UTF-8 BYTES, not
characters. Getting this wrong fails SILENTLY - the post publishes and the
link simply is not clickable. There is no error to notice, so a test is the
only thing standing between a working link and a broken one. The cases below
deliberately use the exact characters this account's headlines actually
contain: em dashes, curly apostrophes, accented author names.

**Failing soft.** By the time crossposting runs, a carousel is already
irreversibly live on Instagram. Anything that raises here would abort the run
AFTER the irreversible act, which is precisely the shape that republished the
same post every 15 minutes before the 24 Aug fixes.

    python -m pytest tests/ -q
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import crosspost                     # noqa: E402


def _post(headline="A finding that matters", journal="Nature",
          doi="10.1038/s41586-026-00001-2", title="A study title"):
    return {"id": "2026-08-28-wildcard-abc", "status": "approved",
            "cover": {"headline": headline, "kicker": journal},
            "study": {"doi": doi, "journal": journal, "title": title,
                      "url": "https://example.test/paper"}}


# ---------------------------------------------------------------------------
# Bluesky facets — UTF-8 byte offsets
# ---------------------------------------------------------------------------
URL = "https://doi.org/10.1038/x"


@pytest.mark.parametrize("prefix,label", [
    ("Plain ascii text. ", "ascii"),
    ("Sleep — memory — attention. ", "em dashes"),
    ("The “effect” didn’t replicate. ", "curly quotes"),
    ("Study by Muñoz, Grüber and Škoda. ", "accented names"),
    ("Big result \U0001F9EC ahead. ", "emoji"),
    ("研究结果非常有趣。 ", "CJK"),
    ("Mixed — “quotes” \U0001F9EC Muñoz 研究 ", "everything at once"),
])
def test_facet_byte_offsets_resolve_to_the_url(prefix, label):
    text = f"{prefix}{URL}"
    facets = crosspost.link_facets(text, URL)
    assert facets, f"no facet produced for {label}"
    idx = facets[0]["index"]
    raw = text.encode("utf-8")
    assert raw[idx["byteStart"]:idx["byteEnd"]].decode("utf-8") == URL, (
        f"facet misaligned for {label} - the link would not be clickable")


def test_byte_offsets_actually_differ_from_character_offsets():
    """Proves these tests would catch the naive implementation.

    A test that passes for both the right and the wrong implementation is
    worth nothing - the 24 Aug audit found two of those. If character and byte
    offsets were the same here, the parametrised test above would be vacuous.
    """
    text = f"Sleep — “memory” — Muñoz 研究 {URL}"
    byte_start = crosspost.link_facets(text, URL)[0]["index"]["byteStart"]
    char_start = text.index(URL)
    assert byte_start != char_start, "case is too weak to catch the real bug"
    assert byte_start > char_start


def test_no_facet_when_the_url_is_absent():
    assert crosspost.link_facets("no link here", URL) == []
    assert crosspost.link_facets("text", "") == []


def test_facet_uses_the_complete_url():
    text = f"See {URL}"
    f = crosspost.link_facets(text, URL)[0]
    assert f["features"][0]["uri"] == URL
    assert f["features"][0]["$type"] == "app.bsky.richtext.facet#link"


# ---------------------------------------------------------------------------
# Length limits
# ---------------------------------------------------------------------------
def test_bluesky_text_respects_both_grapheme_and_byte_limits():
    post = _post(headline="A very long headline. " * 40)
    text, _ = crosspost.compose_text(post, crosspost.BSKY_MAX_GRAPHEMES,
                                     crosspost.BSKY_MAX_BYTES)
    assert crosspost._grapheme_len(text) <= crosspost.BSKY_MAX_GRAPHEMES
    assert len(text.encode("utf-8")) <= crosspost.BSKY_MAX_BYTES


def test_emoji_heavy_text_respects_the_byte_limit():
    post = _post(headline="\U0001F9EC" * 400)
    text, _ = crosspost.compose_text(post, crosspost.BSKY_MAX_GRAPHEMES,
                                     crosspost.BSKY_MAX_BYTES)
    assert len(text.encode("utf-8")) <= crosspost.BSKY_MAX_BYTES
    assert crosspost._grapheme_len(text) <= crosspost.BSKY_MAX_GRAPHEMES


def test_truncation_never_splits_a_multibyte_character():
    post = _post(headline="研究" * 300)
    text, _ = crosspost.compose_text(post, crosspost.BSKY_MAX_GRAPHEMES,
                                     crosspost.BSKY_MAX_BYTES)
    text.encode("utf-8").decode("utf-8")   # raises if a char was cut in half


def test_the_link_is_never_truncated():
    """A truncated URL is a broken URL, and the link is the point."""
    post = _post(headline="Long headline. " * 60)
    text, link = crosspost.compose_text(post, crosspost.BSKY_MAX_GRAPHEMES,
                                        crosspost.BSKY_MAX_BYTES)
    assert link in text, "the link was trimmed away or mangled"
    facets = crosspost.link_facets(text, link)
    idx = facets[0]["index"]
    assert text.encode()[idx["byteStart"]:idx["byteEnd"]].decode() == link


def test_threads_text_respects_its_own_limit():
    post = _post(headline="Long headline. " * 60)
    text, _ = crosspost.compose_text(post, crosspost.THREADS_MAX_CHARS,
                                     include_link=False)
    assert crosspost._grapheme_len(text) <= crosspost.THREADS_MAX_CHARS


def test_doi_is_preferred_over_a_bare_url():
    assert crosspost.study_link(_post()) == "https://doi.org/10.1038/s41586-026-00001-2"
    p = _post()
    p["study"]["doi"] = ""
    assert crosspost.study_link(p) == "https://example.test/paper"


def test_copy_comes_from_the_approved_headline_not_a_new_generation():
    """No unreviewed claim may reach the public.

    The headline has already been through vetting, the caveat rules and a human
    approval. Writing fresh copy here would be a second, unreviewed route to
    publication - the seam the 24 Aug audit was about.
    """
    post = _post(headline="Sleep did **not** improve recall")
    text, _ = crosspost.compose_text(post, 300, 3000)
    assert "Sleep did not improve recall" in text
    assert "**" not in text, "markdown emphasis must be stripped, not shown"


# ---------------------------------------------------------------------------
# Configuration: skipped must not look like success
# ---------------------------------------------------------------------------
def test_bluesky_without_credentials_is_skipped_not_failed(monkeypatch):
    monkeypatch.delenv("BLUESKY_HANDLE", raising=False)
    monkeypatch.delenv("BLUESKY_APP_PASSWORD", raising=False)
    r = crosspost.post_to_bluesky(_post(), live=True)
    assert r["skipped"] is True and r["ok"] is False
    assert "BLUESKY_HANDLE" in r["reason"]


def test_threads_without_credentials_is_skipped_not_failed(monkeypatch):
    monkeypatch.delenv("THREADS_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("THREADS_USER_ID", raising=False)
    r = crosspost.post_to_threads(_post(), live=True)
    assert r["skipped"] is True and r["ok"] is False
    assert "own Meta app" in r["reason"]


def test_unconfigured_platforms_make_no_network_calls(monkeypatch):
    monkeypatch.delenv("BLUESKY_HANDLE", raising=False)
    monkeypatch.delenv("BLUESKY_APP_PASSWORD", raising=False)
    monkeypatch.delenv("THREADS_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("THREADS_USER_ID", raising=False)

    def boom(*a, **kw):
        raise AssertionError("no request may be made with no credentials")

    monkeypatch.setattr(crosspost.requests, "post", boom)
    out = crosspost.crosspost_all(_post(), live=True)
    assert len(out["skipped"]) == 2
    assert out["posted"] == [] and out["failed"] == []


def test_dry_run_makes_no_network_calls(monkeypatch):
    monkeypatch.setenv("BLUESKY_HANDLE", "h.bsky.social")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "abcd-efgh-ijkl-mnop")
    monkeypatch.setenv("THREADS_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("THREADS_USER_ID", "123")

    def boom(*a, **kw):
        raise AssertionError("dry run must not touch the network")

    monkeypatch.setattr(crosspost.requests, "post", boom)
    out = crosspost.crosspost_all(_post(), live=False)
    assert all(r["mode"] == "DRY RUN" for r in out["results"])


# ---------------------------------------------------------------------------
# Failing soft
# ---------------------------------------------------------------------------
def test_crosspost_all_never_raises_even_when_everything_explodes(monkeypatch):
    monkeypatch.setenv("BLUESKY_HANDLE", "h.bsky.social")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "pw")
    monkeypatch.setenv("THREADS_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("THREADS_USER_ID", "123")

    def boom(*a, **kw):
        raise ConnectionError("network is down")

    monkeypatch.setattr(crosspost.requests, "post", boom)
    out = crosspost.crosspost_all(_post(), live=True)     # must not raise
    assert len(out["failed"]) == 2
    assert all("ConnectionError" in r["error"] for r in out["failed"])


def test_a_malformed_post_does_not_raise(monkeypatch):
    monkeypatch.delenv("BLUESKY_HANDLE", raising=False)
    monkeypatch.delenv("THREADS_ACCESS_TOKEN", raising=False)
    for bad in ({}, {"study": None}, {"cover": None, "study": {}}):
        out = crosspost.crosspost_all(bad, live=True)
        assert isinstance(out["results"], list)


def test_http_error_is_reported_not_raised(monkeypatch):
    monkeypatch.setenv("BLUESKY_HANDLE", "h.bsky.social")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "pw")

    class Resp:
        status_code = 401
        text = '{"error":"AuthenticationRequired"}'

    monkeypatch.setattr(crosspost.requests, "post", lambda *a, **kw: Resp())
    r = crosspost.post_to_bluesky(_post(), live=True)
    assert r["ok"] is False and "401" in r["error"]


# ---------------------------------------------------------------------------
# Credentials must not leak into logs or results
# ---------------------------------------------------------------------------
def test_no_credential_appears_in_a_bluesky_result(monkeypatch):
    """Results are printed to the CI log, which is retained and shareable."""
    monkeypatch.setenv("BLUESKY_HANDLE", "h.bsky.social")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "s3cret-app-password")

    class Resp:
        status_code = 500
        text = "upstream boom"

    monkeypatch.setattr(crosspost.requests, "post", lambda *a, **kw: Resp())
    blob = json.dumps(crosspost.post_to_bluesky(_post(), live=True))
    assert "s3cret-app-password" not in blob


def test_no_credential_appears_in_a_threads_result(monkeypatch):
    monkeypatch.setenv("THREADS_ACCESS_TOKEN", "THREADS-SECRET-TOKEN")
    monkeypatch.setenv("THREADS_USER_ID", "123")

    class Resp:
        status_code = 400

        @staticmethod
        def json():
            return {"error": {"message": "bad"}}

    monkeypatch.setattr(crosspost.requests, "post", lambda *a, **kw: Resp())
    blob = json.dumps(crosspost.post_to_threads(_post(), live=True))
    assert "THREADS-SECRET-TOKEN" not in blob


def test_bluesky_record_shape_is_valid_for_the_lexicon(monkeypatch):
    monkeypatch.setenv("BLUESKY_HANDLE", "h.bsky.social")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "pw")
    captured = {}

    class Sess:
        status_code = 200

        @staticmethod
        def json():
            return {"accessJwt": "jwt", "did": "did:plc:abc"}

    class Created:
        status_code = 200

        @staticmethod
        def json():
            return {"uri": "at://x", "cid": "bafy"}

    def fake_post(url, **kw):
        if "createSession" in url:
            return Sess()
        captured["body"] = kw.get("json")
        return Created()

    monkeypatch.setattr(crosspost.requests, "post", fake_post)
    r = crosspost.post_to_bluesky(_post(), live=True)
    assert r["ok"] is True

    rec = captured["body"]["record"]
    assert rec["$type"] == "app.bsky.feed.post"
    assert rec["createdAt"].endswith("Z")
    assert captured["body"]["collection"] == "app.bsky.feed.post"
    assert captured["body"]["repo"] == "did:plc:abc"
    # The embed is what makes a link render as a card - Bluesky does not
    # unfurl links server-side, so without it the post is a bare URL.
    ext = rec["embed"]["external"]
    assert rec["embed"]["$type"] == "app.bsky.embed.external"
    assert ext["uri"] and ext["title"] and ext["description"], \
        "uri, title and description are all required by the lexicon"


def test_threads_sends_the_link_as_an_attachment_and_publishes(monkeypatch):
    monkeypatch.setenv("THREADS_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("THREADS_USER_ID", "999")
    calls = []

    class Resp:
        def __init__(self, payload):
            self.status_code = 200
            self._p = payload

        def json(self):
            return self._p

    def fake_post(url, **kw):
        calls.append((url, kw.get("data", {})))
        return Resp({"id": "container-1"} if url.endswith("/threads")
                    else {"id": "media-1"})

    monkeypatch.setattr(crosspost.requests, "post", fake_post)
    r = crosspost.post_to_threads(_post(), live=True)
    assert r["ok"] is True and r["media_id"] == "media-1"
    assert calls[0][1]["media_type"] == "TEXT"
    assert calls[0][1]["link_attachment"].startswith("https://doi.org/")
    assert calls[1][1]["creation_id"] == "container-1"
    assert calls[1][0].endswith("/threads_publish")
