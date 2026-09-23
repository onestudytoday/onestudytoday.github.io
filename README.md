# One Study Today

An automated Instagram account that turns newly published scientific studies
into carousels a general audience actually reads — and that is architecturally
incapable of publishing hype.

**Start here:** [`docs/RUNBOOK.md`](docs/RUNBOOK.md) — full setup, written for
someone who has never used a terminal.
**Then:** [`docs/LAUNCH.md`](docs/LAUNCH.md) — the ten days around your first
post, from zero followers.
**After that:** [`docs/GROWTH.md`](docs/GROWTH.md) — cadence and growth plan.

---

## What happens every weekday morning

```
  sourcing          Europe PMC · arXiv · Crossref · bioRxiv/medRxiv
     |              per-niche queries, ~75-day window, dedupe ledger
     v
  ranking           heuristic (relatable subject + surprise markers)
     |              then one batched model call: "would a curious
     |              non-scientist stop scrolling, presented HONESTLY?"
     |              -> reorders only; cannot admit anything vetting rejects
     v
  vetting           retraction check · predatory publisher blocklist
     |              preprint detection · design classification
     |              sample size · species · funding · journal tier
     |              -> REJECT (killed) / HOLD (warned) / PASS
     v
  drafting          abstract -> slide copy, locked to exact word counts
     |              lint: banned words, causal verbs, animal claims
     |              audit: every claim re-checked against the abstract
     v
  rendering         5-8 1080x1350 slides, deterministic. cover is a photo
     |              with one sentence over it. cover carries the
     |              IMPLICATION, slide 2 the finding, slide 3 the implication
     |              explained - plus an optional clipping slide showing a
     |              named outlet that already made the same argument.
     v
  review            GitHub issue on your phone, or a local web app
     |              YOU comment `approve`. Nothing else publishes.
     v
  publishing        Instagram Graph API carousel, at your niche's
                    configured publish time (config/niches.yaml) - not the
                    moment you approved it. link-in-bio rebuilds itself.
```

---

## The guardrails, specifically

These are gates in the pipeline, not items on a checklist.

| Rule | Effect |
|---|---|
| Retracted, withdrawn, or under expression of concern | **hard reject** |
| Publisher on the predatory blocklist | **hard reject** |
| No DOI and no stable URL | **hard reject** |
| Older than the recency window | **hard reject** |
| Abstract too thin to summarise honestly | **hard reject** |
| Preprint | forced `PREPRINT — NOT PEER REVIEWED` badge on the cover, forced caveat, forced hedged language in the draft prompt |
| Observational design | causal verbs banned from every slide; "found a pattern, not a cause" caveat forced |
| Study not done in humans | species named on the slide; "not humans" caveat forced |
| Human sample under 30 | number stated on the slide; generalisation blocked |
| Relative risk with no absolute baseline | bare percentage banned from the cover slide |
| Industry funding detected | disclosed on the fine-print slide |
| Journal on the low-rigour watchlist | credibility points deducted, post held |
| Implication is ours, not the paper's | marked `inferred`; the slide AND the cover must be conditional, the fine-print slide gains "claims are our interpretation of this study, read it for yourself at the DOI provided" — added by the renderer, so no edit can remove it — and an unhedged one is blocked |
| Copy too dense for a general reader | above US grade 12 (a high-school senior), or over 7% four-syllable words, is sent back for a rewrite with the offending words named |
| Wrong journal named on the cover | **hard blocker**, checked in code against the catalogue record — not by a model that was only ever shown the abstract |
| Cover image captioned in a non-Latin script | never used; the file's own title is in the same language as the labels in the picture |
| Implication slide missing or out of order | blocked — the finding has to be on the page before the extrapolation |
| Clipping from an outlet not on the allowlist | never shown; the outlet is taken from the URL and the record's own domain field has to agree |
| Clipping that could not be photographed | no page at all — the slide IS a real screenshot of the outlet's own page, never a headline re-typeset in our fonts |

Prove they work, offline, in a couple of minutes:

```bash
python -m pytest tests/ -q
```

---

## Quick reference

```bash
python src/auth.py status               # token health and days remaining
python src/auth.py verify               # can it reach the IG account?

python src/pipeline.py run              # today's niche, end to end
python src/pipeline.py run --sources-only   # just show what sourcing finds
python src/vet.py health                # vetting report for every candidate

python src/review.py serve              # http://localhost:8765
python src/pipeline.py publish-approved         # dry run
python src/pipeline.py publish-approved --live  # actually posts
```

---

## Weekday niches

| Day | Niche | Accent |
|---|---|---|
| Monday | Nature & Environment | `#22C55E` |
| Tuesday | Psychology & Neuroscience | `#A855F7` |
| Wednesday | Health & Medicine | `#F43F5E` |
| Thursday | Physics & Space | `#38BDF8` |
| Friday | Wildcard — chosen by that week's engagement | `#FBBF24` |

Three visual themes ship: `neon` (default), `block`, `editorial`. Switch with
the `THEME` variable.

---

## Costs

| Thing | Cost |
|---|---|
| GitHub Actions on a public repo | $0, unlimited |
| GitHub Pages image hosting | $0 |
| Europe PMC / arXiv / Crossref | $0, no key needed |
| Anthropic API drafting | ~$3–10/month at 5 posts/week |
| Instagram Graph API | $0 |

---

## Samples

`samples/` contains five fully worked posts from real studies published
between 29 July and 12 August 2026, each passing the same automated lint the
pipeline enforces. Rebuild and re-render them:

```bash
python samples/build_samples.py
```

---

## Security

`.env` is gitignored and never committed. Credentials live in GitHub's
encrypted secret store, which stays private even on a public repository. The
Instagram token refreshes itself weekly and writes the new value back to
secrets automatically; see `src/auth.py`.
