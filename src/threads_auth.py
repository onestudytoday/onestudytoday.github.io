"""
Keep the Threads token alive.

WHY THIS IS SEPARATE FROM auth.py
=================================
Threads is not "Instagram with another endpoint". Meta requires a Meta app
configured with the *Threads use case*, with its own app id and secret, its own
OAuth consent flow, its own user id, and its own token. An Instagram access
token does not work, and the Instagram app credentials do not work. So none of
auth.py applies, and mixing the two would produce a module where half the
functions silently need a different credential than the other half.

THE FAILURE THIS PREVENTS
=========================
A Threads long-lived token lasts 60 days. Nothing refreshed it, and every layer
of crossposting is deliberately fail-soft - post_to_threads catches, then
crosspost_all catches, then _publish_one catches. So on day 61 the token would
have started failing, the run would still exit 0, no alert would fire, and
crossposting to Threads would simply stop, permanently and silently, until
somebody happened to look at the account.

That is exactly the failure mode crosspost.py's own docstring warns about, and
it is worse than it sounds: once a long-lived token has actually EXPIRED it can
no longer be refreshed at all. Meta requires the token to be unexpired (and at
least 24 hours old) to exchange it. Miss the window and the only way back is
redoing the OAuth consent flow by hand.

    python src/threads_auth.py status
    python src/threads_auth.py ensure     # refresh if inside the window

STATUS OF THIS CODE
===================
The refresh call itself is one documented GET. It has NOT been exercised
against a live Threads app, because this account does not have one configured
yet - Threads stays entirely inert until THREADS_ACCESS_TOKEN and
THREADS_USER_ID are set. It is written to fail soft and to say clearly what it
did, so the worst case is that it reports a problem rather than causing one.
Treat the first live run as the real test.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any, Dict, Optional

import requests

from config import ROOT
from config import _opt as _opt_setting
from secrets_guard import redact, safe_error

BASE = "https://graph.threads.net"
TIMEOUT = 30

# Refresh with three weeks to spare. An expired token cannot be refreshed at
# all, so the cost of being early is one extra HTTP call and the cost of being
# late is a manual OAuth flow.
REFRESH_THRESHOLD_DAYS = 20
# Meta will not exchange a token younger than this.
MIN_TOKEN_AGE_HOURS = 24


def configured() -> bool:
    return bool(_opt_setting("THREADS_ACCESS_TOKEN", "")
                and _opt_setting("THREADS_USER_ID", ""))


def status() -> Dict[str, Any]:
    """What the token can currently reach. Never raises."""
    if not configured():
        return {"configured": False,
                "reason": "THREADS_ACCESS_TOKEN / THREADS_USER_ID not set"}
    token = _opt_setting("THREADS_ACCESS_TOKEN", "")
    try:
        r = requests.get(f"{BASE}/v1.0/me",
                         params={"fields": "id,username",
                                 "access_token": token}, timeout=TIMEOUT)
        j = r.json()
    except Exception as e:
        return {"configured": True, "ok": False, "error": safe_error(e)}
    if "error" in j:
        return {"configured": True, "ok": False,
                "error": redact(json.dumps(j["error"]))[:300]}
    return {"configured": True, "ok": True, "id": j.get("id"),
            "username": j.get("username")}


def refresh() -> Dict[str, Any]:
    """Exchange the long-lived token for a fresh 60-day one. Never raises."""
    if not configured():
        return {"refreshed": False, "skipped": True,
                "reason": "Threads is not configured"}
    token = _opt_setting("THREADS_ACCESS_TOKEN", "")
    try:
        r = requests.get(f"{BASE}/refresh_access_token",
                         params={"grant_type": "th_refresh_token",
                                 "access_token": token}, timeout=TIMEOUT)
        j = r.json()
    except Exception as e:
        return {"refreshed": False, "error": safe_error(e)}
    if "error" in j or "access_token" not in j:
        return {"refreshed": False,
                "error": redact(json.dumps(j.get("error") or j))[:300]}
    new = j["access_token"]
    expires_in = int(j.get("expires_in") or 0)
    written = persist(new)
    if not written.get("github_secret") and _opt_setting("GH_PAT", ""):
        # Same rule auth.ensure() enforces: a refresh that cannot be WRITTEN
        # BACK has not actually solved anything - the old token still expires
        # on schedule and the run that "succeeded" made no difference.
        return {"refreshed": False, "wrote": written,
                "error": "refreshed, but the new token could not be written "
                         "back to GitHub Secrets - the old one still expires"}
    return {"refreshed": True, "expires_in_days": round(expires_in / 86400, 1),
            "wrote": written,
            "token_tail": new[-6:] if len(new) > 6 else "?"}


def persist(new_token: str) -> Dict[str, bool]:
    """Write the refreshed token to .env and to GitHub Secrets if configured."""
    result = {"dotenv": False, "github_secret": False}

    env_path = ROOT / ".env"
    if env_path.exists():
        lines = env_path.read_text().splitlines()
        hit = False
        for i, ln in enumerate(lines):
            if ln.startswith("THREADS_ACCESS_TOKEN="):
                lines[i] = f"THREADS_ACCESS_TOKEN={new_token}"
                hit = True
        if not hit:
            lines.append(f"THREADS_ACCESS_TOKEN={new_token}")
        env_path.write_text("\n".join(lines) + "\n")
        result["dotenv"] = True

    pat = _opt_setting("GH_PAT", "")
    repo = _opt_setting("GITHUB_REPOSITORY", "")
    if pat and repo:
        try:
            from auth import _put_github_secret
            _put_github_secret(repo, pat, "THREADS_ACCESS_TOKEN", new_token)
            result["github_secret"] = True
        except Exception as e:
            result["github_secret"] = False
            result["github_error"] = safe_error(e)
    return result


def ensure() -> Dict[str, Any]:
    """Refresh only when it is worth doing. Never raises."""
    st = status()
    if not st.get("configured"):
        return {"skipped": True, "reason": st.get("reason")}
    if not st.get("ok"):
        # Reaching /me failed. That may be an expired token, in which case a
        # refresh cannot help and a human is needed - say so rather than
        # burning a call and reporting a confusing failure.
        return {"skipped": False, "ok": False, "status": st,
                "hint": "the token may have expired; an expired Threads token "
                        "cannot be refreshed and needs the OAuth flow again"}
    return refresh()


def _main(argv) -> int:
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "status":
        print(json.dumps(status(), indent=2))
    elif cmd in ("ensure", "refresh"):
        out = ensure() if cmd == "ensure" else refresh()
        print(json.dumps(out, indent=2))
        # Non-zero only when Threads IS configured and the refresh failed -
        # "not configured" is the normal state and must stay green.
        if out.get("error") or out.get("ok") is False:
            return 1
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
