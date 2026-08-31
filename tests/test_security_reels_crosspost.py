"""
Regression tests for the 27 Aug 2026 security review of the Reels and
crossposting work.

Three independent adversarial reviewers went over the new code (credentials,
untrusted input, duplicate-publish). Every finding they CONFIRMED by executing
a payload is pinned here with that same payload. A test that passes against
both the fixed and the broken implementation is worth nothing, so each one
below was checked to fail when its fix is reverted.

    python -m pytest tests/ -q
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import crosspost                     # noqa: E402
import linkinbio                     # noqa: E402
import pipeline                      # noqa: E402
import publish as publish_mod        # noqa: E402
import reel                          # noqa: E402
import secrets_guard                 # noqa: E402


def _post(**study):
    st = {"doi": "10.1038/s41586-026-00001-2", "journal": "Nature",
          "title": "A study title", "url": "https://example.test/paper",
          "is_preprint": False, "pub_date": "2026-08-01"}
    st.update(study)
    return {"id": "2026-08-28-wildcard-abc", "status": "approved",
            "niche": "wildcard", "cover": {"headline": "A real finding."},
            "study": st}


# ---------------------------------------------------------------------------
# Credential leakage
# ---------------------------------------------------------------------------
def test_access_token_is_scrubbed_from_a_network_exception(monkeypatch):
    """The confirmed high-severity leak.

    The Graph API takes access_token as a QUERY PARAMETER, so requests puts the
    whole URL into the exception it raises. That text was printed, tee'd into
    publish.log, and the tail of that file was posted into a GitHub issue
    comment on a PUBLIC repo. Actions masking does not cover it: masking
    applies to the log stream, not to files a process writes and not to REST
    payloads.
    """
    token = "EAAOsecretLongLivedAccessTokenValue1234567890"
    monkeypatch.setenv("IG_ACCESS_TOKEN", token)

    class FakeSettings:
        graph = "https://graph.facebook.com/v23.0"
        ig_access_token = token
        ig_business_account_id = "17841400000000000"

    def raise_with_url(*a, **kw):
        raise ConnectionError(
            "HTTPSConnectionPool(host='graph.facebook.com', port=443): "
            "Max retries exceeded with url: /v23.0/17999/"
            f"?fields=status_code&access_token={token}")

    monkeypatch.setattr(publish_mod, "settings", lambda: FakeSettings())
    monkeypatch.setattr(publish_mod.requests, "get", raise_with_url)

    with pytest.raises(publish_mod.PublishError) as e:
        publish_mod.wait_ready("17999", max_polls=1, poll_seconds=0)
    assert token not in str(e.value), "ACCESS TOKEN LEAKED INTO AN ERROR MESSAGE"
    assert secrets_guard.PLACEHOLDER in str(e.value)


def test_quota_check_scrubs_its_exceptions(monkeypatch):
    token = "EAAOanotherSecretTokenValue0987654321"
    monkeypatch.setenv("IG_ACCESS_TOKEN", token)

    class FakeSettings:
        graph = "https://graph.facebook.com/v23.0"
        ig_access_token = token
        ig_business_account_id = "1784140"

    monkeypatch.setattr(publish_mod, "settings", lambda: FakeSettings())
    monkeypatch.setattr(publish_mod.requests, "get",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            ConnectionError(f"failed url ...access_token={token}")))
    with pytest.raises(publish_mod.PublishError) as e:
        publish_mod.check_quota()
    assert token not in str(e.value)


def test_redaction_covers_credentials_it_was_never_told_about(monkeypatch):
    """The old auth._redact knew three credentials by name. The new ones are
    not on Settings at all, so a name-based list could never have covered
    them."""
    env = {"BLUESKY_APP_PASSWORD": "abcd-efgh-ijkl-mnop",
           "THREADS_ACCESS_TOKEN": "THREADSsecretTokenValue123",
           "ANTHROPIC_API_KEY": "sk-ant-verysecretkeyvalue",
           "HANDLE": "@onestudytoday"}
    text = ("failed: abcd-efgh-ijkl-mnop and THREADSsecretTokenValue123 and "
            "sk-ant-verysecretkeyvalue for @onestudytoday")
    out = secrets_guard.redact(text, env=env)
    for secret in ("abcd-efgh-ijkl-mnop", "THREADSsecretTokenValue123",
                   "sk-ant-verysecretkeyvalue"):
        assert secret not in out
    assert "@onestudytoday" in out, "a public handle must not be redacted"


def test_short_values_are_not_treated_as_secrets():
    assert secrets_guard.redact("v23.0 is the version",
                                env={"GRAPH_VERSION_KEY": "v23"}) == \
        "v23.0 is the version"


def test_publish_log_is_not_pasted_into_the_issue_comment():
    wf = Path(__file__).resolve().parents[1] / \
        ".github/workflows/scheduled-publish.yml"
    body = wf.read_text()
    assert "log.slice(-2000)" not in body, (
        "the raw publish log must not be posted into a GitHub issue comment - "
        "REST payloads are not covered by Actions secret masking, and the repo "
        "is public")


# ---------------------------------------------------------------------------
# Length limits — the grapheme undercount
# ---------------------------------------------------------------------------
def test_combining_marks_cannot_bypass_the_bluesky_limits():
    """4000 combining acute accents measure as ZERO graphemes.

    `journal` is third-party text, so this input is attacker-chosen.
    """
    post = _post(journal="́" * 4000)
    text, link = crosspost.compose_text(post, crosspost.BSKY_MAX_GRAPHEMES,
                                        crosspost.BSKY_MAX_BYTES)
    assert len(text.encode("utf-8")) <= crosspost.BSKY_MAX_BYTES
    assert len(text) <= crosspost.BSKY_MAX_GRAPHEMES * 4
    assert link and link in text, (
        "the byte trim cut the END of the string, which is where the link is - "
        "silently deleting the one thing the crosspost exists to carry")


def test_combining_marks_cannot_bypass_the_threads_limit():
    """The Threads path passed no byte limit at all: 4062 characters went out
    against a 500 limit, reported in the plan as 62."""
    post = _post(journal="́" * 4000)
    text, _ = crosspost.compose_text(post, crosspost.THREADS_MAX_CHARS,
                                     limit_bytes=crosspost.THREADS_MAX_CHARS,
                                     include_link=False)
    assert len(text.encode("utf-8")) <= crosspost.THREADS_MAX_CHARS
    assert len(text) <= crosspost.THREADS_MAX_CHARS * 4


def test_the_reported_plan_matches_what_would_actually_be_sent(monkeypatch):
    """A dry run that under-reports its own length is worse than no dry run."""
    monkeypatch.setenv("BLUESKY_HANDLE", "h.bsky.social")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "pw-app-password")
    r = crosspost.post_to_bluesky(_post(journal="́" * 4000), live=False)
    assert r["bytes"] == len(r["text"].encode("utf-8"))
    assert r["bytes"] <= crosspost.BSKY_MAX_BYTES


# ---------------------------------------------------------------------------
# Editorial integrity — the account's actual promise
# ---------------------------------------------------------------------------
def test_a_preprint_says_so_in_the_crosspost():
    """The public methodology page promises: "If it is a preprint, the post
    says so on the cover slide. Always."

    The disclosure used to ride only on the ({journal}) parenthetical, which is
    dropped whenever it does not fit - and journal length is attacker-chosen.
    """
    post = _post(is_preprint=True, journal="b" * 400, server="bioRxiv",
                 doi="10.1101/2026.01.01.000001")
    text, _ = crosspost.compose_text(post, crosspost.BSKY_MAX_GRAPHEMES,
                                     crosspost.BSKY_MAX_BYTES)
    assert "reprint" in text, "a preprint went out with no preprint disclosure"


def test_never_claims_peer_review_for_a_preprint(monkeypatch):
    """The description defaulted to "Peer-reviewed study" from the mere ABSENCE
    of a journal name, while is_preprint sat unread in the same dict."""
    monkeypatch.setenv("BLUESKY_HANDLE", "h.bsky.social")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "pw-app-password")
    captured = {}

    class Sess:
        status_code = 200
        json = staticmethod(lambda: {"accessJwt": "j", "did": "did:plc:a"})

    class Ok:
        status_code = 200
        json = staticmethod(lambda: {"uri": "at://x", "cid": "b"})

    def fake_post(url, **kw):
        if "createSession" in url:
            return Sess()
        captured["rec"] = kw["json"]["record"]
        return Ok()

    monkeypatch.setattr(crosspost.requests, "post", fake_post)
    crosspost.post_to_bluesky(
        _post(is_preprint=True, journal="", server="bioRxiv",
              doi="10.1101/2026.01.01.000001"), live=True)
    desc = captured["rec"]["embed"]["external"]["description"]
    assert "eer-reviewed" not in desc, f"claimed peer review for a preprint: {desc}"
    assert "reprint" in desc


def test_card_title_comes_from_approved_copy_not_the_raw_study_title(monkeypatch):
    """The raw title went through no vetting and no human approval."""
    monkeypatch.setenv("BLUESKY_HANDLE", "h.bsky.social")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "pw-app-password")
    captured = {}

    class Sess:
        status_code = 200
        json = staticmethod(lambda: {"accessJwt": "j", "did": "did:plc:a"})

    class Ok:
        status_code = 200
        json = staticmethod(lambda: {"uri": "at://x", "cid": "b"})

    def fake_post(url, **kw):
        if "createSession" in url:
            return Sess()
        captured["rec"] = kw["json"]["record"]
        return Ok()

    monkeypatch.setattr(crosspost.requests, "post", fake_post)
    hostile = "BREAKING: @onestudytoday says buy TOKEN at t.co/xyz - official"
    crosspost.post_to_bluesky(_post(title=hostile), live=True)
    assert hostile not in captured["rec"]["embed"]["external"]["title"]


# ---------------------------------------------------------------------------
# URL scheme validation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [
    "javascript:alert(1)", "JaVaScript:alert(1)", " javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>", "//evil.example/x",
    "file:///etc/passwd",
])
def test_dangerous_url_schemes_never_reach_a_link(bad):
    assert crosspost.study_link(_post(doi="", url=bad)) == ""


def test_a_malformed_doi_is_not_interpolated_into_a_link():
    assert crosspost.study_link(_post(doi="not-a-doi", url="")) == ""
    assert crosspost.study_link(
        _post(doi="10.1038/valid", url="")) == "https://doi.org/10.1038/valid"


# ---------------------------------------------------------------------------
# Facet integrity
# ---------------------------------------------------------------------------
def test_a_crafted_journal_cannot_steal_the_link_facet():
    """find() matched inside the untrusted journal, which sits BEFORE the link.

    The rendered post then underlined a lookalike URL and left the real link
    un-clickable.
    """
    real = "10.1038/s41586-026-00001-x"
    post = _post(doi=real,
                 journal=f"https://doi.org/{real}.evil.example/login")
    text, link = crosspost.compose_text(post, crosspost.BSKY_MAX_GRAPHEMES,
                                        crosspost.BSKY_MAX_BYTES)
    facets = crosspost.link_facets(text, link)
    assert facets
    idx = facets[0]["index"]
    raw = text.encode("utf-8")
    assert raw[idx["byteStart"]:idx["byteEnd"]].decode("utf-8") == link
    after = raw[idx["byteEnd"]:].decode("utf-8")
    assert not after.startswith(".evil"), "facet landed on the lookalike URL"


# ---------------------------------------------------------------------------
# Host allow-list
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["https://evil.example", "http://bsky.social",
                                 "ftp://bsky.social", "https://bsky.social.evil.tld"])
def test_app_password_is_never_sent_to_an_arbitrary_host(bad):
    assert crosspost._safe_pds(bad) == crosspost.BSKY_DEFAULT_PDS


def test_a_legitimate_pds_is_still_allowed():
    assert crosspost._safe_pds("https://bsky.social") == "https://bsky.social"


# ---------------------------------------------------------------------------
# Path traversal
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["../../../../tmp/pwn", "a/b", "..", "",
                                 "x\\y", "a b"])
def test_post_id_cannot_escape_its_directory(bad):
    with pytest.raises(reel.ReelError):
        reel.check_post_id(bad)


def test_a_normal_post_id_is_accepted():
    assert reel.check_post_id("2026-08-27-wildcard-abc12345")


# ---------------------------------------------------------------------------
# Duplicate publishing
# ---------------------------------------------------------------------------
def test_a_live_publish_with_no_media_id_raises_instead_of_falling_through(
        tmp_path, monkeypatch):
    """A 200 with an unrecognised body means Instagram MAY have accepted it.

    Falling through wrote no published record and no ledger entry, so the next
    15-minute poll republished it - the exact 24 Aug bug through a new door.
    """
    docs = tmp_path / "docs"
    post = _post()
    img = docs / "img" / post["id"]
    img.mkdir(parents=True)
    (img / "01.jpg").write_bytes(b"x")
    (img / "02.jpg").write_bytes(b"x")

    monkeypatch.setenv("REEL_NICHES", "off")
    monkeypatch.setattr(pipeline, "DOCS", docs)
    monkeypatch.setattr(pipeline, "OUT", tmp_path / "out")
    monkeypatch.setattr(publish_mod, "public_urls",
                        lambda j, pid: [f"https://x.test/{p.name}" for p in j])
    monkeypatch.setattr(publish_mod, "publish",
                        lambda p, urls, live: {"mode": "LIVE"})   # no media_id

    f = tmp_path / "q.json"
    f.write_text(json.dumps(post))
    with pytest.raises(publish_mod.PublishError) as e:
        pipeline._publish_one(f, post, live=True)
    assert "MAY ALREADY BE LIVE" in str(e.value)
    assert f.exists(), "the queue file must survive for manual reconciliation"


def test_a_reel_publishes_without_needing_carousel_jpegs(tmp_path, monkeypatch):
    """run() creates docs/img/<id>/ when it writes reel.mp4, while the JPEGs
    are staged by a separate later step that can be skipped. The Reel branch
    used to sit AFTER the no-slides return, so that combination was a silent
    permanent stall retried every 15 minutes, green."""
    docs = tmp_path / "docs"
    post = _post()
    img = docs / "img" / post["id"]
    img.mkdir(parents=True)
    (img / "reel.mp4").write_bytes(b"fake-mp4")      # no .jpg files at all

    class FakeSettings:
        public_image_base = "https://onestudytoday.github.io/img"

    monkeypatch.setenv("REEL_NICHES", "wildcard")
    monkeypatch.setattr(pipeline, "DOCS", docs)
    monkeypatch.setattr(pipeline, "OUT", tmp_path / "out")
    monkeypatch.setattr(pipeline, "settings", lambda: FakeSettings())
    monkeypatch.setattr(publish_mod, "publish_reel",
                        lambda p, url, live, **kw: {"mode": "DRY RUN",
                                                    "kind": "REELS"})
    f = tmp_path / "q.json"
    f.write_text(json.dumps(post))
    res = pipeline._publish_one(f, post, live=False)
    assert res["kind"] == "REELS"


# ---------------------------------------------------------------------------
# The public page must survive bad data
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad_date", ["", "not-a-date", "2026-13-45", None])
def test_one_malformed_date_does_not_take_down_the_whole_site(tmp_path,
                                                              monkeypatch,
                                                              bad_date):
    """_week_key re-did _iso_date's parse without its guard.

    One published post with a bad date raised ValueError and permanently broke
    regeneration of index.html, sitemap.xml and robots.txt - and "the
    link-in-bio rebuild fails" is one of the three documented events that can
    discard a publish record.
    """
    pub = tmp_path / "published"
    pub.mkdir()
    good = {"id": "good", "niche": "nature",
            "study": {"title": "Good", "journal": "Nature",
                      "pub_date": "2026-08-20", "pub_date_display": "Aug 20, 2026",
                      "url": "https://example.test/g"}}
    bad = {"id": "bad", "niche": "nature",
           "study": {"title": "Bad", "journal": "Nature",
                     "pub_date": bad_date, "pub_date_display": "?",
                     "url": "https://example.test/b"}}
    (pub / "good.json").write_text(json.dumps(good))
    (pub / "bad.json").write_text(json.dumps(bad))

    monkeypatch.setattr(linkinbio, "PUBLISHED", pub)
    monkeypatch.setattr(linkinbio, "DOCS", tmp_path / "docs")
    (tmp_path / "docs").mkdir()

    html = linkinbio.build()                      # must not raise
    out = Path(html).read_text() if Path(str(html)).exists() else str(html)
    assert "Good" in out, "the well-formed post must still be listed"
