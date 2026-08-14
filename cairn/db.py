"""MongoDB access and index bootstrap.

State and memory are deliberately separate collections:

  state   runs                       -- resumable, disposable after the run
  memory  sites pages topics
          verdicts rules briefs      -- cross-run, semantic, permanent
"""

from __future__ import annotations

import time
from typing import Any

from pymongo import ASCENDING, MongoClient
from pymongo.database import Database
from pymongo.errors import OperationFailure

from .config import SETTINGS
from .embed import AUTOEMBED_PATH, uses_atlas_autoembed

_client: MongoClient | None = None


def get_db() -> Database:
    global _client
    if not SETTINGS.mongodb_uri:
        raise RuntimeError(
            "MONGODB_URI is not set. Copy .env.example to .env and paste your "
            "Atlas connection string."
        )
    if _client is None:
        _client = MongoClient(SETTINGS.mongodb_uri, appname="cairn")
    return _client[SETTINGS.db_name]


# --- vector index definitions ------------------------------------------------
# Three vector indexes, each serving a distinct veto decision in the memory gate.

INDEX_NAMES = {
    "pages": "pages_vec",
    "verdicts": "verdicts_vec",
    "rules": "rules_vec",
    "gsc_performance": "gsc_vec",
}


def _vector_field(collection: str) -> dict[str, Any]:
    """autoEmbed when Atlas owns the embeddings, a plain vector field otherwise."""
    if uses_atlas_autoembed():
        return {
            "type": "autoEmbed",
            "modality": "text",
            "path": AUTOEMBED_PATH[collection],
            "model": SETTINGS.autoembed_model,
        }
    return {
        "type": "vector",
        "path": "embedding",
        "numDimensions": SETTINGS.embed_dims,
        "similarity": "cosine",
    }


VECTOR_INDEXES: dict[str, dict[str, Any]] = {
    coll: {
        "name": name,
        "definition": {
            "fields": [_vector_field(coll), {"type": "filter", "path": "site"}]
        },
    }
    for coll, name in INDEX_NAMES.items()
}

# Atlas Search sits alongside the vector index on `pages`. Cannibalization is a
# two-signal problem: vector catches same-intent/different-words, keyword catches
# literal targetKeyword collision. Either signal alone misses half the cases.
SEARCH_INDEXES: dict[str, dict[str, Any]] = {
    "pages": {
        "name": "pages_text",
        "definition": {
            "mappings": {
                "dynamic": False,
                "fields": {
                    "site": {"type": "token"},
                    "title": {"type": "string"},
                    "h1": {"type": "string"},
                    "targetKeyword": {"type": "string"},
                    "summary": {"type": "string"},
                },
            }
        },
    }
}


def ensure_collections() -> list[str]:
    """Create collections and plain indexes. Idempotent."""
    db = get_db()
    created = []
    for name in (
        "sites", "pages", "topics", "verdicts", "rules", "briefs", "runs",
        "gsc_performance",
    ):
        if name not in db.list_collection_names():
            db.create_collection(name)
            created.append(name)

    db.sites.create_index([("domain", ASCENDING)], unique=True)
    db.pages.create_index([("site", ASCENDING), ("url", ASCENDING)], unique=True)
    db.topics.create_index([("site", ASCENDING), ("runId", ASCENDING)])
    db.verdicts.create_index([("site", ASCENDING), ("query", ASCENDING)])
    db.rules.create_index([("site", ASCENDING), ("confidence", ASCENDING)])
    db.briefs.create_index([("site", ASCENDING), ("status", ASCENDING)])
    db.runs.create_index([("runId", ASCENDING)], unique=True)
    db.gsc_performance.create_index(
        [("site", ASCENDING), ("query", ASCENDING), ("page", ASCENDING)], unique=True
    )
    return created


def ensure_search_indexes(verbose: bool = True) -> dict[str, str]:
    """Create Atlas vector + text search indexes. Idempotent, best-effort.

    Search index creation is asynchronous on Atlas; this kicks it off and
    reports status rather than blocking the pipeline.
    """
    db = get_db()
    status: dict[str, str] = {}

    for coll_name, spec in VECTOR_INDEXES.items():
        status[spec["name"]] = _create_search_index(
            db, coll_name, spec["name"], spec["definition"], "vectorSearch"
        )
    for coll_name, spec in SEARCH_INDEXES.items():
        status[spec["name"]] = _create_search_index(
            db, coll_name, spec["name"], spec["definition"], "search"
        )
    return status


def _create_search_index(
    db: Database, coll_name: str, name: str, definition: dict, kind: str
) -> str:
    coll = db[coll_name]
    try:
        existing = {ix["name"] for ix in coll.list_search_indexes()}
    except OperationFailure:
        return "unsupported (not an Atlas cluster?)"
    if name in existing:
        return "exists"
    try:
        coll.create_search_index({"name": name, "type": kind, "definition": definition})
        return "creating"
    except OperationFailure as exc:
        return f"failed: {exc.details.get('errmsg', exc)[:80]}"


def wait_for_search_indexes(timeout: float = 180.0) -> bool:
    """Block until every vector index reports queryable, or timeout."""
    db = get_db()
    deadline = time.time() + timeout
    targets = [(c, s["name"]) for c, s in VECTOR_INDEXES.items()]
    while time.time() < deadline:
        pending = []
        for coll_name, ix_name in targets:
            try:
                ixs = {i["name"]: i for i in db[coll_name].list_search_indexes()}
            except OperationFailure:
                return False
            info = ixs.get(ix_name)
            if not info or not info.get("queryable"):
                pending.append(ix_name)
        if not pending:
            return True
        time.sleep(3)
    return False
