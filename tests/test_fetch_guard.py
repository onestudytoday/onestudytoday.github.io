"""
The SSRF guard on the Friday Reel's downloader.

friday-reel.yml fetches a still or clip from a URL a human typed, builds a
video from it, and COMMITS that to a public repository. Two things follow:
what gets fetched must be answerable, and it must never be something on the
runner's own network.

The subtle half is redirects. Vetting only the typed URL checks the one hop
that was never the risk - an ordinary public host can 302 to the cloud
metadata address, and curl's --proto-redir pins the scheme while saying
nothing about where it lands.

    python -m pytest tests/test_fetch_guard.py -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import fetch_guard  # noqa: E402


def _resolves_to(monkeypatch, ip):
    monkeypatch.setattr(fetch_guard.socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", (ip, 443))])


@pytest.mark.parametrize("url", [
    "http://example.com/a.jpg",          # not https
    "ftp://example.com/a.jpg",
    "https://user:pw@example.com/a.jpg",  # credentials
    "https:///a.jpg",                     # no host
])
def test_obviously_wrong_urls_are_refused(url, monkeypatch):
    _resolves_to(monkeypatch, "93.184.216.34")
    with pytest.raises(fetch_guard.UnsafeURL):
        fetch_guard.check_host(url)


@pytest.mark.parametrize("ip", [
    "169.254.169.254",   # cloud metadata - the one that matters
    "127.0.0.1",
    "10.0.0.5",
    "192.168.1.1",
    "172.16.0.1",
    "0.0.0.0",
    "::1",
    "fe80::1",
])
def test_internal_addresses_are_refused(ip, monkeypatch):
    _resolves_to(monkeypatch, ip)
    with pytest.raises(fetch_guard.UnsafeURL) as e:
        fetch_guard.check_host("https://totally-normal.example/a.jpg")
    assert "not a public address" in str(e.value)


def test_a_public_address_is_allowed(monkeypatch):
    _resolves_to(monkeypatch, "93.184.216.34")
    assert fetch_guard.check_host("https://example.com/a.jpg") == \
        "https://example.com/a.jpg"


class _Resp:
    def __init__(self, status, location=None):
        self.status_code = status
        self.headers = {"location": location} if location else {}


def test_a_redirect_into_the_metadata_service_is_refused(monkeypatch):
    """THE one this module exists for.

    The typed URL is a perfectly ordinary public host. It 302s to the cloud
    metadata address. Vetting only the first hop - or trusting curl's
    --proto-redir, which pins the scheme and not the destination - fetches it.
    """
    hops = {"https://nice-host.example/pic.jpg":
            _Resp(302, "https://169.254.169.254/latest/meta-data/")}
    monkeypatch.setattr(fetch_guard.requests, "head",
                        lambda url, **k: hops.get(url, _Resp(200)))

    def fake_addr(host, *a, **k):
        ip = "169.254.169.254" if "169.254" in host else "93.184.216.34"
        return [(2, 1, 6, "", (ip, 443))]
    monkeypatch.setattr(fetch_guard.socket, "getaddrinfo", fake_addr)

    with pytest.raises(fetch_guard.UnsafeURL) as e:
        fetch_guard.resolve("https://nice-host.example/pic.jpg")
    assert "169.254.169.254" in str(e.value)


def test_an_ordinary_redirect_chain_resolves(monkeypatch):
    hops = {"https://a.example/x": _Resp(301, "https://b.example/y"),
            "https://b.example/y": _Resp(200)}
    monkeypatch.setattr(fetch_guard.requests, "head",
                        lambda url, **k: hops.get(url, _Resp(200)))
    _resolves_to(monkeypatch, "93.184.216.34")
    assert fetch_guard.resolve("https://a.example/x") == "https://b.example/y"


def test_a_redirect_loop_is_refused(monkeypatch):
    monkeypatch.setattr(fetch_guard.requests, "head",
                        lambda url, **k: _Resp(302, "https://a.example/x"))
    _resolves_to(monkeypatch, "93.184.216.34")
    with pytest.raises(fetch_guard.UnsafeURL):
        fetch_guard.resolve("https://a.example/x")


def test_too_many_redirects_is_refused(monkeypatch):
    n = {"i": 0}

    def head(url, **k):
        n["i"] += 1
        return _Resp(302, f"https://h{n['i']}.example/x")
    monkeypatch.setattr(fetch_guard.requests, "head", head)
    _resolves_to(monkeypatch, "93.184.216.34")
    with pytest.raises(fetch_guard.UnsafeURL):
        fetch_guard.resolve("https://h0.example/x")


def test_a_newline_cannot_survive_normalisation(monkeypatch):
    """urlparse silently strips newlines, so the RAW input must never be what
    gets written out - it would have injected an extra $GITHUB_OUTPUT line."""
    _resolves_to(monkeypatch, "93.184.216.34")
    out = fetch_guard.check_host("https://example.com/a.jpg\npost_id=evil")
    assert "\n" not in out
