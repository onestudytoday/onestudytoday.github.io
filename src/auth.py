"""
Instagram / Meta token lifecycle.

Meta long-lived tokens die after 60 days. That is the single most likely way
this whole pipeline silently stops working, so it is handled first and handled
properly:

  * `inspect()`  - what kind of token is this, when does it die
  * `refresh()`  - exchange for a fresh 60-day token (auto-detects token type)
  * `persist()`  - write the new token back to .env AND to GitHub Secrets
  * `verify()`   - prove the token can actually reach the IG account
  * `ensure()`   - the one function the scheduled job calls

Run manually any time:
    python src/auth.py status
    python src/auth.py refresh
    python src/auth.py verify

The GitHub Action in .github/workflows/token-refresh.yml runs `ensure` every
Sunday. It refreshes when fewer than REFRESH_THRESHOLD_DAYS remain, so even if
three consecutive runs fail you still have weeks of runway.
"""

from __future__ import annotations

import base64
import json
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests

from config import ROOT, settings

REFRESH_THRESHOLD_DAYS = 20
TIMEOUT = 30

IG_GRAPH = "https://graph.instagram.com"


class AuthError(RuntimeError):
    pass


@dataclass
class TokenInfo:
    valid: bool
    kind: str                 # USER | PAGE | IG_USER | UNKNOWN
    app_id: str
    expires_at: int           # unix; 0 means "never expires"
    data_access_expires_at: int
    scopes: list
    raw: Dict[str, Any]

    @property
    def never_expires(self) -> bool:
        return self.expires_at == 0

    @property
    def days_left(self) -> float:
        if self.never_expires:
            return float("inf")
        return (self.expires_at - time.time()) / 86400.0

    @property
    def data_access_days_left(self) -> float:
        if not self.data_access_expires_at:
            return float("inf")
        return (self.data_access_expires_at - time.time()) / 86400.0

    def human(self) -> str:
        def fmt(ts):
            if not ts:
                return "never"
            return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        dl = "never" if self.never_expires else f"{self.days_left:.1f} days"
        return (
            f"  valid          : {self.valid}\n"
            f"  token type     : {self.kind}\n"
            f"  app id         : {self.app_id}\n"
            f"  expires        : {fmt(self.expires_at)}  ({dl} left)\n"
            f"  data access    : {fmt(self.data_access_expires_at)}"
            f"  ({self.data_access_days_left:.1f} days left)\n"
            f"  scopes         : {', '.join(self.scopes) or '(none reported)'}"
        )


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------
def inspect(token: Optional[str] = None) -> TokenInfo:
    s = settings()
    tok = token or s.ig_access_token
    app_token = f"{s.meta_app_id}|{s.meta_app_secret}"

    try:
        r = requests.get(
            f"{s.graph}/debug_token",
            params={"input_token": tok, "access_token": app_token},
            timeout=TIMEOUT,
        )
        payload = r.json()
    except Exception as e:  # network / json
        raise AuthError(f"Could not reach the Graph API: {e}")

    if "error" in payload:
        # Might be an Instagram-Login token, which Facebook's debug endpoint
        # will not recognise. Probe the Instagram graph host instead.
        ig = _probe_instagram_login(tok)
        if ig:
            return ig
        raise AuthError(
            "Graph API rejected the token during inspection:\n  "
            + json.dumps(payload["error"], indent=2)
        )

    d = payload.get("data", {})
    return TokenInfo(
        valid=bool(d.get("is_valid")),
        kind=(d.get("type") or "UNKNOWN").upper(),
        app_id=str(d.get("app_id", "")),
        expires_at=int(d.get("expires_at", 0) or 0),
        data_access_expires_at=int(d.get("data_access_expires_at", 0) or 0),
        scopes=list(d.get("scopes", []) or []),
        raw=d,
    )


def _probe_instagram_login(tok: str) -> Optional[TokenInfo]:
    """Tokens issued by Instagram Login live on graph.instagram.com."""
    try:
        r = requests.get(
            f"{IG_GRAPH}/me",
            params={"fields": "id,username", "access_token": tok},
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            return None
        d = r.json()
    except Exception:
        return None
    return TokenInfo(
        valid=True,
        kind="IG_USER",
        app_id="",
        expires_at=0,           # unknown from this endpoint; refresh is idempotent
        data_access_expires_at=0,
        scopes=[],
        raw=d,
    )


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------
def refresh(token: Optional[str] = None) -> Dict[str, Any]:
    """Exchange the current token for a fresh long-lived one.

    Returns {"access_token": ..., "expires_in": ..., "path": ...}.
    Safe to call repeatedly: Meta issues a new token and the old one keeps
    working until its own expiry, so a failed write-back is never fatal.
    """
    s = settings()
    tok = token or s.ig_access_token
    info = inspect(tok)

    if info.kind == "IG_USER":
        r = requests.get(
            f"{IG_GRAPH}/refresh_access_token",
            params={"grant_type": "ig_refresh_token", "access_token": tok},
            timeout=TIMEOUT,
        )
        j = r.json()
        if "access_token" not in j:
            raise AuthError(f"ig_refresh_token failed: {json.dumps(j, indent=2)}")
        return {"access_token": j["access_token"],
                "expires_in": j.get("expires_in", 5184000),
                "path": "ig_refresh_token"}

    if info.kind == "PAGE" and info.never_expires:
        return {"access_token": tok, "expires_in": 0, "path": "noop_page_token_never_expires"}

    # USER token (and expiring PAGE tokens): the fb_exchange_token dance
    r = requests.get(
        f"{s.graph}/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": s.meta_app_id,
            "client_secret": s.meta_app_secret,
            "fb_exchange_token": tok,
        },
        timeout=TIMEOUT,
    )
    j = r.json()
    if "access_token" not in j:
        raise AuthError(f"fb_exchange_token failed: {json.dumps(j, indent=2)}")
    return {"access_token": j["access_token"],
            "expires_in": j.get("expires_in", 5184000),
            "path": "fb_exchange_token"}


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def persist(new_token: str) -> Dict[str, bool]:
    """Write the new token to .env and, if configured, to GitHub Secrets."""
    result = {"dotenv": False, "github_secret": False}

    env_path = ROOT / ".env"
    if env_path.exists():
        lines = env_path.read_text().splitlines()
        hit = False
        for i, ln in enumerate(lines):
            if ln.startswith("IG_ACCESS_TOKEN="):
                lines[i] = f"IG_ACCESS_TOKEN={new_token}"
                hit = True
        if not hit:
            lines.append(f"IG_ACCESS_TOKEN={new_token}")
        env_path.write_text("\n".join(lines) + "\n")
        result["dotenv"] = True

    s = settings()
    if s.github_pat and s.github_repo:
        try:
            _put_github_secret(s.github_repo, s.github_pat, "IG_ACCESS_TOKEN", new_token)
            result["github_secret"] = True
        except Exception as e:
            print(f"  ! GitHub secret write-back failed: {e}", file=sys.stderr)

    # local audit trail so you can always see when it last rotated
    log = ROOT / "data" / "token_history.jsonl"
    with log.open("a") as f:
        f.write(json.dumps({
            "at": datetime.now(timezone.utc).isoformat(),
            "token_tail": new_token[-8:],
            "persisted": result,
        }) + "\n")
    return result


def _put_github_secret(repo: str, pat: str, name: str, value: str) -> None:
    """Encrypt with the repo public key (libsodium sealed box) and PUT it."""
    from nacl import encoding, public  # PyNaCl

    h = {"Authorization": f"Bearer {pat}",
         "Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28"}
    k = requests.get(f"https://api.github.com/repos/{repo}/actions/secrets/public-key",
                     headers=h, timeout=TIMEOUT)
    k.raise_for_status()
    kd = k.json()
    pk = public.PublicKey(kd["key"].encode(), encoding.Base64Encoder())
    sealed = public.SealedBox(pk).encrypt(value.encode())
    r = requests.put(
        f"https://api.github.com/repos/{repo}/actions/secrets/{name}",
        headers=h,
        json={"encrypted_value": base64.b64encode(sealed).decode(),
              "key_id": kd["key_id"]},
        timeout=TIMEOUT,
    )
    if r.status_code not in (201, 204):
        raise AuthError(f"GitHub returned {r.status_code}: {r.text}")


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
def verify(token: Optional[str] = None) -> Dict[str, Any]:
    """Prove the token can actually reach the Instagram account we publish to."""
    s = settings()
    tok = token or s.ig_access_token
    r = requests.get(
        f"{s.graph}/{s.ig_business_account_id}",
        params={"fields": "id,username,name,followers_count,media_count",
                "access_token": tok},
        timeout=TIMEOUT,
    )
    j = r.json()
    if "error" in j:
        raise AuthError(
            "Token cannot reach the Instagram account.\n  "
            + json.dumps(j["error"], indent=2)
            + "\n\n  Most common causes:\n"
              "   - the token is missing instagram_basic / instagram_content_publish\n"
              "   - the IG account is not a Business or Creator account\n"
              "   - the IG account is not linked to the Facebook Page the app can access"
        )
    return j


# ---------------------------------------------------------------------------
# The one call the scheduler makes
# ---------------------------------------------------------------------------
def ensure(force: bool = False) -> Dict[str, Any]:
    info = inspect()
    out: Dict[str, Any] = {"before": asdict(info), "refreshed": False}

    if not info.valid and info.kind != "IG_USER":
        raise AuthError(
            "Token reports as INVALID. Automatic refresh cannot fix this - a "
            "human has to re-authorise the app.\n"
            "See docs/RUNBOOK.md section 'Re-authorising from scratch'."
        )

    needs = force or (info.days_left <= REFRESH_THRESHOLD_DAYS)
    if not needs:
        print(f"Token healthy: {info.days_left:.1f} days left. No action.")
        out["reason"] = "healthy"
        return out

    print(f"Refreshing (days left: {info.days_left:.1f}, threshold: {REFRESH_THRESHOLD_DAYS})")
    res = refresh()
    new = res["access_token"]

    # never persist a token we have not proved works
    verify(new)
    written = persist(new)

    after = inspect(new)
    out.update({"refreshed": True, "path": res["path"],
                "written": written, "after": asdict(after)})
    print(f"Refreshed via {res['path']}. New token valid for "
          f"{after.days_left:.1f} days. Written: {written}")
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "status":
        info = inspect()
        print("Instagram token status\n" + info.human())
        if info.days_left < REFRESH_THRESHOLD_DAYS:
            print(f"\n  ACTION: below the {REFRESH_THRESHOLD_DAYS}-day threshold. "
                  f"Run: python src/auth.py refresh")
    elif cmd == "refresh":
        print(json.dumps(ensure(force=True), indent=2, default=str))
    elif cmd == "ensure":
        print(json.dumps(ensure(), indent=2, default=str))
    elif cmd == "verify":
        acct = verify()
        print("Token reaches the account:")
        for k, v in acct.items():
            print(f"  {k:16}: {v}")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
