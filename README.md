# cairn

**An SEO agent that gets cheaper and sharper every run, because MongoDB remembers what didn't work.**

Built at the MongoDB Persistent Context Sprint Hackathon, 2026-08-13.

> A cairn is a stack of stones marking a trail, so the next traveler doesn't re-explore the dead ends.

---

## The idea

Most AI SEO tools are stateless. Point one at your site twice and it does the same expensive research
twice, proposes the same topics, and cheerfully suggests an article you already published three
months ago.

Cairn stores every SERP verdict, every duplicate collision, and every human rejection in MongoDB —
then uses that memory to **veto work before paying for it**.

The load-bearing claim:

> The same candidate topic costs ~2,000 tokens to evaluate on run 1 and **~0 tokens on run 2**,
> because the answer is already in MongoDB with its reasoning attached.

This is the difference between RAG and memory. A RAG app retrieves documents and stuffs them into a
prompt. **Cairn retrieves and then refuses to run the next stage.** Retrieval output here is control
flow, not prompt filler.

## Pipeline

```
cairn run <domain>

[0] Site memory     cold: crawl sitemap → pages + embeddings
                    warm: delta only

[1] Candidates      Hermes proposes topics from inventory + live web research

[2] MEMORY GATE     three checks, zero API spend:
      a. vector + Atlas Search vs `pages`   → already covered / cannibalization risk
      b. vector vs `verdicts`               → previously graded UNWINNABLE
      c. vector vs `rules`                  → a learned generalization forbids it
    every veto prints the exact MongoDB document that killed it

[3] SERP verdicts   survivors only. Hermes reads the live top-10 and grades
                    WINNABLE / CONTESTED / UNWINNABLE + reason + competitors

[4] Briefs          WINNABLE only. Internal links retrieved from `pages`,
                    so they cannot be hallucinated

[5] Rule induction  generalizes this run's verdicts into `rules` with confidence

[6] Human gate      cairn review → approve/reject. Rejections become rules.
```

Every stage checkpoints. `cairn run <domain> --resume <run_id>` continues mid-flight — including
resuming the Hermes agent **mid-conversation**, from trajectories stored in MongoDB.

## How MongoDB is used

| Feature | Where | Why |
|---|---|---|
| **Vector Search** | `pages`, `verdicts`, `rules` | Three indexes, three distinct veto decisions |
| **Atlas Search** | `pages` | The lexical half of cannibalization detection — vector catches same-intent/different-words, keyword catches literal `targetKeyword` collision. Either alone misses half the cases. |
| **Change Streams** | watcher on `verdicts` | Briefing starts the moment a WINNABLE verdict lands, instead of waiting for the whole grading batch |
| **Atomic `$inc`** | `rules.confidence` | Learning is a database write, so it's auditable and can't hallucinate itself upward |
| **Aggregation** | `cairn stats` | Tokens per accepted brief, run over run — the headline metric |

**State and memory are deliberately separate.** `runs` is state: a stage cursor, disposable once the
run ends. `pages` / `verdicts` / `rules` / `briefs` are memory: cross-run, semantic, permanent.

## How Hermes is used

One `AIAgent` per stage, specialized by `ephemeral_system_prompt` — scout, grader, briefer, rulemaker.

The central choice is what we **turned off**:

```python
AIAgent(
    enabled_toolsets=["web"],     # live SERP reads with tool invocation + retries
    skip_memory=True,             # ← MongoDB is the memory
    skip_context_files=True,      # ←
    quiet_mode=True,
)
```

We replaced Hermes's private, local, single-session memory with a **shared, queryable, cross-run
memory in MongoDB**. Hermes supplies reasoning and web tools; everything it durably learns lives in
Atlas, where the next run — on any machine — can query it.

`run_conversation()` returns the full message list, which we persist to `runs.trajectories` and
replay via `conversation_history=` on resume. Verdict grading fans out across a `ThreadPoolExecutor`
with a fresh `AIAgent` per thread, since instances aren't thread-safe.

Model routing through OpenRouter: a cheap model for high-volume verdict grading, a stronger one for
low-volume brief writing.

## The SEO, specifically

These are real practitioner mechanics, not demo heuristics:

- **Cannibalization detection.** Two of your own pages competing for one intent splits link equity.
  It's the most valuable thing an AI content pipeline can do, and the thing they universally fail at —
  they publish the collision.
- **SERP difficulty read.** If official docs and Wikipedia own a definitional query, a marketing site
  does not win it. The agent reads what actually ranks and which *format* ranks.
- **Intent matching.** Informational / commercial-investigation / transactional. Mismatch means no
  rank regardless of content quality.
- **Topical authority.** Only pursue topics adjacent to clusters the site already covers, derived
  from the `pages` embeddings.
- **Internal linking that's real.** Brief link targets are retrieved from the site's own inventory.
  They came out of the database, so they can't be invented.

Output is a **brief, not a draft** — the human gate stays before publication, deliberately.

## Setup

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
git clone <this repo> && cd cairn

# Hermes ships no wheel, so vendor it from source:
git clone --depth 1 https://github.com/NousResearch/hermes-agent.git vendor/hermes-agent

uv sync

cp .env.example .env      # add MONGODB_URI and OPENROUTER_API_KEY
uv run cairn doctor       # verify credentials + connectivity
uv run cairn init --wait  # create collections and Atlas search indexes
```

## Use

```bash
uv run cairn run example.com        # full pipeline
uv run cairn run example.com --resume <run_id>   # continue after a crash
uv run cairn review example.com     # approve/reject briefs; rejections become rules
uv run cairn memory example.com     # what the system has learned
uv run cairn stats example.com      # tokens per brief, run over run
uv run cairn reset example.com      # wipe memory, to demo a cold start again
```

## Degradation

Nothing in the pipeline hard-blocks on a missing optional dependency:

- **No Atlas vector index yet?** Index builds are asynchronous, so the gate falls back to an exact
  cosine scan. At these corpus sizes that's milliseconds — and it keeps the gate honest instead of
  silently returning "no duplicates found", which is the most dangerous possible failure mode here.
- **No `VOYAGE_API_KEY`?** Embeddings fall back to a deterministic hashed bag-of-words. Lexical
  similarity only, but the pipeline runs end-to-end with no embedding provider at all.
- **No change streams?** The verdict watcher falls back to polling.

## License

MIT.
