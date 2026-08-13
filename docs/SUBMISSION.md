# Submission copy

## Short version (~100 words, for a constrained field)

**Every agent starts from nothing — and in SEO that's expensive.** AI SEO tools are stateless: point
one at your site twice and it redoes the same research, proposes topics you already published, and
recommends articles that compete with your own pages. The costly mistakes happen *before* anyone
writes a word.

Cairn stores every SERP verdict, duplicate collision, and human rejection in MongoDB, then uses that
memory to **veto work before paying for it**. Retrieval here is control flow, not prompt filler — a
hit doesn't get summarized into a prompt, it stops the stage.

Measured on plausible.io: **22,359 tokens on run 1, 2,802 on run 2.** Ten of ten candidates resolved
from memory, zero live searches paid for.

---

## Full version

### The problem

Every AI agent starts from nothing. For SEO that isn't just wasteful, it's actively harmful.

Point a stateless AI SEO tool at your site twice and it will: redo the same expensive research,
propose topics you published three months ago, and — worst of all — recommend an article that
competes with a page you already own. That last one is called cannibalization, and it makes *both*
pages rank worse. AI content pipelines don't catch it. They publish the collision.

The expensive mistakes in SEO all happen **before** anyone writes a word: choosing a query that
official documentation and Wikipedia already own, or one you're unknowingly already ranking for.
Getting that wrong costs an entire article's effort, and you don't find out for three months.

### What we built

Cairn is an SEO agent whose memory lives in MongoDB Atlas and whose job is to **refuse work**.

Four specialized Hermes agents propose topics, read live search results, and write content briefs.
Between proposing and paying, every candidate passes a **memory gate**: three MongoDB checks that
run before any model call or web request.

- **Vector search on `pages`** — do we already cover this intent?
- **Atlas Search on `pages`** — does this literally collide with an existing target keyword?
- **Vector search on `verdicts`** — did we already judge this query unwinnable, and why?

Anything the gate stops costs nothing, and each veto names the exact MongoDB document responsible —
so the agent's reasoning is auditable, not magic.

**This is the difference between RAG and memory.** A RAG app retrieves documents and stuffs them
into a prompt. Cairn retrieves and then refuses to run the next stage. Retrieval output is *control
flow*.

### Results, measured

| run | considered | resolved from memory | live searches paid for | tokens |
|---|---|---|---|---|
| 1 (cold) | 10 | 9/10 | 1 | 22,359 |
| 2 (warm) | 10 | **10/10** | **0** | **2,802** |

87% fewer tokens. Even the cold run stopped 9 of 10, because the crawl fills the page inventory
before the gate runs — cannibalization detection pays for itself on the first run.

### How MongoDB is used

Six capabilities, each load-bearing:

- **Automated Embedding** (`autoEmbed`) — documents carry text; Atlas generates and maintains the
  vectors server-side. No embedding pipeline, no second API key.
- **Vector Search ×3** — three indexes serving three genuinely different veto decisions.
- **Atlas Search** — the lexical half of cannibalization detection. Vector catches same-intent,
  different-words; keyword catches literal collision. Either alone misses half the cases.
- **Change streams** — a watcher on `verdicts` sends each WINNABLE result to the brief stage the
  instant it lands, so briefing starts while grading is still running.
- **Atomic `$inc`** — learned-rule confidence is a database write, so it's auditable and can't
  hallucinate itself upward.
- **Aggregation** — the headline metric: share of candidates resolved from memory, run over run.

**State and memory are deliberately separate.** `runs` is state — a stage cursor, disposable. Everything
else is memory — cross-run, semantic, permanent. A crash loses none of the run; deleting the run
loses none of the learning.

### How Hermes is used

Four `AIAgent` roles — scout, grader, briefer, rulemaker — each with its own system prompt, toolset,
and model tier (cheap for high-volume SERP grading, strong for judgement).

The central choice is what we turned **off**: `skip_memory=True`, `skip_context_files=True`. We
replaced Hermes's private, local, single-session memory with a shared, queryable, cross-run memory in
MongoDB. Hermes supplies reasoning and live web tools; everything it durably learns lives in Atlas,
where the next run — on any machine — can query it.

Conversations persist to `runs.trajectories` and replay via `conversation_history=`, so a resumed
agent continues mid-conversation rather than restarting cold.

### Crash recovery, verified with `kill -9`

Killed mid-grading with 3 of 8 verdicts done. On resume it skipped the 3 already in MongoDB,
graded only the remaining 5, recovered a winner that had been graded but not yet briefed, and
finished with 8 verdict documents across 8 distinct queries — no duplicated work.

### Deliberate boundaries

The output is a **brief, not a published article**. The agent never touches your site: it has no
credentials and no code path that writes to it. Automating research is safe; automating publication
is where quality drift starts. The human gate before publication is a design position, not an
unfinished edge.

Works on any domain with zero auth — it reads your public `sitemap.xml`. One install tracks any
number of sites, each with fully isolated memory.
