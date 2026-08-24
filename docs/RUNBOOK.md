# RUNBOOK

Everything you have to do, in order. Written assuming you have never used a
terminal. Where a step needs you specifically — an account, a key, a click —
it is marked **YOU**.

---

## What this thing actually is

A folder of small programs that, five mornings a week, will:

1. search the scientific literature for that day's niche
2. throw out anything retracted, predatory, stale, or too thin to summarise
3. score what survives for credibility, and work out the design, the sample
   size, and whether it was even done in humans
4. write the carousel copy, then check that copy against the abstract for
   overclaiming
5. render five 1080×1350 slides
6. open a GitHub issue with the slides so you can review it from your phone
7. publish only after you comment `approve`

You are the last gate. Nothing goes live without you.

---

## Part 1 — Accounts and keys (about 30 minutes, once)

### 1.1 GitHub  **YOU**

1. Make a free account at <https://github.com> if you do not have one. The
   username becomes part of your public bio URL, so pick something you are
   happy for followers to see.
2. Click **+** (top right) → **New repository**.
   - **Name it exactly `YOUR-USERNAME.github.io`** — for example, if your
     username is `onestudytoday`, name the repo `onestudytoday.github.io`.
     GitHub treats a repo with that exact name specially and serves it at the
     bare domain, so your bio link becomes `https://onestudytoday.github.io/`
     instead of `https://onestudytoday.github.io/onestudytoday/`. Shorter, cleaner,
     and it costs nothing extra.
   - Set it to **Public**. This is what makes Actions and Pages free forever,
     and Instagram has to be able to reach the images anyway.
   - Do not tick "Add a README" or anything else. Click **Create repository**.
3. On the next screen click **uploading an existing file**.
4. Unzip the folder I gave you. Open it, select **everything inside** (not the
   folder itself), and drag it into the browser. Click **Commit changes**.

> Your secrets are not in that upload. `.env` is blocked by `.gitignore`. The
> only place your token lives is GitHub's encrypted secret store, which stays
> private even on a public repo.

### 1.2 Turn on GitHub Pages  **YOU**

Repo → **Settings** → **Pages** (left sidebar)
- Source: **Deploy from a branch**
- Branch: `main`, folder: **`/docs`**
- **Save**

Wait about a minute, then note the URL it gives you. If you named the repo
`YOUR-USERNAME.github.io` as recommended, it is simply:

```
https://YOUR-USERNAME.github.io/
```

This is two things at once: your link-in-bio page, and the place Instagram
downloads your slide images from.

### 1.3 Anthropic API key  **YOU — optional, can be added later**

This is what writes the slide copy automatically. It is a *separate product
from a Claude subscription*, with separate billing.

- **With a key:** the pipeline drafts the whole carousel overnight; you spend
  about 90 seconds reading and approving.
- **Without a key:** everything else still runs — sourcing, vetting, rendering,
  publishing — and you get a pre-filled skeleton to write yourself, about 10
  minutes per post. Set `ALLOW_PREPRINTS` and the rest as normal and just leave
  `ANTHROPIC_API_KEY` blank.

You can add it any time. The code detects the key and switches over on the next
run; nothing needs rebuilding.

To get one:

1. Go to <https://console.anthropic.com/settings/keys>
2. Create a key. Copy it — it is shown once and never again.
3. Under **Billing**, add about $10 of credit. At five posts a week that lasts
   months. Expect $3–10/month.

### 1.4 GitHub personal access token  **YOU**

This is what lets the Sunday job write your refreshed Instagram token back
into your secrets automatically. Skip it and you will be pasting a token by
hand every 60 days.

1. <https://github.com/settings/personal-access-tokens/new>
2. Token name: `onestudytoday-secret-writer`
3. Expiration: **No expiration** (or set a calendar reminder)
4. Repository access: **Only select repositories** → pick `onestudytoday`
5. Permissions → Repository permissions → **Secrets: Read and write**
6. Generate, copy it.

### 1.5 Load the secrets  **YOU**

Repo → **Settings** → **Secrets and variables** → **Actions**

Under the **Secrets** tab, click **New repository secret** six times:

| Name | Value |
|---|---|
| `META_APP_ID` | `1055186336869529` |
| `META_APP_SECRET` | the app secret from your `.env` |
| `IG_ACCESS_TOKEN` | the long token from your `.env` |
| `IG_BUSINESS_ACCOUNT_ID` | `17841448165128378` |
| `ANTHROPIC_API_KEY` | from step 1.3 — **leave this one out entirely if you are starting without it** |
| `GH_PAT` | from step 1.4 |

Then switch to the **Variables** tab and add four:

| Name | Value |
|---|---|
| `PUBLIC_IMAGE_BASE` | `https://YOUR-USERNAME.github.io/img` (no trailing slash) |
| `HANDLE` | `@onestudytoday` |
| `THEME` | `neon` |
| `ALLOW_PREPRINTS` | `true` |

> **Rotate your app secret.** It has been sitting in a file that moved between
> machines. Meta app dashboard → Settings → Basic → **Reset** next to App
> Secret, then update the `META_APP_SECRET` value above. Takes 30 seconds and
> closes the only real security hole in this setup.

### 1.6 Rank candidates by projected engagement  **YOU — optional, can be added later**

Sourcing already leans toward whichever eligible candidate looks likely to
land best, using two free signals it fetches anyway — citation count and
open-access status. For a study published in the last 14 days both are
usually still zero (neither citations nor real attention has had time to
accumulate), so this mostly just falls back to picking the newest one, same
as before.

A stronger signal — real news/social/Reddit attention, from Altmetric —
exists, but there is no instant self-serve signup for it. Altmetric requires
a key for every request now, and free keys go through their **Scientometric
Researcher Access** program, explicitly scoped to "a specific academic
project" — worth applying for, not guaranteed for a social account like this
one. Start at <https://www.altmetric.com/solutions/free-tools/> if you want
to try.

If you get a key: add one more secret, `ALTMETRIC_API_KEY`. That's the whole
integration — the code already checks for it and switches over automatically.
If you don't: skip this section entirely. Nothing else changes.

---

## Part 2 — Prove the token works (2 minutes)

Repo → **Actions** tab → **Keep the Instagram token alive** → **Run workflow**.

Watch it run. In the log you want to see:

```
Instagram token status
  valid          : True
  token type     : USER          (or PAGE)
  expires        : 2026-10-xx    (xx days left)
```

**If it says `valid: False`** or errors, jump to *Re-authorising from scratch*
at the bottom. Do not continue until this step is green — everything else
depends on it.

From here the token takes care of itself. The job runs every Sunday, refreshes
whenever fewer than 20 days remain, writes the new token back into your
secrets, and opens an issue shouting at you if it ever fails.

---

## Part 3 — Your first post

### 3.1 Fire a draft manually

Actions → **Draft today's post** → **Run workflow** → leave the fields blank →
**Run workflow**.

Give it three or four minutes. It will:
- pull candidates for today's niche
- print each one with its verdict and score in the log
- draft, lint, audit and render the winner
- commit the slides
- open an issue titled `REVIEW <niche> · <headline>`

### 3.2 Review it

Open the **Issues** tab. The issue has the rendered slides inline, the vetting
report, and the full caption. This works fine on a phone through the GitHub
mobile app.

Read the slides against the paper — the link is at the top. You are checking
one thing: **does the copy say anything the paper does not?**

### 3.3 Publish

Comment `approve` on the issue.

That closes the issue and marks the post approved - it does **not** post to
Instagram right away. A separate workflow checks every 15 minutes for
approved posts and publishes each one once its niche's peak-engagement time
arrives (the table in `docs/GROWTH.md` - e.g. nature/psych at 7am Central,
health at noon). Reviewing at 6am does not publish at 6am; it just means the
post is ready and waiting for its slot. Once it actually goes out, the bot
comments on the (already-closed) issue again with the result, and the
link-in-bio page rebuilds itself.

Want something out immediately instead of waiting for its slot? Actions →
**Publish approved posts** → **Run workflow**. That publishes everything
currently approved right now, ignoring the time gate.

Other comments it understands:
- `kill` — reject it, and never source that study again
- `force approve` — publish despite a flagged blocker. Only use this when you
  have read the paper yourself and think the checker is wrong.

### 3.4 Set your bio link

Instagram → Edit profile → Website:
`https://YOUR-USERNAME.github.io/`

That page rebuilds itself every time you publish. You never touch it.

---

## Part 4 — Running it on your own laptop (optional)

You do not need this. It is useful for fiddling with copy and for seeing the
richer review interface.

**Mac:** open Terminal (Cmd+Space, type "terminal").
**Windows:** install Python from <https://python.org/downloads> — tick *Add
Python to PATH* — then open PowerShell.

```bash
cd path/to/onestudytoday
pip install -r requirements.txt
cp .env.example .env
```

Open `.env` in any text editor and fill in the same values as the secrets
above. Then:

```bash
python src/auth.py status              # is the token healthy?
python src/auth.py verify              # can it reach the account?
python src/pipeline.py run             # draft today's post
python src/review.py serve             # opens http://localhost:8765
```

The local review page lets you edit the headline, the caveats, and the caption,
then re-render instantly. Approving there sets the same `approved` status the
workflow uses.

Publishing is deliberately two-stage:

```bash
python src/pipeline.py publish-approved          # DRY RUN, prints the plan
python src/pipeline.py publish-approved --live   # actually posts
```

---

## Part 5 — Everyday commands

| I want to... | Command |
|---|---|
| see what today's sourcing finds, without drafting | `python src/pipeline.py run --sources-only` |
| draft a different niche | `python src/pipeline.py run --niche physics` |
| widen the date window | `python src/pipeline.py run --days 21` |
| draft 3 posts to bank ahead | `python src/pipeline.py run --limit 3` |
| draft without spending API credit | `python src/pipeline.py run --no-api` |
| see the vetting report for every candidate | `python src/vet.py health` |
| re-render after editing a post file | `python src/pipeline.py render <post-id>` |
| check the guardrails still work | `python -m pytest tests/ -q` |
| rebuild the bio page | `python src/linkinbio.py` |
| publish anything approved, right now | `python src/pipeline.py publish-approved --live` |
| publish only what's past its niche's slot | `python src/pipeline.py publish-scheduled --live` |

---

## Part 6 — When something breaks

### "No post today — nothing cleared vetting"
Normal. Some days the literature is thin, especially Thursdays. Re-run with
`days = 21`, or loosen the topic terms in `config/niches.yaml`.

### The post published but the images are broken
GitHub Pages had not finished deploying when Instagram tried to fetch them.
Check `https://YOUR-USERNAME.github.io/onestudytoday/img/<post-id>/01_cover.jpg`
loads in a browser. If Pages is off, turn it back on (step 1.2).

### "PUBLIC_IMAGE_BASE is not set"
The variable in step 1.5 is missing or has a typo. It must end in `/img` and
have no trailing slash.

### Token refresh failed
An issue is opened automatically. Usually the `GH_PAT` expired. Make a new one
(step 1.4) and update the secret.

### Re-authorising from scratch
If the token is genuinely dead, no script can save it — Meta requires a human
to re-consent.

1. <https://developers.facebook.com/tools/explorer/>
2. Pick your app, then **User Token**
3. Add permissions: `instagram_basic`, `instagram_content_publish`,
   `pages_show_list`, `pages_read_engagement`, `business_management`
4. **Generate Access Token**, approve the dialog
5. Copy the short-lived token, then run:
   ```bash
   python src/auth.py refresh
   ```
   with `IG_ACCESS_TOKEN` temporarily set to that short token. It exchanges it
   for a fresh 60-day one and writes it back to your secrets.

### Instagram rejected the image
The Graph API wants JPEG, under 8 MB, aspect ratio between 4:5 and 1.91:1.
The pipeline already produces exactly that. If you swapped in your own image,
that is the spec.

---

## Part 7 — What each file does

```
src/auth.py        token status, refresh, write-back, verification
src/sources.py     Europe PMC, arXiv, Crossref, bioRxiv clients
src/vet.py         THE GUARDRAILS. retraction, preprint, correlation,
                   sample size, species, funding, journal tier
src/draft.py       abstract to slide copy, lint, and the anti-overclaim audit
src/theme.py       colours, fonts, the three visual systems
src/render.py      turns a post into 1080x1350 PNGs
src/caption.py     caption assembly and hashtag rotation
src/publish.py     Instagram carousel publishing
src/review.py      the local review web app and the approval gate
src/issue.py       builds the phone-friendly GitHub issue
src/linkinbio.py   builds your bio page from published posts
src/pipeline.py    the orchestrator that runs all of it

config/niches.yaml     what to search for, per weekday, and the journal tiers
config/copy_spec.yaml  the post template as enforceable word counts
config/hashtags.yaml   the three-tier hashtag strategy

tests/             offline proof that the guardrails work
samples/           five worked posts from real studies
```
