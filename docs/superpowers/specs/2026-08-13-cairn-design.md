# Cairn — design

*A cairn is stacked stones marking a trail so the next traveler doesn't re-explore the dead ends.*

An autonomous SEO agent that gets cheaper and sharper every run, because every SERP verdict,
duplicate collision, and human rejection is stored in MongoDB as memory that **vetoes work before
it is paid for**.

Built for the MongoDB Persistent Context Sprint Hackathon, 2026-08-13.

## Thesis

Retrieval output is **control flow, not prompt filler**. A RAG app retrieves documents and stuffs
them into a prompt. Cairn retrieves and then *refuses to run the next stage*.

The load-bearing claim: the same candidate topic costs ~2000 tokens to evaluate on run 1 and
~0 tokens on run 2, because the answer is already in MongoDB with its reasoning attached.

## Scope

Point it at any domain. Zero auth. Open source, clone-and-run.

In scope: sitemap inventory, topic candidates, memory gate, live SERP verdicts, briefs,
rule induction, human approval, checkpoint/resume.

Explicitly out of scope: drafts, publishing, Google Search Console, backlinks/outreach,
rank tracking, web dashboard.

## Pipeline

```
cairn run <domain>

[0] Site memory     cold: crawl sitemap -> pages + embeddings
                    warm: delta only

[1] Candidates      Hermes proposes N topics from inventory + web research

[2] MEMORY GATE     three checks, zero API spend:
      a. vector+keyword search vs `pages`    -> already covered / cannibalization
      b. vector search vs `verdicts`         -> previously graded UNWINNABLE
      c. `rules` semantic filter             -> learned generalization applies
    every veto records the exact memory doc that caused it

[3] SERP verdict    survivors only. Hermes reads live top-10, grades
                    WINNABLE / CONTESTED / UNWINNABLE + reason + competitors + intent

[4] Brief           winnable only. Internal links retrieved from `pages` by vector search

[5] Rule induction  reads this run's verdicts, writes generalizations into `rules`
                    with confidence. Confirmed rules strengthen, contradicted decay.

[6] Human gate      cairn review -> approve/reject. Rejection reasons become rules.
```

Every stage checkpoints. `cairn run --resume <run_id>` continues mid-flight, including
mid-conversation for the Hermes agent.

## Data model

State and memory are kept separate.

**State** (durable, resumable, disposable after the run):
- `runs` — stage cursor, checkpoint, token ledger, Hermes trajectories keyed by `run_id:stage`

**Memory** (cross-run, semantic, permanent):
- `sites` — domain, vertical, ICP, crawl cursor
- `pages` — url, title, h1, summary, targetKeyword, embedding *(vector + Atlas Search index)*
- `topics` — candidate, status, vetoReason, vetoedBy (memory doc id)
- `verdicts` — query, grade, reason, competitors[], intent, embedding *(vector index)*
- `rules` — text, confidence, evidenceIds[], timesApplied, embedding *(vector index)*
- `briefs` — full brief, status, humanFeedback

## MongoDB feature usage

- **Vector search** on `pages`, `verdicts`, `rules` — each serving a distinct veto decision.
- **Atlas Search** alongside vector on `pages` — hybrid. Vector catches same-intent/different-words;
  keyword catches literal `targetKeyword` collision. Cannibalization needs both.
- **Automated embeddings** — generated and maintained in-database, no separate pipeline.
  Fallback ladder: Atlas auto -> Voyage API -> deterministic local hash (dev only).
- **Change streams** — watcher on `verdicts` fires the brief stage the moment a verdict lands
  WINNABLE, decoupling grading from briefing.
- **Atomic updates** — rule confidence is `$inc` on confirmation and decay on contradiction.
  Learning is a database write, so it is auditable and cannot hallucinate.
- **Aggregation pipeline** — tokens per accepted brief, grouped by run. The headline metric.

## SEO mechanics

Real practitioner mechanics, not demo heuristics:

- **Content inventory** from `sitemap.xml`. Zero auth, any domain.
- **Cannibalization detection** — two of your own pages competing for one intent splits link equity.
  The most valuable thing an AI content pipeline can do, and the thing they universally fail at.
- **SERP difficulty read** — who ranks, site type, and which *format* ranks. If official docs and
  Wikipedia own a definitional query, a marketing site does not win it.
- **Intent matching** — informational / commercial-investigation / transactional. Mismatch means
  no rank regardless of quality.
- **Topical authority** — only pursue topics adjacent to clusters the site already covers,
  derived from `pages` embeddings.
- **Internal linking** — brief names specific internal URLs retrieved from the site's own inventory.
  They cannot be hallucinated; they came out of the database.

Output is a **brief, not a draft** — the human gate stays before publication.

## Hermes usage

- One `AIAgent` per stage, specialized via `ephemeral_system_prompt`: SERP-grader, brief-writer,
  rule-inducer.
- `enabled_toolsets=["web"]` — live SERP reads with tool invocation and retry handling.
- `skip_memory=True`, `skip_context_files=True` — deliberately. MongoDB is the sole memory
  substrate. *We replaced the agent's private, local, single-session memory with a shared,
  queryable, cross-run memory in MongoDB.*
- `run_conversation()` returns full `messages`; we persist them to `runs` and replay via
  `conversation_history=` on resume, so the agent resumes mid-conversation with reasoning intact.
- `task_id` = `run_id:stage` for correlation back to database documents.
- `ThreadPoolExecutor` with a fresh `AIAgent` per thread (instances are not thread-safe) to fan out
  verdict grading.
- Model routing via OpenRouter: cheap model for high-volume verdict grading, stronger model for
  low-volume brief writing.

## Demo

1. `cairn run acme.com` — cold. Full crawl, candidates, verdicts, briefs. Show token cost.
2. Ctrl-C mid-run. `--resume`. Continues from checkpoint.
3. `cairn run acme.com` — warm. Most candidates vetoed by memory before any API call, each citing
   stored evidence. Large token reduction.
4. `cairn stats` — tokens per accepted brief falling run over run; `rules` the system wrote itself.

## Risks

- Atlas automated embeddings may be unavailable on the sandbox tier -> fallback ladder in `embed.py`.
- Hermes installs from git (no wheel) -> vendor via `uv` git dependency, pin commit.
- Live SERP reads are the slowest stage -> parallel fan-out, and verdicts cache permanently.

If the clock slips, stages 4-5 get thinner. The memory gate never does — it is the project.
