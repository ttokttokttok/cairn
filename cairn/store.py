"""Persistence: memory writes, and run state / checkpoints.

State and memory are kept apart on purpose. `runs` is state -- a cursor through
the pipeline, disposable once the run finishes. Everything else is memory --
cross-run, semantic, permanent.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from pymongo import UpdateOne

from .crawl import CrawlResult
from .db import get_db
from .embed import active_backend, embed, uses_atlas_autoembed

STAGES = [
    "inventory",
    "candidates",
    "gate",
    "verdicts",
    "briefs",
    "rules",
    "done",
]


# --- memory ------------------------------------------------------------------


def upsert_site(crawl: CrawlResult) -> None:
    get_db().sites.update_one(
        {"domain": crawl.domain},
        {
            "$set": {
                "root": crawl.root,
                "sitemapSource": crawl.source,
                "lastCrawledAt": time.time(),
            },
            "$setOnInsert": {"firstSeenAt": time.time()},
        },
        upsert=True,
    )


def known_urls(site: str) -> set[str]:
    return {d["url"] for d in get_db().pages.find({"site": site}, {"url": 1})}


def store_pages(site: str, pages: list) -> int:
    """Upsert page inventory. Returns count written.

    Under autoEmbed we write only `embedText` and Atlas maintains the vector.
    Otherwise we embed client-side and store the vector alongside.
    """
    if not pages:
        return 0
    texts = [p.text_for_embedding() for p in pages]
    vectors = (
        [None] * len(pages)
        if uses_atlas_autoembed()
        else embed(texts, input_type="document")
    )
    ops = []
    for page, text, vec in zip(pages, texts, vectors):
        fields = {
            "title": page.title,
            "h1": page.h1,
            "description": page.description,
            "summary": page.summary,
            "targetKeyword": page.target_keyword,
            "words": page.words,
            "embedText": text,
            "embedBackend": active_backend(),
            "updatedAt": time.time(),
        }
        if vec is not None:
            fields["embedding"] = vec
        ops.append(
            UpdateOne({"site": site, "url": page.url}, {"$set": fields}, upsert=True)
        )
    get_db().pages.bulk_write(ops, ordered=False)
    return len(ops)


def page_inventory(site: str, limit: int = 80) -> list[dict[str, Any]]:
    return list(
        get_db().pages.find(
            {"site": site}, {"url": 1, "title": 1, "targetKeyword": 1, "_id": 0}
        ).limit(limit)
    )


def store_verdict(site: str, run_id: str, query: str, verdict: dict[str, Any]) -> Any:
    # `query` is itself the indexed text under autoEmbed, so nothing extra to add.
    doc = {
        "site": site,
        "runId": run_id,
        "query": query,
        "grade": verdict.get("grade", "CONTESTED"),
        "reason": verdict.get("reason", ""),
        "intent": verdict.get("intent", ""),
        "dominantFormat": verdict.get("dominant_format", ""),
        "competitors": verdict.get("competitors", []),
        "observedAt": time.time(),
    }
    if not uses_atlas_autoembed():
        doc["embedding"] = embed([query], input_type="query")[0]
    return get_db().verdicts.insert_one(doc).inserted_id


def store_gsc(site: str, rows) -> int:
    """Upsert Search Console performance. Text-only under autoEmbed."""
    from pymongo import UpdateOne

    ops = []
    for r in rows:
        fields = {
            "impressions": r.impressions,
            "clicks": r.clicks,
            "ctr": r.ctr,
            "position": r.position,
            "fetchedAt": time.time(),
        }
        if not uses_atlas_autoembed():
            fields["embedding"] = embed([r.query], input_type="query")[0]
        ops.append(
            UpdateOne(
                {"site": site, "query": r.query, "page": r.page},
                {"$set": fields},
                upsert=True,
            )
        )
    if not ops:
        return 0
    for i in range(0, len(ops), 1000):
        get_db().gsc_performance.bulk_write(ops[i : i + 1000], ordered=False)
    return len(ops)


def store_topic(site: str, run_id: str, decision, stage_cost: int = 0) -> None:
    get_db().topics.insert_one(
        {
            "site": site,
            "runId": run_id,
            "query": decision.query,
            "status": "passed" if decision.passed else "vetoed",
            "action": getattr(decision, "action", ""),
            "improveUrl": getattr(decision, "improve_url", ""),
            "vetoReason": decision.reason,
            "vetoedBy": decision.vetoed_by,
            "evidence": decision.evidence,
            "score": decision.score,
            "tokensSaved": stage_cost if not decision.passed else 0,
            "createdAt": time.time(),
        }
    )


def store_brief(
    site: str,
    run_id: str,
    query: str,
    brief: dict[str, Any],
    kind: str = "new_article",
    improve_url: str = "",
) -> Any:
    return (
        get_db()
        .briefs.insert_one(
            {
                "site": site,
                "runId": run_id,
                "query": query,
                "brief": brief,
                # "new_article" or "improve_existing" -- the latter targets a URL
                # that already ranks rather than proposing a new page.
                "kind": kind,
                "improveUrl": improve_url,
                "status": "pending_approval",
                "createdAt": time.time(),
            }
        )
        .inserted_id
    )


def store_rules(site: str, run_id: str, rules: list[dict[str, Any]]) -> int:
    """Insert induced rules, reinforcing near-duplicates instead of duplicating.

    Confidence is an atomic `$inc`, never an LLM output -- learning that lives in
    the database is auditable and cannot hallucinate itself upward.
    """
    from .gate import knn

    rules = [r for r in rules if r.get("rule")]
    if not rules:
        return 0
    db = get_db()
    written = 0
    texts = [r["rule"] for r in rules]
    vectors = (
        [None] * len(rules)
        if uses_atlas_autoembed()
        else embed(texts, input_type="document")
    )
    for rule, text, vec in zip(rules, texts, vectors):
        near = knn("rules", site, text, limit=1, projection={"rule": 1})
        if near and near[0]["score"] >= 0.93:
            db.rules.update_one(
                {"_id": near[0]["_id"]},
                {
                    "$inc": {"confidence": 0.05, "timesConfirmed": 1},
                    "$push": {"evidenceIds": run_id},
                },
            )
            continue
        doc = {
            "site": site,
            "rule": text,
            # Default to "prefer": a rule that fails to declare polarity must not
            # silently gain the power to veto.
            "polarity": "avoid" if rule.get("polarity") == "avoid" else "prefer",
            "confidence": min(float(rule.get("confidence", 0.6)), 0.95),
            "evidence": rule.get("evidence", ""),
            "evidenceIds": [run_id],
            "timesApplied": 0,
            "timesConfirmed": 1,
            "createdAt": time.time(),
        }
        if vec is not None:
            doc["embedding"] = vec
        db.rules.insert_one(doc)
        written += 1
    return written


# --- run state / checkpoints -------------------------------------------------


def new_run(site: str) -> str:
    run_id = f"{site}-{uuid.uuid4().hex[:8]}"
    get_db().runs.insert_one(
        {
            "runId": run_id,
            "site": site,
            "stage": "inventory",
            "startedAt": time.time(),
            "tokens": 0,
            "tokensSavedByMemory": 0,
            "coldStart": get_db().pages.count_documents({"site": site}) == 0,
            "checkpoint": {},
            "trajectories": {},
        }
    )
    return run_id


def get_run(run_id: str) -> dict[str, Any] | None:
    return get_db().runs.find_one({"runId": run_id})


def checkpoint(run_id: str, stage: str, data: dict[str, Any] | None = None) -> None:
    update: dict[str, Any] = {"stage": stage, "updatedAt": time.time()}
    for key, value in (data or {}).items():
        update[f"checkpoint.{key}"] = value
    get_db().runs.update_one({"runId": run_id}, {"$set": update})


def save_trajectory(run_id: str, stage: str, messages: list[dict[str, Any]]) -> None:
    """Persist a Hermes conversation so a resume continues mid-conversation.

    Replayed via `conversation_history=` so the agent picks up with its reasoning
    intact rather than restarting the stage from a cold prompt.
    """
    get_db().runs.update_one(
        {"runId": run_id},
        {"$set": {f"trajectories.{stage}": messages[-40:]}},
    )


def load_trajectory(run_id: str, stage: str) -> list[dict[str, Any]] | None:
    run = get_run(run_id) or {}
    return (run.get("trajectories") or {}).get(stage)


def add_tokens(run_id: str, tokens: int, saved: int = 0) -> None:
    get_db().runs.update_one(
        {"runId": run_id},
        {"$inc": {"tokens": tokens, "tokensSavedByMemory": saved}},
    )


def finish_run(run_id: str) -> None:
    checkpoint(run_id, "done")
    get_db().runs.update_one({"runId": run_id}, {"$set": {"finishedAt": time.time()}})
