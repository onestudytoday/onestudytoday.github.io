"""
Resolve a URL's redirect chain, refusing to leave the public internet.

WHY THIS IS NOT JUST `curl -L`
==============================
friday-reel.yml downloads a still or a clip from a URL a human typed, builds a
video out of it, and COMMITS the result to a public repository. So "what did
we actually fetch" has to be answerable, and the answer must never be "something
on the runner's own network".

curl's --proto-redir pins the SCHEME across redirects. It says nothing about
the destination: a perfectly ordinary https host can 302 to
https://169.254.169.254/ (the cloud metadata address) or to an https service
inside the runner's network, and curl will follow it having broken no rule.
Vetting only the URL that was typed therefore checks the one hop that was
never the risk.

So each hop is resolved here, one at a time, with redirects disabled, and every
hop's ADDRESS is checked before the next request is made. Only the final URL
is handed to curl, which is then told not to follow anything.

    python3 src/fetch_guard.py <url>      # prints the vetted final URL
"""

from __future__ import annotations

import ipaddress
import socket
import sys
import urllib.parse

import requests

MAX_HOPS = 4
TIMEOUT = 20
UA = "onestudytoday/1.0"


class UnsafeURL(RuntimeError):
    pass


def check_host(url: str) -> str:
    p = urllib.parse.urlparse(url)
    if p.scheme != "https" or not p.netloc:
        raise UnsafeURL(f"Refusing {url!r}: only https:// URLs may be fetched.")
    if "@" in p.netloc:
        raise UnsafeURL(f"Refusing {url!r}: credentials embedded in the URL.")
    host = p.hostname or ""
    try:
        infos = socket.getaddrinfo(host, p.port or 443, proto=socket.IPPROTO_TCP)
    except Exception as e:
        raise UnsafeURL(f"Refusing {url!r}: {host} does not resolve ({e}).")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        # Every non-public category, not just loopback. Link-local covers the
        # cloud metadata endpoint; reserved and private cover everything an
        # internal service is likely to sit on.
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise UnsafeURL(
                f"Refusing {url!r}: {host} resolves to {ip}, which is not a "
                f"public address. Whatever is fetched here gets committed to "
                f"a public repository.")
    return urllib.parse.urlunparse(p)


def resolve(url: str, max_hops: int = MAX_HOPS) -> str:
    """Follow redirects by hand, vetting every hop. Returns the final URL."""
    seen = set()
    for _ in range(max_hops):
        url = check_host(url)
        if url in seen:
            raise UnsafeURL("Refusing: redirect loop.")
        seen.add(url)
        try:
            r = requests.head(url, allow_redirects=False, timeout=TIMEOUT,
                              headers={"User-Agent": UA})
        except Exception as e:
            raise UnsafeURL(f"Refusing {url!r}: {type(e).__name__}: {e}")
        if r.status_code not in (301, 302, 303, 307, 308):
            return url
        nxt = r.headers.get("location")
        if not nxt:
            return url
        url = urllib.parse.urljoin(url, nxt)
    raise UnsafeURL(f"Refusing: more than {max_hops} redirects.")


def _main() -> None:                                   # pragma: no cover
    if len(sys.argv) != 2:
        raise SystemExit("usage: fetch_guard.py <https-url>")
    try:
        sys.stdout.write(resolve(sys.argv[1]))
    except UnsafeURL as e:
        raise SystemExit(str(e))


if __name__ == "__main__":                             # pragma: no cover
    _main()
