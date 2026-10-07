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

from config import ROOT, _opt, settings

REFRESH_THRESHOLD_DAYS = 20
TIMEOUT = 30

IG_GRAPH = "https://graph.instagram.com"


class AuthError(RuntimeError):
    pass


def _redact(text: Any) -> str:
    """Blank out credentials before they can end up in an error message.

    The Graph API takes the app secret and the access token as query
    parameters, so when a call fails at the network level the exception text
    requests raises quotes the whole URL back - secrets included. GitHub masks
    known secrets in Actions logs, but nothing masks the terminal on your own
    laptop, and a screenshot of a red error is exactly the thing people paste
    into an issue or send to someone when asking for help.
    """
    s = settings()
    out = str(text)
    for v in (s.meta_app_secret, s.ig_access_token, s.github_pat):
        if v and len(v) > 6:
            out = out.replace(v, "***REDACTED***")
    return out


@dataclass
class TokenInfo:
    valid: bool
    kind: str                 # USER | PAGE | IG_USER | UNKNOWN
    app_id: str
    # unix timestamp; 0 means "never expires"; None means "WE DO NOT KNOW".
    #
    # Those last two were the same value until 7 Oct 2026, and conflating
    # them is what let the account's token die. See never_expires below.
    expires_at: Optional[int]
    data_access_expires_at: int
    scopes: list
    raw: Dict[str, Any]

    @property
    def expiry_known(self) -> bool:
        """Did the API actually tell us when this token dies?

        Instagram-Login tokens are inspected through graph.instagram.com/me,
        which does not report an expiry at all. That is not the same as a
        token that never expires, and treating it as one is what broke:
        `expires_at = 0` meant days_left was inf, inf is never <= the 20-day
        threshold, so ensure() printed "Token healthy" and refreshed nothing
        every Sunday for two months while a 60-day token ran down. The
        workflow went green each week, and the alert issue is wired to
        `if: failure()`, so the one warning that mattered never fired.
        """
        return self.expires_at is not None

    @property
    def never_expires(self) -> bool:
        """A token the API positively says has no expiry (a Page token)."""
        return self.expires_at == 0

    @property
    def days_left(self) -> float:
        """Days until expiry. inf for a true never-expiring token.

        NaN when the expiry is unknown, so that every comparison against it is
        False - including `days_left > threshold`. An unknown expiry must not
        be able to pass a "plenty of time left" test by accident, whichever
        direction a future caller writes the comparison.
        """
        if self.expires_at is None:
            return float("nan")
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
            if ts is None:
                return "not reported by this endpoint"
            if not ts:
                return "never"
            return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if not self.expiry_known:
            dl = "UNKNOWN - will refresh on every run"
        elif self.never_expires:
            dl = "never"
        else:
            dl = f"{self.days_left:.1f} days"
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
        raise AuthError(f"Could not reach the Graph API: {_redact(e)}")

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
        # None, not 0. This endpoint does not report an expiry, and "we do
        # not know" is not "it never expires" - see TokenInfo.expiry_known.
        # ensure() refreshes unconditionally when it does not know, which is
        # safe because refreshing is idempotent and this runs weekly.
        expires_at=None,
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

    # REFRESH WHEN WE CANNOT TELL.
    #
    # The old condition was `days_left <= THRESHOLD` alone, and an unknown
    # expiry evaluated to inf, so it never fired. Refreshing is idempotent and
    # this job runs once a week, so the cost of refreshing a token that did not
    # need it is one API call; the cost of NOT refreshing one that did is the
    # account going silent with no warning, which is what happened.
    if not info.expiry_known:
        print("The API did not report an expiry for this token, so refreshing "
              "rather than assuming it is immortal.")
    needs = force or (not info.expiry_known) or (info.days_left <= REFRESH_THRESHOLD_DAYS)
    if not needs:
        print(f"Token healthy: {info.days_left:.1f} days left. No action.")
        out["reason"] = "healthy"
        return out

    left = "unknown" if not info.expiry_known else f"{info.days_left:.1f}"
    print(f"Refreshing (days left: {left}, threshold: {REFRESH_THRESHOLD_DAYS})")
    res = refresh()
    new = res["access_token"]

    # never persist a token we have not proved works
    verify(new)
    written = persist(new)

    after = inspect(new)
    out.update({"refreshed": True, "path": res["path"],
                "written": written, "after": asdict(after)})
    left = "unknown" if not after.expiry_known else f"{after.days_left:.1f} days"
    print(f"Refreshed via {res['path']}. New token valid for {left}. "
          f"Written: {written}")

    # THE ASSERTION THAT GIVES THE ALERT SOMETHING TO FIRE ON.
    #
    # token-refresh.yml's alert is `if: failure()`, so it can only fire when a
    # step goes red - and for two months nothing did. This function printed
    # "Token healthy" every Sunday while a 60-day token ran down, because an
    # Instagram-Login token reports no expiry and that read as "never
    # expires". The job was green the whole way to the token dying mid-week.
    #
    # Checked HERE rather than in a following workflow step, because this is
    # the only place the NEW token exists: the step after this one still has
    # the old value of ${{ secrets.IG_ACCESS_TOKEN }} in its environment -
    # secrets are resolved when the job starts - so a separate step would
    # inspect the token we just replaced and cry wolf on every real refresh.
    if after.expiry_known and not after.never_expires \
            and after.days_left <= REFRESH_THRESHOLD_DAYS:
        raise AuthError(
            f"The refresh ran and was saved, but the new token still expires "
            f"in {after.days_left:.1f} days - inside the "
            f"{REFRESH_THRESHOLD_DAYS}-day threshold. Meta did not issue a "
            f"full-length token. Re-authorise by hand before this one lapses: "
            f"docs/RUNBOOK.md, 'Re-authorising from scratch'.")

    # A refresh that could not be SAVED is a failed refresh, and it has to
    # exit non-zero or nobody ever finds out.
    #
    # persist() catches every exception from the GitHub secret write-back and
    # only prints to stderr, so an expired, revoked or misnamed GH_PAT left
    # the repository secret holding the OLD token while this function returned
    # normally. token-refresh.yml then went green every Sunday - and because
    # the alert issue is wired to `if: failure()`, the one notification you
    # actually need never fired. The token would simply age out and, on about
    # day 60, drafting and publishing would stop together with no warning.
    #
    # In CI (GITHUB_REPOSITORY set) the repository secret is the ONLY copy
    # that matters: there is no .env on a runner, and the runner's filesystem
    # is discarded. So a dotenv-only write is not success there.
    in_ci = bool(_opt("GITHUB_REPOSITORY"))
    if in_ci and not written.get("github_secret"):
        raise AuthError(
            "Token was refreshed and verified, but could NOT be written back "
            "to the GitHub repository secret - so the new token has been "
            "thrown away and IG_ACCESS_TOKEN still holds the old one.\n"
            "Usual cause: the GH_PAT secret is missing, misnamed, expired, or "
            "lacks the 'Secrets: write' permission on this repository.\n"
            "Fix it in docs/RUNBOOK.md under 'Re-authorising from scratch'. "
            "Until it is fixed, posting stops when the current token expires."
        )
    if not any(written.values()):
        raise AuthError(
            "Token was refreshed and verified, but was not persisted "
            "anywhere - neither .env nor the GitHub repository secret. The "
            "new token has been lost."
        )
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "status":
        info = inspect()
        print("Instagram token status\n" + info.human())
        if not info.expiry_known:
            print("\n  NOTE: this endpoint does not report an expiry, so the "
                  "weekly job refreshes unconditionally.")
        elif info.days_left < REFRESH_THRESHOLD_DAYS:
            print(f"\n  ACTION: below the {REFRESH_THRESHOLD_DAYS}-day threshold. "
                  f"Run: python src/auth.py refresh")
    elif cmd == "check":
        # EXITS NON-ZERO when the token is in a state that will stop posting.
        #
        # The weekly job's alert is wired to `if: failure()`, so it can only
        # fire if some step actually fails - and for two months nothing did.
        # ensure() reported "healthy" every Sunday while a 60-day token ran
        # down, because an unknown expiry read as infinite. This is the step
        # that turns "the token is dying" into a failure the alert can see.
        info = inspect()
        print("Instagram token status\n" + info.human())
        if not info.valid:
            print("\n  FAIL: the token is not valid. A human has to "
                  "re-authorise - docs/RUNBOOK.md, 'Re-authorising from "
                  "scratch'.")
            return 1
        if info.expiry_known and not info.never_expires \
                and info.days_left < REFRESH_THRESHOLD_DAYS:
            print(f"\n  FAIL: {info.days_left:.1f} days left, below the "
                  f"{REFRESH_THRESHOLD_DAYS}-day threshold, and the refresh "
                  f"that just ran did not move it.")
            return 1
        print("\n  OK: posting will keep working.")
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
