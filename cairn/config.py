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
    verdict_model: str
    brief_model: str
    rule_model: str

    embed_backend: str  # "auto" | "voyage" | "hash"
    embed_model: str
    embed_dims: int
    voyage_api_key: str | None

    # Memory gate thresholds. Cosine similarity, 0-1.
    dup_threshold: float
    verdict_reuse_threshold: float
    rule_match_threshold: float

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
        verdict_model=os.getenv("CAIRN_VERDICT_MODEL", "openai/gpt-oss-120b"),
        brief_model=os.getenv("CAIRN_BRIEF_MODEL", "anthropic/claude-sonnet-4.6"),
        rule_model=os.getenv("CAIRN_RULE_MODEL", "anthropic/claude-sonnet-4.6"),
        embed_backend=os.getenv("CAIRN_EMBED_BACKEND", "auto"),
        embed_model=os.getenv("CAIRN_EMBED_MODEL", "voyage-3.5"),
        embed_dims=int(os.getenv("CAIRN_EMBED_DIMS", "1024")),
        voyage_api_key=os.getenv("VOYAGE_API_KEY") or None,
        dup_threshold=float(os.getenv("CAIRN_DUP_THRESHOLD", "0.86")),
        verdict_reuse_threshold=float(os.getenv("CAIRN_VERDICT_THRESHOLD", "0.90")),
        rule_match_threshold=float(os.getenv("CAIRN_RULE_THRESHOLD", "0.75")),
        candidates_per_run=int(os.getenv("CAIRN_CANDIDATES", "12")),
        verdict_workers=int(os.getenv("CAIRN_VERDICT_WORKERS", "4")),
        crawl_max_pages=int(os.getenv("CAIRN_MAX_PAGES", "60")),
    )


SETTINGS = load_settings()
