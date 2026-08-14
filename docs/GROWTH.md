# Cadence and growth plan

Five posts a week, indefinitely, without burning out or drifting into hype.

---

## The one number that matters

Instagram's most heavily weighted distribution signal right now is **sends —
shares into DMs**. Not likes. Not follows. Someone seeing your post and
thinking *"I have to send this to Marco."*

Saves are second. Comments third. Likes are close to noise.

This changes what a post is for. You are not writing to be agreed with. You
are writing to be **forwarded**. Every design and copy decision in this build
follows from that:

- The cover slide states a finding, not a topic, because you cannot forward a
  topic.
- The fine-print slide exists partly because "here's the catch" is the single
  most forwardable slide in a science carousel. It is what makes someone send
  it to the friend who is sceptical.
- The caption ends with a question answerable in four words, because a
  four-word question gets answered and a paragraph-long one gets scrolled.

**Track sends and saves. Ignore likes.** `publish.insights()` already pulls
`saved`, `shares`, `comments`, `reach` — and Friday's niche is chosen by
saves+shares weighted 3× against likes at 1×. Your best-performing field
literally decides what you post on Friday.

---

## The weekly rhythm

| Day | Niche | Accent | Post at |
|---|---|---|---|
| Monday | Nature & Environment | green | 07:00 |
| Tuesday | Psychology & Neuroscience | purple | 07:00 |
| Wednesday | Health & Medicine | red | 12:00 |
| Thursday | Physics & Space | blue | 09:00 |
| Friday | Wildcard — best field of the week | gold | 09:00 |

Wednesday noon and Thursday morning are the two highest-engagement slots in
large-scale post analyses, so the two niches with the broadest appeal (health,
space) sit there. Adjust once you have 30 days of your own data — your
audience beats any general benchmark.

The workflow fires at 11:00 UTC, which is 06:00 Chicago. That gives you an
hour to review before the 07:00 slot. To change it, edit the `cron` line in
`.github/workflows/daily-draft.yml`.

### Your actual daily time cost

- **6–8 minutes.** Open the issue, skim the slides against the abstract,
  comment `approve`.
- Some days you will edit a headline. That is another two minutes.
- Some days nothing clears vetting. That is a zero-minute day, and it is the
  system working, not failing.

### The Sunday habit (20 minutes)

1. Open Instagram Insights. Write down sends and saves for the week's five
   posts.
2. Ask one question: *which cover slide made people forward it?* Pattern-match
   the phrasing, not the topic.
3. Reply to every comment from the week. Every single one. Comment replies are
   a ranking signal and, more importantly, they are how the first 500 followers
   decide whether there is a person behind the account.

---

## Month one — the only goal is proof of consistency

Do not chase followers in month one. Chase 20 published posts with zero
factual corrections. That reputation is the entire moat of this account — any
one can post science summaries, almost nobody posts ones you can trust.

**Week 1.** Publish Monday to Friday. Do not touch anything else. You are
finding out whether the pipeline suits your taste, and whether the voice feels
like yours. Edit headlines freely in the review step; that is what it is for.

**Week 2.** Add the first-comment habit: right after publishing, comment on
your own post with the single most interesting detail that did not fit on a
slide. It gives early engagement, and it gives people something to reply to.

**Week 3.** Start replying in other people's comment sections — not promoting,
just answering questions correctly. Find five accounts in your niches that post
science and have 5k–50k followers. Be the person in their comments who actually
read the paper. This is the highest-yield unpaid growth activity available to a
science account and almost nobody does it because it is slow.

**Week 4.** Post your first "we were wrong" post if anything warrants it. If a
study you covered gets contradicted, corrected, or fails to replicate, make a
carousel about it. Nothing else you can post builds trust as fast.

---

## Month two and three — leverage

**Repost the fine print.** Take the caveat slide from a post that did well and
make it a standalone single image: *"Three ways to read a study badly."* These
travel further than the original.

**Build the Friday franchise.** Friday is your only slot with editorial
freedom. Make it a recognisable thing: the weirdest, most forwardable study of
the week. That is the post people will follow you for.

**Answer the same question twice.** When a comment asks something good, the
answer is a post. You have a pipeline that can render a carousel in seconds —
use it for replies, not just for the daily.

**Add Reels, from the same source material.** The renderer already produces
your slides; a 20-second talking-over-slides Reel from the same post costs
almost nothing and reaches a different distribution surface. Carousels win on
engagement per impression; Reels win on impressions. You want both.

---

## What to write in the caption's first line

Since hashtag-following was removed, discovery has shifted toward keywords in
captions, profile name, and bio. Instagram's own guidance is now 3–5 relevant
hashtags, which is why `config/hashtags.yaml` defaults to 5.

Practical consequences:

- **Your profile name field is a search field.** Not `@onestudytoday` — make it
  `One Study Today · science studies explained`. That field is indexed.
- **The first line of every caption should contain the plain words someone
  would type into the search box.** "GLP-1 pill weight loss study" beats "the
  interesting number here is not 12.1%" for discovery — so `copy_spec.yaml`
  requires the caption to open by restating the hook in plain language.
- Do not hide hashtags in the first comment. It does nothing and it splits your
  keyword signal.

---

## Milestones and what unlocks at each

| Followers | What changes |
|---|---|
| **100** | Nothing algorithmically. But you now have people who will notice if you stop. |
| **1,000** | Link stickers in Stories become genuinely useful; you can start driving people to the bio page directly. |
| **5,000** | You start showing up in "suggested accounts" for science accounts. Growth stops being linear. |
| **10,000** | Brand and publisher outreach starts. Say no to anything that wants copy approval. |

Realistic pace on 5 posts a week with genuine effort on comments: 1,000 in
three to five months, 10,000 in twelve to eighteen. Anyone promising faster is
selling something.

---

## The rules that protect the account

1. **Never post a study you have not opened.** The pipeline gives you the link
   in the review issue. Open it.
2. **Correct publicly and fast.** A pinned correction comment within an hour
   costs you nothing. A silent deletion costs you everything.
3. **Never remove the fine-print slide** to make a post punchier. The renderer
   will let you. Do not.
4. **Never post a preprint without the badge.** The code enforces this. Do not
   go looking for the switch.
5. **When a study contradicts something you posted, post the contradiction.**
   That is the whole brand.

---

## Six-week checkpoint

Look at your five best posts by sends. Ask:

- Are they concentrated in one niche? Consider dropping the weakest weekday and
  doubling the strongest.
- Are they all one *shape* of finding — counterintuitive results, big numbers,
  "we were wrong about X"? Bias sourcing toward that shape by editing the
  `europepmc_query` terms in `config/niches.yaml`.
- Is the caveat slide the most screenshotted? If so, lead with the caveat
  sometimes. "Everyone shared this study. Here's what it doesn't say" is a
  format, and it is yours.

Sources: [Buffer — Instagram algorithm 2026](https://buffer.com/resources/instagram-algorithms/)
