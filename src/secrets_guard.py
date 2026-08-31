"""
One place that knows how to strip credentials out of text.

WHY THIS IS ITS OWN MODULE
==========================
`auth._redact` already existed and did the right thing, but it was reachable
only from auth.py and it knew about exactly three credentials by name:

    for v in (s.meta_app_secret, s.ig_access_token, s.github_pat):

That list is now wrong in two ways. It cannot see BLUESKY_APP_PASSWORD or
THREADS_ACCESS_TOKEN - which are not on Settings at all, because crosspost.py
reads them through config._opt - and publish.py never imported it, so the
module that makes the most credentialed network calls had no scrubbing at all.

THE LEAK THIS CLOSES
====================
The Graph API takes `access_token` as a QUERY PARAMETER. When a call fails at
the network level, the exception requests raises quotes the entire prepared
URL, token included:

    ProxyError: HTTPSConnectionPool(host='graph.facebook.com', port=443):
    Max retries exceeded with url:
    /v23.0/17999/?fields=status_code&access_token=EAAO<the real token>

That exception is printed by pipeline._publish_batch, piped through
`tee publish.log` by scheduled-publish.yml, and the last 2000 characters of
that file are posted into a GitHub issue comment. The repository is public.

GitHub's secret masking does not save you here, and it is worth being precise
about why: masking is applied to the workflow's LOG STREAM. It is not applied
to bytes a process writes to a file (`tee`), and it is not applied to payloads
sent to the REST API (the issue comment body). So the one path that ends
somewhere world-readable is exactly the path masking does not cover.

The Reel work widened the window: publish_reel polls for up to six minutes,
versus two for a carousel, so there is three times as long for a transient
network failure to produce that exception.

Rather than maintain a list of credential names, this collects every
environment variable whose NAME looks like a secret. A new integration's
credential is covered the day it is added, without anyone remembering to come
back here.
"""

from __future__ import annotations

import os
import re
from typing import Any, Iterable

# Names that hold credentials. Matched against the env var NAME, never the
# value - guessing at what a secret looks like by shape is how you end up
# redacting the word "token" out of an error message and not the token.
SECRET_NAME = re.compile(
    r"(TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|_KEY|PAT|CREDENTIAL)$", re.I)

# Below this length a "secret" is almost certainly a placeholder, and
# replacing a 3-character string everywhere would mangle unrelated text.
MIN_SECRET_LEN = 8

PLACEHOLDER = "***REDACTED***"


def secret_values(env: Any = None) -> Iterable[str]:
    env = os.environ if env is None else env
    seen = set()
    for name, value in env.items():
        v = (value or "").strip()
        if len(v) >= MIN_SECRET_LEN and SECRET_NAME.search(name) and v not in seen:
            seen.add(v)
            yield v


def redact(text: Any, env: Any = None) -> str:
    """Replace every credential-looking env value found in `text`.

    Longest first: if one secret is a substring of another (an app id inside a
    token, say), replacing the short one first would leave a recognisable
    fragment of the long one behind.
    """
    out = str(text)
    for v in sorted(secret_values(env), key=len, reverse=True):
        out = out.replace(v, PLACEHOLDER)
    return out


def safe_error(exc: BaseException, env: Any = None) -> str:
    """A one-line, credential-free description of an exception."""
    return redact(f"{type(exc).__name__}: {exc}", env=env)
