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
from .db import INDEX_NAMES, get_db
from .embed import AUTOEMBED_PATH, cosine, embed_one, uses_atlas_autoembed


@dataclass
class Decision:
    query: str
    passed: bool
    reason: str = ""
    vetoed_by: str = ""  # collection:_id of the memory doc responsible
    evidence: str = ""
    score: float = 0.0
    # "proceed" | "stop" | "improve".
    #
    # `improve` is the outcome only Search Console can produce: we already rank
    # for this, but not well enough. It is not a veto -- it redirects the work
    # from "write a new article" to "upgrade the page you already have", which
    # is usually the cheaper win.
    action: str = ""
    improve_url: str = ""

    def __post_init__(self) -> None:
        if not self.action:
            self.action = "proceed" if self.passed else "stop"

    @property
    def kind(self) -> str:
        return self.vetoed_by.split(":")[0] if self.vetoed_by else ""


_WARNED: set[str] = set()


def knn(
    collection: str,
    site: str,
    text: str,
    limit: int = 5,
    projection: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Semantic search over one memory collection, keyed by query *text*.

    With Atlas Automated Embedding the vectors never leave the server: we pass
    `query` text and a `model`, and Atlas embeds both sides. With a client-side
    backend we embed here and fall back to an exact cosine scan if the index is
    still building -- at hackathon corpus sizes that is milliseconds, and it
    keeps the gate honest rather than silently returning zero matches, which
    would read as "no duplicates found": the most dangerous failure mode here.
    """
    db = get_db()
    index = INDEX_NAMES[collection]

    if uses_atlas_autoembed():
        stage = {
            "index": index,
            "path": AUTOEMBED_PATH[collection],
            "query": text,
            "model": SETTINGS.autoembed_model,
            # Required even under autoEmbed: the embedding is server-side but the
            # search is still ANN.
            "numCandidates": max(limit * 20, 100),
            "limit": limit,
            "filter": {"site": site},
        }
        try:
            return _run(collection, stage, projection)
        except OperationFailure as exc:
            # No exact-scan fallback exists here: with autoEmbed there are no
            # stored vectors to scan. Fail loudly instead of pretending the
            # memory is empty.
            if collection not in _WARNED:
                _WARNED.add(collection)
                print(
                    f"  ! vector search on `{collection}` failed: "
                    f"{str(exc)[:120]}\n"
                    f"    the memory gate is running BLIND on this collection. "
                    f"Run `cairn init --wait`."
                )
            return []

    vector = embed_one(text, input_type="query")
    stage = {
        "index": index,
        "path": "embedding",
        "queryVector": vector,
        "numCandidates": max(limit * 20, 100),
        "limit": limit,
        "filter": {"site": site},
    }
    try:
        hits = _run(collection, stage, projection)
        if hits:
            return hits
    except OperationFailure:
        pass
    return _exact_knn(collection, site, vector, limit, projection)


def _run(
    collection: str, stage: dict[str, Any], projection: dict[str, Any] | None
) -> list[dict[str, Any]]:
    pipeline: list[dict[str, Any]] = [
        {"$vectorSearch": stage},
        {"$set": {"score": {"$meta": "vectorSearchScore"}}},
    ]
    if projection:
        pipeline.append({"$project": {**projection, "score": 1}})
    return list(get_db()[collection].aggregate(pipeline))


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
    # Searches `targetKeyword` ONLY, deliberately.
    #
    # Including title and h1 destroys precision: a long query shares common
    # tokens with unrelated headlines and BM25 rewards the pile-up. Measured on
    # plausible.io, "bounce rate removed in GA4 what to use instead" matched
    # /blog/how-to-store-last-seen-for-users at 6.08 -- ABOVE the true collision
    # "plausible vs matomo" -> /vs-matomo at 5.65, so no threshold could split
    # them. Restricted to targetKeyword the same pair separates cleanly:
    # 2.34 false vs 3.07 true. Dropping fuzzy did not help; the path did.
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
                                "path": "targetKeyword",
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


def gsc_decision(site: str, query: str) -> Decision | None:
    """Search Console check. Returns None when GSC is unconfigured or silent.

    This is the only check backed by the site's *own* performance rather than
    what the sitemap claims or what the public SERP shows, so it runs first --
    if you already rank, that fact outranks every other signal.
    """
    # Gate on whether the DATA exists, not on whether credentials are currently
    # loaded. Once performance is in MongoDB it is memory like anything else, and
    # a run on a machine without the service-account key should still use it.
    hits = knn(
        "gsc_performance",
        site,
        query,
        limit=1,
        projection={
            "query": 1, "page": 1, "position": 1,
            "impressions": 1, "clicks": 1,
        },
    )
    if not hits:
        return None
    top = hits[0]
    # Query-to-query similarity, the same shape as verdict reuse, so it uses the
    # same calibrated threshold.
    if top["score"] < SETTINGS.verdict_reuse_threshold:
        return None
    impressions = int(top.get("impressions", 0))
    if impressions < SETTINGS.gsc_min_impressions:
        return None  # too little data to act on in either direction

    position = float(top.get("position", 999))
    url, matched = top.get("page", ""), top.get("query", "")
    common = dict(
        query=query,
        vetoed_by=f"gsc_performance:{top['_id']}",
        score=top["score"],
        improve_url=url,
    )

    if position <= SETTINGS.gsc_own_position:
        return Decision(
            passed=False,
            action="stop",
            reason=f"already ranking #{position:.0f}",
            evidence=f"{url} ranks #{position:.1f} for '{matched}' "
                     f"({impressions:,} impressions)",
            **common,
        )
    if position <= SETTINGS.gsc_striking_position:
        clicks = int(top.get("clicks", 0))
        return Decision(
            passed=False,
            action="improve",
            reason=f"striking distance #{position:.0f}",
            evidence=f"{url} ranks #{position:.1f} for '{matched}' with "
                     f"{impressions:,} impressions and only {clicks:,} clicks — "
                     f"improve this page instead of writing a new one",
            **common,
        )
    return None


def evaluate(site: str, query: str) -> Decision:
    """Run one candidate through the memory checks.

    No LLM calls and no web calls -- the whole point is that this is the cheap
    path. With autoEmbed the embedding happens inside the database.
    """
    # (d) do we ALREADY RANK for this? Our own data beats every other signal.
    gsc = gsc_decision(site, query)
    if gsc is not None:
        return gsc

    # (a) do we already cover this intent?
    for page in knn("pages", site, query, limit=3, projection={"url": 1, "title": 1}):
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
    if kw and kw[0]["score"] >= SETTINGS.keyword_threshold:
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
        query,
        limit=3,
        projection={"query": 1, "grade": 1, "reason": 1},
    ):
        if v["score"] < SETTINGS.verdict_reuse_threshold:
            continue
        if v.get("grade") in ("UNWINNABLE", "CONTESTED"):
            return Decision(
                query=query,
                passed=False,
                reason=f"prior verdict {v['grade']}",
                vetoed_by=f"verdicts:{v['_id']}",
                evidence=f"'{v.get('query', '')}' — {v.get('reason', '')}",
                score=v["score"],
            )
        # A WINNABLE verdict is not a reason to stop -- unless we already acted
        # on it. Without this, every repeat run re-grades and re-briefs its own
        # previous winners and quietly accumulates duplicate briefs.
        brief = get_db().briefs.find_one(
            {"site": site, "query": v.get("query")}, {"_id": 1, "status": 1}
        )
        if brief:
            return Decision(
                query=query,
                passed=False,
                reason=f"brief already written ({brief.get('status', '')})",
                vetoed_by=f"briefs:{brief['_id']}",
                evidence=f"'{v.get('query', '')}' — graded WINNABLE and briefed",
                score=v["score"],
            )

    # (c) does a learned rule forbid it?
    # Only the single best-matching rule gets a vote, and only if it says avoid.
    #
    # Scanning the top-N instead lets a weak, irrelevant `avoid` rule veto a topic
    # that a stronger `prefer` rule endorses. Measured on umami.is, for the query
    # "umami vs fathom for agencies": a privacy/GDPR avoid-rule scored 0.730 while
    # the correct brand-comparison prefer-rule scored 0.709. Rule-to-query
    # matching compares a category sentence against a short query, so the margins
    # are genuinely thin -- this check is the least reliable of the three and is
    # deliberately given the least power.
    top_rules = (
        knn(
            "rules",
            site,
            query,
            limit=1,
            projection={"rule": 1, "confidence": 1, "polarity": 1},
        )
        if SETTINGS.rule_veto_enabled
        else []
    )
    for rule in top_rules:
        if (
            rule.get("polarity") == "avoid"
            and rule["score"] >= SETTINGS.rule_match_threshold
            and rule.get("confidence", 0) >= 0.6
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
