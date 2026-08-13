# Submission copy

## Short version (~120 words)

**SEO is how you get found on Google without paying for ads** — and for most companies it's the
largest source of customers who arrive already looking for what you sell. The work is deciding what
to publish. Most articles never rank, so the expensive mistakes all happen *before* anyone writes:
targeting a search that documentation and Wikipedia already own, or writing a page that competes
with one you already published and drags both down.

Cairn is an agent that makes those decisions and refuses the bad ones. Every judgement it makes —
what your site covers, which searches it graded unwinnable and why, what you rejected — lives in
MongoDB Atlas and is checked *before* any model or web call runs.

Measured on plausible.io: **22,359 tokens on run 1, 2,802 on run 2.**

---

## Full version

### What SEO is, and why it's worth automating

SEO is getting your pages to show up on Google when someone searches for what you offer. It's the
largest acquisition channel for most software companies, because the traffic is free and the intent
is high — the person is already looking for you.

The common intuition is that SEO means writing lots of articles. It mostly doesn't. Most articles
never rank at all, and the reasons are decided long before anyone starts writing:

- **The search is already owned.** If official documentation and Wikipedia fill the first page, a
  marketing site will not displace them, however good the article is.
- **You're competing with yourself.** Publish two pages targeting one intent and they split their
  own authority and confuse the ranker. This is called *cannibalization*, it makes both pages worse,
  and AI content pipelines don't just miss it — they publish the collision.
- **You already rank and didn't notice.** Improving a page sitting at position 8 beats writing
  anything new.

So the valuable question isn't "write me an article." It's **"what should we write, and can we
actually win it?"** — and getting that wrong costs a full article's effort, which you don't discover
for three to six months.

### What cairn does

Point it at any domain. No login, no tracking script, no Search Console — it reads your public
`sitemap.xml`.

Four specialized Hermes agents propose topics, read live Google results, judge whether each is
realistically winnable, and write a content **brief**: target keyword, the angle that beats what
already ranks, a section outline, real internal links, and explicit do-not-cannibalize warnings.

The output is a brief, not a published article. The agent never touches your site — it has no
credentials and no code path that writes to one. Automating research is safe; automating publication
is where quality drift starts.

### How we use MongoDB

MongoDB isn't a log here. It's the component that **decides what the agent does next.**

Between proposing a topic and paying to research it, every candidate passes a **memory gate** —
three MongoDB checks that run before any model call or web request:

| check | index | the question it answers |
|---|---|---|
| **Vector search** on `pages` | `pages_vec` | Do we already cover this intent? |
| **Atlas Search** on `pages` | `pages_text` | Does this literally collide with an existing target keyword? |
| **Vector search** on `verdicts` | `verdicts_vec` | Did we already judge this search unwinnable, and why? |

Anything the gate stops costs **nothing** — no tokens, no web call — and every veto records the `_id`
of the MongoDB document responsible, so the agent's reasoning is auditable rather than magic.

**This is the difference between RAG and memory.** A RAG app retrieves documents and stuffs them into
a prompt. Cairn retrieves and then *refuses to run the next stage*. Retrieval output here is control
flow.

Six capabilities, each load-bearing:

- **Automated Embedding (`autoEmbed`)** — documents carry text; Atlas generates and maintains the
  vectors server-side using Voyage. No embedding pipeline, no second API key, and query and document
  can never drift apart.
- **Vector Search ×3** — three indexes for three genuinely different veto decisions, not one index
  called "memory."
- **Atlas Search** — the lexical half of cannibalization detection. Vector catches same-intent /
  different-words; keyword catches literal collision. Either alone misses half the cases.
- **Change streams** — a watcher on `verdicts` pushes each winnable result to the brief stage the
  instant it's inserted, so briefing begins while grading is still running.
- **Atomic `$inc`** — learned-rule confidence is a database write, so it's auditable and can't
  inflate itself.
- **Aggregation** — the headline metric: share of candidates resolved from memory, run over run.

**State and memory are deliberately separate.** `runs` is state — a stage cursor, disposable once the
run ends. `pages`, `verdicts`, `rules`, and `briefs` are memory — cross-run, semantic, permanent. A
crash loses none of the run; deleting the run loses none of the learning.

Every document carries a `site` field and all three vector indexes declare `site` as a filter, so one
install tracks any number of domains with fully isolated memory.

### How Hermes is used

Four `AIAgent` roles — scout, grader, briefer, rulemaker — each with its own system prompt, toolset,
and model tier: a cheap model for high-volume SERP grading, a stronger one for judgement.

The central choice is what we turned **off**: `skip_memory=True`, `skip_context_files=True`. We
replaced Hermes's private, local, single-session memory with a shared, queryable, cross-run memory in
MongoDB. Hermes supplies reasoning and live web tools; everything it durably learns lives in Atlas,
where the next run — on any machine — can query it.

Conversations persist to `runs.trajectories` and replay via `conversation_history=`, so a resumed
agent continues mid-conversation instead of restarting cold.

### Results, measured

| run | considered | resolved from memory | live searches paid for | tokens |
|---|---|---|---|---|
| 1 (cold) | 10 | 9/10 | 1 | 22,359 |
| 2 (warm) | 10 | **10/10** | **0** | **2,802** |

87% fewer tokens. Even the cold run stopped 9 of 10 — the crawl fills the page inventory before the
gate runs, so cannibalization detection pays for itself on the very first run.

Crash recovery was verified with a real `kill -9` mid-grading: on resume it skipped the verdicts
already in MongoDB, graded only what remained, recovered a winner that had been graded but not yet
briefed, and finished with no duplicated work.

A later run produced 6 briefs containing **29 internal links, every one verified to exist** in the
crawled inventory. Every AI writing tool hallucinates these; cairn's come out of a vector search over
the site's own pages, so they can't be invented.
