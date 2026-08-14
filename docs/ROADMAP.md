# Roadmap

Two features scoped but deliberately not built during the hackathon. Both were cut for the same
reason: they trade away the property that makes cairn cloneable — *point it at any domain, no auth,
no repo access* — and neither could be finished and tested inside the window. Written up here so the
design decisions survive.

---

## 1. Google Search Console as a fourth memory check

### Why it's worth doing

Everything cairn knows today is **external**: what your sitemap says you cover, and what Google shows
the public. GSC is the one source of *your own* truth — what you actually rank for, and how well.

The highest-value pattern in SEO is not "write another article". It is:

> This page gets 4,000 impressions a month and 60 clicks. You are sitting at position 8.
> Improving it beats writing anything new.

Cairn cannot see that today, so it can only ever recommend *new* content. That is a real blind spot:
it will happily propose a topic you already rank #8 for, and the gate will pass it, because a
sitemap entry tells you a page exists but not that it is underperforming.

### Where it slots in

GSC becomes **check (d)** in the memory gate, after the existing three:

```
a. pages     -> we already cover this intent
b. verdicts  -> we already graded this query
c. rules     -> a learned generalization applies
d. gsc       -> we ALREADY RANK for this; improve the existing page instead   <-- new
```

The decision it produces is not a plain veto. It is a redirect, and that is a new outcome type:

| position | impressions | recommended action |
|---|---|---|
| 1–3 | any | veto — you own this already |
| 4–10 | high | **rewrite brief**: optimize the existing URL, don't write a new one |
| 4–10 | low | veto — demand isn't there |
| 11–30 | high | brief a substantial upgrade of the existing page |
| none | — | fall through to the normal pipeline |

That "striking distance" band (4–20 with real impressions) is where the cheapest wins live.

### New collection

```
gsc_performance
  site, query, page, impressions, clicks, ctr, position, fetchedAt
  embedding  (autoEmbed on `query`, same pattern as verdicts)
```

Indexed the same way as the existing three vector indexes, with `site` as a filter field so
multi-site isolation is preserved.

### The work, honestly

- **OAuth 2.0** — Google Cloud project, consent screen, `webmasters.readonly` scope, token refresh
  and encrypted storage. This is the bulk of the effort and most of the risk.
- `searchanalytics.query` against the Search Console API, paginated, dimensions `query` + `page`.
- A `cairn connect <domain>` command for the consent flow, and per-site token storage.
- A new `Decision` outcome (`improve_existing`) plus a brief variant that targets an existing URL
  rather than proposing a new one.
- Threshold calibration for the position/impression bands, done the same way as the existing gates:
  measure a must-fire and a must-not-fire case before trusting any number.

Estimate: **1–2 days**, most of it auth and the new brief type, not the query itself.

### The tradeoff to be explicit about

GSC only works on sites you have **verified ownership of**. The moment it becomes required, cairn
stops being "clone it and point it at any domain" and becomes "clone it, own the site, and complete
an OAuth flow". So it must stay strictly **optional and additive**: without a GSC connection the
pipeline behaves exactly as it does now, and with one it gains check (d).

---

## 2. A second Hermes agent with repository access — BUILT, see `cairn author`

### The idea

Close the loop. Today the chain is:

```
cairn  ->  brief  ->  [human writes]  ->  [human publishes]
```

A second agent — call it the **author** — would take an *approved* brief, write the article against
the site's actual content conventions, and open a pull request.

### Why it is a genuinely different product

The current agents are **read-only against the public web**. An author agent needs write access to a
repository, which is a categorically different risk profile and a different security model. It is
not an increment on the existing pipeline; it is a second system that consumes the first one's
output.

That is the main reason it was cut. The other reason is a design position taken up front and worth
keeping: **the human gate stays before publication.** Automating research is safe. Automating
publication is where quality drift and self-inflicted SEO damage start. An author agent should
therefore end at a *pull request*, never at a deploy.

### Shape

```
cairn author <brief-id> --repo ./my-site

  1. read the approved brief from MongoDB
  2. read the repo: content directory, front-matter schema, 2-3 existing posts for
     voice, the site's own internal-link conventions
  3. draft the article against the brief's outline and angle
  4. insert the brief's internal links -- already verified to exist
  5. write the file, matching the existing front-matter exactly
  6. open a PR. stop. a human reviews and merges.
```

### Permissions model

The agent should be able to do exactly one thing and nothing else:

- **Scope**: a single configured content directory (e.g. `content/blog/**`). Nothing outside it.
- **Operations**: create new files only. No edits to existing files, no deletes, no config or CI
  changes. A new article should never be able to touch `package.json` or a workflow file.
- **Branch**: always a fresh `cairn/<slug>` branch. Never commits to the default branch.
- **Terminal**: off. `enabled_toolsets=["file", "web"]` — no shell, so it cannot run build scripts
  or arbitrary commands.
- **Credentials**: a GitHub token scoped to a single repo with PR-open permission, never a
  broad-scope personal token.
- **Reviewable diff**: because it only adds files inside one directory, every PR is trivially
  auditable — which is the actual safety property, more than any rule above.

### What MongoDB adds here

The same memory argument applies, one level up. Store per-repo conventions once — front-matter
schema, voice notes, directory layout, which internal links were accepted or removed in review —
and the author agent stops rediscovering them on every article. Merged-vs-rejected PR outcomes
become training signal for the next draft, in exactly the way SERP verdicts feed topic selection
today.

### The work, honestly

- Repo reader: detect the content directory and front-matter schema, sample existing posts.
- An `author` Hermes role with a file toolset and the permission scope above.
- PR creation via `gh` or the GitHub API, plus a status write-back to the brief document
  (`drafted`, `pr_open`, `merged`).
- A `repo_conventions` collection keyed by repo.

Estimate: **2–3 days**, and it should ship behind an explicit flag with a dry-run mode that writes
to a local file before it is ever pointed at a real repository.

---

## Smaller, cheaper items

- **JavaScript-rendered sites.** The crawler reads server HTML, so `umami.is` yields zero pages. It
  fails soft, which is the wrong direction — with no page memory every candidate passes the gate.
  Should warn loudly, and optionally render with a headless browser.
- **Rule vetoing.** Off by default because a measured false positive (0.730) outscored every true
  positive (0.636–0.661). Storing *trigger example queries* alongside each rule and embedding those
  instead of the rule sentence would likely fix the separation.
- **Scheduling.** A run is a clean bounded process that leaves its state in Atlas, so `cron` already
  works. What is missing is a notification when briefs are waiting.
- **Cost in currency.** The ledger counts tokens; per-run dollar cost would be more legible.
