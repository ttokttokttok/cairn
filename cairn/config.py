"""Settings, read once from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    mongodb_uri: str
    db_name: str

    # Model routing: verdicts are high-volume and structured, briefs are
    # low-volume and quality-sensitive. Paying for a big model on every SERP
    # read would burn the OpenRouter credit for no gain.
    scout_model: str
    verdict_model: str
    brief_model: str
    rule_model: str

    # "atlas"  -- Atlas Automated Embedding. Text goes in, Atlas generates and
    #             maintains the vectors server-side. No embedding pipeline, no
    #             extra API key. Requires an M10+ dedicated cluster.
    # "voyage"  -- client-side embeddings via the Voyage API.
    # "hash"    -- deterministic local fallback, lexical similarity only.
    embed_backend: str
    embed_model: str  # client-side model (voyage backend)
    autoembed_model: str  # server-side model (atlas backend)
    embed_dims: int
    voyage_api_key: str | None

    # Memory gate thresholds. Cosine similarity, 0-1.
    dup_threshold: float
    verdict_reuse_threshold: float
    rule_match_threshold: float
    rule_veto_enabled: bool

    candidates_per_run: int
    verdict_workers: int
    crawl_max_pages: int

    @property
    def has_mongo(self) -> bool:
        return bool(self.mongodb_uri)


def load_settings() -> Settings:
    return Settings(
        mongodb_uri=os.getenv("MONGODB_URI", ""),
        db_name=os.getenv("CAIRN_DB", "cairn"),
        # SCOUT runs once per run and must emit a long structured list.
        # gpt-oss-120b reliably returns reasoning with no visible content for
        # this shape of task, so the generation roles get the stronger tier.
        scout_model=os.getenv("CAIRN_SCOUT_MODEL", "anthropic/claude-sonnet-4.6"),
        # Cheap tier, but it must reliably emit visible JSON: gpt-oss-120b
        # returned reasoning-only responses on roughly a quarter of calls,
        # which stores a garbage verdict as durable memory.
        verdict_model=os.getenv("CAIRN_VERDICT_MODEL", "anthropic/claude-haiku-4.5"),
        brief_model=os.getenv("CAIRN_BRIEF_MODEL", "anthropic/claude-sonnet-4.6"),
        rule_model=os.getenv("CAIRN_RULE_MODEL", "anthropic/claude-sonnet-4.6"),
        embed_backend=os.getenv("CAIRN_EMBED_BACKEND", "atlas"),
        embed_model=os.getenv("CAIRN_EMBED_MODEL", "voyage-3.5"),
        autoembed_model=os.getenv("CAIRN_AUTOEMBED_MODEL", "voyage-4"),
        embed_dims=int(os.getenv("CAIRN_EMBED_DIMS", "1024")),
        voyage_api_key=os.getenv("VOYAGE_API_KEY") or None,
        # Calibrated against voyage-4 via Atlas autoEmbed, which normalizes
        # scores into roughly 0.55 (unrelated) .. 0.90 (identical). Measured:
        #   dup      0.825 same-intent must veto / 0.733 new topic must pass
        #   verdict  0.840 same intent must reuse / 0.712 same-shape-different-
        #            topic must not. The old 0.90 default never fired at all.
        #   rule     0.636 should fire / 0.570 should not -- the thinnest margin
        #            of the three, so rules also require confidence >= 0.6.
        dup_threshold=float(os.getenv("CAIRN_DUP_THRESHOLD", "0.80")),
        verdict_reuse_threshold=float(os.getenv("CAIRN_VERDICT_THRESHOLD", "0.82")),
        rule_match_threshold=float(os.getenv("CAIRN_RULE_THRESHOLD", "0.62")),
        # Off by default, on evidence. Rule-to-query matching compares a category
        # sentence against a short query and the margins do not separate:
        # measured true positives scored 0.636-0.661, while a verified FALSE
        # positive (a privacy/GDPR rule matching "umami vs fathom for agencies")
        # scored 0.730 -- higher than any true positive. No threshold can split
        # those, so rules steer SCOUT instead, where the same data is unambiguous.
        rule_veto_enabled=os.getenv("CAIRN_RULE_VETO", "false").lower() == "true",
        candidates_per_run=int(os.getenv("CAIRN_CANDIDATES", "12")),
        verdict_workers=int(os.getenv("CAIRN_VERDICT_WORKERS", "4")),
        crawl_max_pages=int(os.getenv("CAIRN_MAX_PAGES", "60")),
    )


SETTINGS = load_settings()
