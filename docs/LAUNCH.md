# Launch plan — zero to the first hundred

[`GROWTH.md`](GROWTH.md) assumes posts are already going out and engagement
data is coming back. This is the ten days before that is true.

---

## What actually happens to a brand-new account

There is no shadowban on new accounts and no probation period. The problem is
duller than that: Instagram ranks by predicting how you will respond to a post,
and on day one it has nothing to predict from. No follower graph, no interest
signal, no engagement history on the account, no history on the *format*.
Post #1 does not get suppressed. It gets shown to almost nobody because there
is nobody the system has any reason to show it to.

That asymmetry reverses fast, and this is the part worth internalising: small
accounts are the ones that grow. In Metricool's June 2026 analysis of 24
million posts across 375,000 accounts, **10.13% of accounts under 2,000
followers moved up a follower bracket in the year, against 1.34% of accounts
between 100k and 1M**. Accounts under 10k also pull 8–15% organic reach rates,
roughly three to four times what accounts over 100k get. The cold start is a
week or two of near-silence, not a structural disadvantage.

### Honest numbers

| Milestone | Realistic | If it goes well |
|---|---|---|
| Post #1 reach | 20–80 accounts | 200 |
| Followers, end of week 1 | 5–25 | 60 |
| Followers, end of month 1 | 40–150 | 300 |
| First 100 followers | week 3–8 | week 2 |
| First 1,000 | month 3–5 (as GROWTH.md) | month 2 |

A post that reaches 40 people is not a failed post in week one. It is the
system having no information. The failure mode to actually watch for is
**zero profile visits across five consecutive posts** — that means the cover
slides are not making anyone curious, which is a copy problem you can fix, not
a distribution problem you have to wait out.

---

## Before post #1

`RUNBOOK.md` covers the repo, the secrets, the token and the bio link. This is
everything else, and all of it is on the Instagram side.

**Account type.** It must be a Professional account for the Graph API to
publish at all, which it already is. Leave it on Business. Creator accounts
unlock Broadcast Channels, but those need roughly 10k followers in practice —
not a decision you have to make for months.

**Turn on search indexing.** Since 10 July 2025, public professional accounts
belonging to adults are indexed by Google — photos, carousels, Reels, the lot.
It is on by default; verify it, because it is the single highest-leverage
setting for an account whose whole product is plain-language answers to
questions people type into search boxes. Settings → Privacy → *Allow public
photos and videos to appear in search engine results*.

**Write the bio around the guardrails, not the topic.** "Science studies
explained" describes a thousand accounts. The differentiator is in the
`README.md` table — the account is *architecturally incapable* of posting a
retracted paper, a predatory-publisher paper, or a preprint without a badge.
Say that:

```
One real study every weekday. Peer-reviewed sources.
Sample size on the slide. Preprints labelled.
Every paper linked below ↓
```

Three lines, each of which is a promise a competitor cannot copy without
building the same pipeline. The link goes to the GitHub Pages index, which
already lists every paper with a direct link — that page *is* the proof.

**Profile picture.** It renders at about 32px in a comment thread, which is
where most of your first followers will first see it. Test it at that size. A
single glyph or a two-letter mark survives; a detailed illustration does not.

**Do not mass-follow on day one.** Following 200 accounts in an hour from a
day-old account is the one behaviour that genuinely triggers rate limits. Ten
to twenty a day, only accounts you actually intend to read.

---

## Bank posts before you launch. Do not drip.

`RUNBOOK.md` Part 6 says a day where nothing clears vetting is normal — it is
the system working. That is fine on week 40 and fatal on day 1. Do not let the
launch date depend on a same-morning sourcing run.

```bash
python src/pipeline.py run --niche health   --limit 2
python src/pipeline.py run --niche psych    --limit 2
python src/pipeline.py run --niche physics  --limit 2
```

Approve five before you publish any. `samples/posts/` already holds five
vetted, rendered posts from real papers if you want a floor under this.

**Then publish three in the first 48 hours, not one.** This is the one place
worth breaking the Monday-to-Friday rhythm. Someone who arrives from your
comment on another account and finds a single post does not follow — there is
nothing to indicate the account will still exist in a month. Three posts and a
bio that promises a weekday cadence is a different proposition. Go to the
normal five-a-week schedule from day 3.

The volume is already unusual and worth knowing: the average Instagram account
posted **1.30 times a week** in 2026. Five puts you in a very small minority
before you have a single follower.

---

## The 72-hour rule

Roughly **76% of the lifetime views of a feed post land in the first 72 hours**
(65% for Reels). There is no slow burn to wait for. Whatever you are going to
do to help a post, you do it inside three days, and the first of those days
does most of the work.

This is what makes the launch week a real week of work rather than five
approvals.

---

## The week before post #1 — build the comment presence first

GROWTH.md puts "reply in other people's comment sections" in week 3. **For a
zero-follower account, invert that: it is week minus one.** Commenting is the
only distribution surface that does not require the algorithm to have an
opinion about you. It works identically at 0 followers and at 50,000.

Seven days before launch:

1. Pick **twelve accounts**, three per weekday niche, in the 5k–50k range.
   Not the mega-accounts — their comment sections are unreadable and your
   comment sinks in ninety seconds. 5k–50k is where a comment stays visible
   for a day and where the account owner reads it.
2. Turn on post notifications for all twelve. Being in the first ten comments
   is most of the value.
3. Comment on three to five posts a day. **One rule: the comment must contain
   a fact the post did not.** The sample size. The design. What the control
   group got. The effect size in absolute terms. You are not promoting; you
   are visibly being the person in the thread who read the paper. That is the
   entire product, demonstrated for free.
4. Never write "great post", never write "check out my page", never drop a
   link. A profile visit you earn converts; one you ask for does not.

By launch day this should have produced somewhere between 5 and 40 profile
visits and a handful of follows from people who found you before you had
anything to show. That is your seed audience, and it is made of exactly the
right people.

---

## Launch day

| When | Do |
|---|---|
| Night before | Approve the post. Confirm images load at the Pages URL. |
| Post time | Publish through the normal `approve` flow. |
| +2 min | First comment: the most interesting detail that did not fit on a slide. This is GROWTH.md's week-2 habit — start it on day 1 instead. |
| +5 min | Send the post by DM to 15–25 real people, individually. See below. |
| +30 min | Do that day's twelve-account commenting round. Your name is now attached to an account with posts on it. |
| +2 h, +6 h, +24 h | Reply to every comment. All of them, within minutes. |
| +48 h | Post #2 and #3 out. |

### The DM send, which is the only legitimate seeding there is

Sends are the most heavily weighted distribution signal on the platform.
Fifteen to twenty-five people you actually know, messaged **individually**,
each with one line about why this specific study is for them, is not a growth
hack — it is genuine sends from genuine accounts, which is precisely the
behaviour the ranking system is built to reward.

It is also the only version of this that works. A group chat blast, a "please
engage" message, or the same copy-pasted line to thirty people produces sends
from accounts with no interest in science, which teaches the interest graph
that your content belongs in front of people who ignore it. That is worse than
doing nothing.

Do this for post #1, #2 and #3. Then stop. It does not scale and it is not
supposed to.

---

## Reels from day one, not month two

GROWTH.md defers Reels to months two and three. That ordering is correct for
an account with an audience and wrong for one without.

Below 50k followers, Reels are the highest-reach format on the platform, and
the advantage is largest at the bottom:

| Account size | Reels reach rate | Carousel |
|---|---|---|
| 1k–5k | **9.78%** | lower |
| 5k–10k | 7.55% | lower |
| 10k–50k | 7.10% | lower |
| 50k+ | 5.60% | **5.85–6.00%** |

Across 10,000 feed posts in the first half of 2026, Reels pulled about 1.36×
the reach of carousels and 2.25× that of single images, with the effect
concentrated in accounts under 50k. Carousels still win on *depth* — 1.38%
engagement rate against 1.23%, and nine times the saves of a single image —
which is exactly why the carousel stays the product.

So: **the carousel is the thing. The Reel is the trailer.** Twenty to forty
seconds, the cover slide's finding read aloud over the slides the renderer
already produced, ending on the fine-print slide. Thirty to sixty seconds is
the highest-reach band; past two minutes reach falls off hard.

One a week is enough at the start, and it belongs on **Friday**, on top of the
wildcard post. Friday is already the slot with editorial freedom and the most
forwardable study of the week; it is the obvious franchise to build a
recognisable weekly video around.

---

## Where the first hundred actually come from

Ranked by yield per hour, for this account specifically:

1. **Comments on 5k–50k science accounts.** Slow, unglamorous, works at zero
   followers, and almost nobody does it. Highest yield by a distance.
2. **The Friday Reel.** The only format that reaches non-followers in volume
   this early.
3. **Search.** Instagram search plus Google indexing. The caption's first line
   is already forced into plain searchable language by `copy_spec.yaml`; this
   is the payoff. It compounds and it is the only channel that keeps working
   on posts from six months ago.
4. **DM sends**, for the first three posts only.
5. **The five niche hashtags.** `hashtags.yaml` already explains why the
   under-50k-post niche tags are the only tier that matters at your size —
   they are where a standing-start account can genuinely hit top posts.
6. **Collab posts**, once you have something to offer a partner. Not week one.

---

## Off-platform seeding, and what it is actually worth

Worth an hour, total, in launch week. Not more.

**Bluesky.** The real science community migrated there and stayed. The
astronomy ecosystem alone runs seventeen curated feeds serving around 92,000
accounts a week from 1,836 registered posters as of January 2026, with ESA,
ESO and the AAS posting into them. Post the cover-slide finding plus the DOI
link. It costs ninety seconds, the audience is researchers, and researchers
are the people whose approval makes a science account credible.

**Reddit — carefully.** r/science accepts only links to published
peer-reviewed research and removes self-promotion on sight. r/EverythingScience
is looser. The site-wide rule is 90/10: nine parts genuine participation to one
part anything of your own, and several science subs run closer to 99/1.
Posting your Instagram link will get you removed and possibly shadowbanned.

What works instead is the same move as the Instagram comments: find threads
about a paper you have covered and post the thing you know — the sample size,
the design, the caveat — with the DOI. No link to the account. Some fraction of
people who read a comment like that go looking for who wrote it. Treat Reddit
as a place to be useful, not a channel.

**TikTok and YouTube Shorts — not yet.** Each is its own cold start with its
own algorithm to teach, and the account's differentiator, the fine print, is
weakest in a fifteen-second vertical video. Revisit in month two, once the
Friday Reel is a routine.

**Threads — yes, it is nearly free.** Same login, same account, text-native,
and the fine-print line from each carousel is a complete Threads post on its
own. Budget five minutes a day.

If you do cross-post video later: export a clean master from the editor and
upload it natively to each platform. Never download from one platform and
re-upload to another. Instagram suppresses TikTok watermarks, YouTube bans
them outright, and TikTok audio is not licensed to Meta, so a Reel carrying a
TikTok sound gets limited distribution.

---

## What will get the account penalised

**Bought followers or likes.** Obvious, and worth saying anyway: it destroys
the ratio that determines your reach, permanently, and the followers never
engage.

**Engagement pods.** Be clear-eyed about these. Instagram does not publish a
rule saying every pod member gets actioned, but large, repetitive or automated
pods look like coordinated inauthentic activity, which is prohibited, and
enforcement is not appealable in practice. Set the policy risk aside and the
mechanism still fails you: pod engagement comes from accounts with no interest
in science, and modern ranking runs on an interest graph. You would be
spending real effort teaching the recommender to show your carousels to people
who will scroll past them. **Do not join one.**

**Follow/unfollow, and any tool that wants your password.** Instagram's
automation enforcement is aggressive and the account is publishing through the
Graph API — an action block or suspension takes the whole pipeline down with
it.

**Reposting other people's content.** Since May 2026, accounts that regularly
post unoriginal content are excluded from recommendations entirely. Everything
this pipeline renders is original by construction. Do not compromise that to
fill a slow day.

**Resharing your own feed post to Stories expecting reach.** Mosseri, 6 April
2026: *"you can definitely share your own post to your Stories… but it's not
going to meaningfully change your reach overall, because Feed generally gets
more reach than Stories anyway."* Stories reach followers you already have.
At zero followers they reach zero people. Skip them entirely for the first
month.

**Deleting posts that underperformed.** Tempting on a nine-post grid. Do not.
The archive is the moat — the whole pitch is that every paper you have ever
covered is linked and checkable. A gap in it is worth more to you than a
tidier grid, and since June 2026 you can reorder the grid anyway. Put the
three best posts in the top row and leave everything up.

---

## What to measure in week one

Not followers. At this reach, follower count is mostly noise.

Measure **follows ÷ profile visits**. It is the one number that is fully
within your control at tiny sample sizes, because it does not depend on
distribution at all — it measures whether the bio and the top row of the grid
convert a curious visitor. If ten people visit and one follows, the profile is
working and you have a distribution problem that time and comments will fix.
If forty visit and none follow, rewrite the bio and reorder the grid before
you post anything else.

Second: **profile visits per post**. That is the cover slide doing its job or
not doing it.

Sends and saves — GROWTH.md's real metrics — need volume before they mean
anything. Start reading them in week three.

---

## Handing over to GROWTH.md

You are done with this document when all of the following are true:

- 10 posts published, zero corrections outstanding
- 100+ followers
- the twelve-account commenting round is a habit, not a task
- one Friday Reel has gone out
- at least one post has been sent by someone you do not know

Then GROWTH.md's month one is already half done, and the rest of it —
the Sunday insights habit, the "we were wrong" post, the fine-print reposts,
the Friday franchise — is what the next eight weeks are for.

Sources:
[Metricool — 2026 Instagram study, 24M posts](https://metricool.com/important-instagram-statistics/) ·
[Socialinsider — Instagram Reels statistics 2026, 140k Reels](https://www.socialinsider.io/blog/instagram-reels-statistics/) ·
[CollabKit — Reels vs carousels vs images, 10k posts](https://collabkit.me/blog/instagram-reels-vs-carousels-vs-images-data-study-2026) ·
[Outfame — organic reach statistics 2026](https://www.outfame.com/blog/instagram-organic-reach-statistics) ·
[Social Media Today — Mosseri debunks the Stories-reshare hack, April 2026](https://www.socialmediatoday.com/news/instagram-chief-debunks-popular-engagement-hack/816781/) ·
[HeyOrca — 2026 Instagram changelog](https://www.heyorca.com/blog/instagram-social-news) ·
[Metricool — Instagram indexing on Google](https://metricool.com/instagram-indexing-on-google/) ·
[Buffer — Instagram algorithm 2026](https://buffer.com/resources/instagram-algorithms/) ·
[Hypeflare — engagement pods in 2026](https://hypeflare.co/resources/instagram-engagement-pods/) ·
[Redship — Reddit self-promotion rules 2026](https://redship.io/blog/reddit-self-promotion-rules) ·
[Springer Nature — Reddit 101 for scientists](https://www.springernature.com/gp/researchers/the-source/blog/blogposts-communicating-research/reddit-101-for-scientists/16614004) ·
[The Astrosky Ecosystem, arXiv 2601.16838](https://arxiv.org/html/2601.16838v1) ·
[Socialync — cross-posting without duplication penalties, 2026](https://www.socialync.io/blog/avoid-content-duplication-penalties-cross-posting-2026)
