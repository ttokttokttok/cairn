"""The memory gate.

This is the project. Every candidate topic passes three MongoDB checks BEFORE any
web call or token is spent. Retrieval output here is control flow, not prompt
filler: a hit does not get summarized into a prompt, it stops the stage.

  a. pages     hybrid vector + Atlas Search -- we already cover this intent
  b. verdicts  vector                       -- we already graded this query
  c. rules     vector                       -- a learned generalization forbids it

Every veto records the id of the memory document that caused it, so the terminal
can show which stored fact killed which topic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pymongo.errors import OperationFailure

from .config import SETTINGS
from .db import get_db
from .embed import cosine, embed_one


@dataclass
class Decision:
    query: str
    passed: bool
    reason: str = ""
    vetoed_by: str = ""  # collection:_id of the memory doc responsible
    evidence: str = ""
    score: float = 0.0

    @property
    def kind(self) -> str:
        return self.vetoed_by.split(":")[0] if self.vetoed_by else ""


def knn(
    collection: str,
    site: str,
    vector: list[float],
    limit: int = 5,
    projection: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Vector search with an exact-cosine fallback.

    Atlas search indexes build asynchronously, and a brand-new cluster can be
    queryable for writes long before `pages_vec` is ready. At hackathon corpus
    sizes an exact scan is milliseconds, so falling back keeps the gate honest
    instead of silently returning zero matches (which would look like "no
    duplicates found" -- the most dangerous possible failure mode here).
    """
    db = get_db()
    index = {"pages": "pages_vec", "verdicts": "verdicts_vec", "rules": "rules_vec"}[
        collection
    ]
    pipeline = [
        {
            "$vectorSearch": {
                "index": index,
                "path": "embedding",
                "queryVector": vector,
                "numCandidates": max(limit * 20, 100),
                "limit": limit,
                "filter": {"site": site},
            }
        },
        {"$set": {"score": {"$meta": "vectorSearchScore"}}},
    ]
    if projection:
        pipeline.append({"$project": {**projection, "score": 1}})
    try:
        hits = list(db[collection].aggregate(pipeline))
        if hits:
            return hits
    except OperationFailure:
        pass
    return _exact_knn(collection, site, vector, limit, projection)


def _exact_knn(
    collection: str,
    site: str,
    vector: list[float],
    limit: int,
    projection: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    db = get_db()
    fields = dict(projection or {})
    fields["embedding"] = 1
    docs = list(db[collection].find({"site": site}, fields))
    scored = []
    for doc in docs:
        emb = doc.pop("embedding", None)
        if not emb:
            continue
        doc["score"] = cosine(vector, emb)
        scored.append(doc)
    scored.sort(key=lambda d: d["score"], reverse=True)
    return scored[:limit]


def keyword_hits(site: str, query: str, limit: int = 3) -> list[dict[str, Any]]:
    """Atlas Search on `pages`. The lexical half of cannibalization detection.

    Vector catches same-intent/different-words; this catches a literal
    targetKeyword collision that a semantic model may score as merely similar.
    """
    db = get_db()
    pipeline = [
        {
            "$search": {
                "index": "pages_text",
                "compound": {
                    "filter": [{"equals": {"path": "site", "value": site}}],
                    "must": [
                        {
                            "text": {
                                "query": query,
                                "path": ["targetKeyword", "title", "h1"],
                                "fuzzy": {"maxEdits": 1},
                            }
                        }
                    ],
                },
            }
        },
        {"$limit": limit},
        {"$set": {"score": {"$meta": "searchScore"}}},
        {"$project": {"url": 1, "title": 1, "targetKeyword": 1, "score": 1}},
    ]
    try:
        return list(db.pages.aggregate(pipeline))
    except OperationFailure:
        # No Atlas Search index (local mongod, or still building). The vector
        # half of the check still runs; we just lose the lexical signal.
        return []


def evaluate(site: str, query: str) -> Decision:
    """Run one candidate through all three memory checks. No API calls."""
    vec = embed_one(query, input_type="query")

    # (a) do we already cover this intent?
    for page in knn("pages", site, vec, limit=3, projection={"url": 1, "title": 1}):
        if page["score"] >= SETTINGS.dup_threshold:
            return Decision(
                query=query,
                passed=False,
                reason="already covered",
                vetoed_by=f"pages:{page['_id']}",
                evidence=f"{page.get('url', '')} — {page.get('title', '')}",
                score=page["score"],
            )

    kw = keyword_hits(site, query)
    if kw and kw[0]["score"] >= 3.0:
        top = kw[0]
        return Decision(
            query=query,
            passed=False,
            reason="keyword collision (cannibalization risk)",
            vetoed_by=f"pages:{top['_id']}",
            evidence=f"{top.get('url', '')} targets '{top.get('targetKeyword', '')}'",
            score=top["score"],
        )

    # (b) have we already graded this query? This is where the token savings live.
    for v in knn(
        "verdicts",
        site,
        vec,
        limit=3,
        projection={"query": 1, "grade": 1, "reason": 1},
    ):
        if v["score"] >= SETTINGS.verdict_reuse_threshold and v.get("grade") in (
            "UNWINNABLE",
            "CONTESTED",
        ):
            return Decision(
                query=query,
                passed=False,
                reason=f"prior verdict {v['grade']}",
                vetoed_by=f"verdicts:{v['_id']}",
                evidence=f"'{v.get('query', '')}' — {v.get('reason', '')}",
                score=v["score"],
            )

    # (c) does a learned rule forbid it?
    for rule in knn(
        "rules", site, vec, limit=3, projection={"rule": 1, "confidence": 1}
    ):
        if rule["score"] >= SETTINGS.rule_match_threshold and (
            rule.get("confidence", 0) >= 0.6
        ):
            return Decision(
                query=query,
                passed=False,
                reason="learned rule",
                vetoed_by=f"rules:{rule['_id']}",
                evidence=rule.get("rule", ""),
                score=rule["score"],
            )

    return Decision(query=query, passed=True, reason="no prior knowledge")


def note_rule_applied(decision: Decision) -> None:
    """Count a rule firing. Learning is a database write, not an LLM call."""
    if decision.kind != "rules":
        return
    from bson import ObjectId

    get_db().rules.update_one(
        {"_id": ObjectId(decision.vetoed_by.split(":", 1)[1])},
        {"$inc": {"timesApplied": 1}},
    )
