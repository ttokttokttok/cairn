# 60-second demo script

**Rule: the first 30 seconds must show the thing working, not explain it.** Judges watch
asynchronously and bail early. No title card, no architecture diagram, no "hi we're team X."

## Before you hit record (2 min)

```bash
cd ~/Desktop/seo_agent
# big font, ~100x30 window, clear scrollback
printf '\033c'
```

Rehearse **beat 1 once** so you know its timing, then `cairn reset` nothing — the warm memory is
the point.

---

## Beat 1 · 0:00–0:12 — the hook

**Type:**
```bash
uv run cairn run plausible.io --candidates 10
```

**What lands on screen:** `WARM START`, then a table of candidate topics with red ✗ marks, each row
naming a MongoDB document, then `N vetoed by memory before spending anything`.

**Say:**
> "This is an SEO agent. It just proposed ten topics — and killed almost all of them before spending
> a single token. Every red row is MongoDB saying: we already cover this, or we already judged it
> unwinnable. That's a database query, not a model call."

**Why this opens the video:** it's ~6 seconds of runtime, it's visually obvious, and it shows the
thesis instead of describing it.

---

## Beat 2 · 0:12–0:24 — the number

**Type:**
```bash
uv run cairn stats plausible.io
```

**Say:**
> "First run cost twenty-two thousand tokens. Second run, two thousand eight hundred. Same site,
> same ten candidates — the difference is memory. Ten out of ten resolved from MongoDB, zero live
> searches paid for."

Point at the `resolved from memory` column going up.

---

## Beat 3 · 0:24–0:40 — crash recovery *(cut this first if you're short)*

**Two terminals.** Left runs it, right kills it.

Left:
```bash
CAIRN_VERDICT_WORKERS=1 uv run cairn run umami.is --candidates 8
```
Right, once you see verdicts printing:
```bash
pkill -9 -f "cairn run umami.is"
```
Left:
```bash
uv run cairn run umami.is --resume <run-id>
```

**Say:**
> "Kill it mid-flight with SIGKILL. On resume it skips the verdicts already in MongoDB, grades only
> what's left, and recovers a winner that was graded but never briefed. No duplicated work."

---

## Beat 4 · 0:40–0:57 — the deliverable

**Type:**
```bash
uv run cairn report plausible.io
```

Browser opens. **Scroll straight to the briefs.**

**Say:**
> "And here's what it produces — briefs, not published articles. Target keyword, the angle that
> beats what already ranks, a full outline. These internal links came out of a vector search over
> the site's own pages, so unlike every AI writing tool, they can't be hallucinated. Twenty-nine
> links, all real."

---

## Beat 5 · 0:57–1:00 — close

**Say:**
> "Four Hermes agents, and MongoDB is their only memory. Every run makes the next one cheaper."

---

## If a judge asks what's under it

- Atlas **Automated Embedding** — text in, Atlas generates and maintains the vectors, no pipeline
- **Three vector indexes** for three different veto decisions
- **Atlas Search** alongside vector — cannibalization needs both literal and semantic signals
- **Change streams** — briefing starts the instant a verdict lands, not when the batch finishes
- Hermes with `skip_memory=True` — we removed its local memory so MongoDB is the only one

## Don't

- Don't run a cold crawl on camera — it's ~60s of nothing
- Don't open the code
- Don't explain SEO
- Don't say "as you can see"
