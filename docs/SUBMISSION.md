# Submission copy

## Short version (~110 words)

We built a **Hermes multi-agent system for SEO** whose memory lives in MongoDB Atlas.

Four specialized Hermes agents — scout, grader, briefer, rulemaker — propose topics, read live
SERPs, judge difficulty, and write content briefs. Between proposing a topic and paying to research
it, every candidate passes a **memory gate**: three MongoDB checks that run before any model call or
web request. Already covered? Keyword collision with an existing page? Already graded unwinnable?
Stopped, for free, with the exact document that stopped it recorded.

Retrieval here is control flow, not prompt filler — a hit doesn't get summarized into a prompt, it
stops the stage.

Measured on plausible.io: **22,359 tokens on run 1, 2,802 on run 2.** 10 of 10 candidates resolved
from memory, zero live SERP reads paid for.

---

## Full version

### What we built

A **Hermes multi-agent system for SEO** with its memory in MongoDB Atlas.

Point it at any domain — no auth, no Search Console, it reads the public `sitemap.xml`. Four
specialized Hermes agents then run a pipeline:

| agent | job | tools | model tier |
|---|---|---|---|
| **scout** | propose candidate topics from the site's inventory + live research | web | strong |
| **grader** ×N | read the live SERP, grade WINNABLE / CONTESTED / UNWINNABLE | web | cheap |
| **briefer** | write the content brief for winners | web | strong |
| **rulemaker** | induce reusable rules from the run's outcomes | none | strong |

Graders fan out across a thread pool with a fresh `AIAgent` per thread. Output is a **brief, not a
published article** — the agent has no credentials to your site and no code path that writes to one.

### The problem it solves

A stateless SEO agent run twice redoes the same expensive research, proposes topics you already
published, and recommends articles that compete with your own pages. That last one — cannibalization
— makes both pages rank worse, and AI content pipelines don't catch it; they publish the collision.

The expensive mistakes happen *before* anyone writes. So the agent's most valuable behaviour is
refusing work.

### How we use MongoDB

MongoDB isn't a log here. It's the component that **decides what the agent does next.**

Between proposing a topic and paying for it, every candidate passes a **memory gate** — three
MongoDB checks, zero API calls:

| check | index | question |
|---|---|---|
| **Vector search** on `pages` | `pages_vec` | do we already cover this intent? |
| **Atlas Search** on `pages` | `pages_text` | literal collision with an existing target keyword? |
| **Vector search** on `verdicts` | `verdicts_vec` | already graded unwinnable, and why? |

Anything stopped costs nothing, and every veto records the `_id` of the document responsible — so the
reasoning is auditable rather than magic.

**This is the difference between RAG and memory.** A RAG app retrieves and stuffs the result into a
prompt. Cairn retrieves and then *refuses to run the next stage*.

Six capabilities, each load-bearing:

- **Automated Embedding (`autoEmbed`)** — documents carry text; Atlas generates and maintains the
  vectors server-side with Voyage. No embedding pipeline, no second API key, and query and document
  can't drift apart.
- **Vector Search ×3** — three indexes for three genuinely different veto decisions, not one index
  called "memory."
- **Atlas Search** — the lexical half of cannibalization detection. Vector catches same-intent /
  different-words; keyword catches literal collision. Either alone misses half the cases.
- **Change streams** — a watcher on `verdicts` pushes each WINNABLE result to the brief stage the
  instant it's inserted, so briefing runs while grading is still going.
- **Atomic `$inc`** — learned-rule confidence is a database write, so it's auditable and can't
  inflate itself.
- **Aggregation** — the headline metric: share of candidates resolved from memory, run over run.

**State and memory are deliberately separate.** `runs` is state — a stage cursor, disposable. `pages`,
`verdicts`, `rules`, `briefs` are memory — cross-run, semantic, permanent. A crash loses none of the
run; deleting the run loses none of the learning.

Every document carries a `site` field and all three vector indexes declare `site` as a filter, so one
install tracks any number of domains with fully isolated memory.

### How we use Hermes

Four `AIAgent` roles, each with its own system prompt, toolset, and model tier — cheap for
high-volume SERP grading, strong for judgement.

The central choice is what we turned **off**: `skip_memory=True`, `skip_context_files=True`. We
replaced Hermes's private, local, single-session memory with a shared, queryable, cross-run memory in
MongoDB. Hermes supplies reasoning and live web tools; everything it durably learns lives in Atlas,
where the next run — on any machine — can query it.

Conversations persist to `runs.trajectories` and replay via `conversation_history=`, so a resumed
agent continues mid-conversation instead of restarting cold.

### Results, measured

| run | considered | resolved from memory | live SERP reads paid for | tokens |
|---|---|---|---|---|
| 1 (cold) | 10 | 9/10 | 1 | 22,359 |
| 2 (warm) | 10 | **10/10** | **0** | **2,802** |

87% fewer tokens. Even the cold run stopped 9 of 10 — the crawl fills the page inventory before the
gate runs, so cannibalization detection pays for itself on the first run.

**Crash recovery verified with a real `kill -9`** mid-grading: on resume it skipped the verdicts
already in MongoDB, graded only what remained, recovered a winner that had been graded but not yet
briefed, and finished with no duplicated work.

A later run produced 6 briefs containing **29 internal links, every one verified to exist** in the
crawled inventory. Every AI writing tool hallucinates these; cairn's come from a vector search over
the site's own pages, so they can't be invented.
