"""Embeddings, with a fallback ladder so a missing key degrades rather than blocks.

  voyage  Voyage AI (MongoDB's own embedding models). The real path.
  hash    Deterministic hashed bag-of-words. No network, no key. Lexical
          similarity only -- it will catch "vector search" vs "vector search
          explained" but not "hybrid search" vs "combining BM25 and vectors".
          Present so the pipeline runs end-to-end on a laptop with no keys.

`auto` picks voyage when VOYAGE_API_KEY is set, otherwise hash.
"""

from __future__ import annotations

import hashlib
import math
import re
from functools import lru_cache

import httpx

from .config import SETTINGS

_WORD = re.compile(r"[a-z0-9']+")


def active_backend() -> str:
    if SETTINGS.embed_backend != "auto":
        return SETTINGS.embed_backend
    return "voyage" if SETTINGS.voyage_api_key else "hash"


def uses_atlas_autoembed() -> bool:
    """True when Atlas generates the vectors server-side.

    In this mode we never compute, store, or send an embedding: documents carry
    the source text, and $vectorSearch takes `query` text plus a `model`.
    """
    return active_backend() == "atlas"


# The field on each collection that Atlas embeds when autoEmbed is on.
AUTOEMBED_PATH = {
    "pages": "embedText",
    "verdicts": "query",
    "rules": "rule",
    "gsc_performance": "query",
}


def embed(texts: list[str], input_type: str = "document") -> list[list[float]]:
    """Embed a batch. `input_type` is "document" or "query"."""
    if not texts:
        return []
    backend = active_backend()
    if backend == "voyage":
        try:
            return _voyage(texts, input_type)
        except Exception as exc:  # noqa: BLE001 - degrade, never block a run
            print(f"  ! voyage embedding failed ({exc}); falling back to hash")
            return [_hash_embed(t) for t in texts]
    return [_hash_embed(t) for t in texts]


def embed_one(text: str, input_type: str = "query") -> list[float]:
    return embed([text], input_type=input_type)[0]


def _voyage(texts: list[str], input_type: str) -> list[list[float]]:
    resp = httpx.post(
        "https://api.voyageai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {SETTINGS.voyage_api_key}"},
        json={
            "input": texts,
            "model": SETTINGS.embed_model,
            "input_type": input_type,
            "output_dimension": SETTINGS.embed_dims,
        },
        timeout=60.0,
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    return [row["embedding"] for row in sorted(data, key=lambda r: r["index"])]


@lru_cache(maxsize=4096)
def _hash_embed_cached(text: str, dims: int) -> tuple[float, ...]:
    vec = [0.0] * dims
    tokens = _WORD.findall(text.lower())
    # Unigrams plus bigrams: bigrams give the vector a little word-order signal,
    # which matters for keyword-shaped text like "vector search pricing".
    grams = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
    for gram in grams:
        h = hashlib.blake2b(gram.encode(), digest_size=8).digest()
        idx = int.from_bytes(h[:4], "big") % dims
        sign = 1.0 if h[4] & 1 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return tuple(v / norm for v in vec)


def _hash_embed(text: str) -> list[float]:
    return list(_hash_embed_cached(text, SETTINGS.embed_dims))


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)
