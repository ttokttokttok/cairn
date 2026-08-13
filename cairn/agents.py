"""Hermes agent wrappers -- one specialized role per pipeline stage.

The central architectural choice: `skip_memory=True` and `skip_context_files=True`.
We deliberately switch off Hermes's private, local, single-session memory so that
MongoDB is the agent's *only* memory substrate. Hermes supplies reasoning and
live web tools; every durable thing it learns lives in Atlas where the next run,
on any machine, can query it.

AIAgent instances are not thread-safe -- construct a fresh one per thread.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from .config import SETTINGS

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


class MissingCredentials(RuntimeError):
    pass


def require_llm_key() -> None:
    if not any(
        os.getenv(k)
        for k in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")
    ):
        raise MissingCredentials(
            "No LLM key found. Set OPENROUTER_API_KEY in .env "
            "(or OPENAI_API_KEY / ANTHROPIC_API_KEY for direct provider access)."
        )


def estimate_tokens(messages: list[dict[str, Any]], text: str) -> int:
    """Rough token count from message text, ~4 chars per token.

    Needed because OpenRouter usage reporting is provider-dependent: the same
    model and parameters return real usage on one routing and nothing on the
    next. Reporting 0 in that case would make the memory saving look infinite,
    so we estimate and label it rather than publish a number we didn't measure.
    """
    chars = len(text or "")
    for m in messages or []:
        content = m.get("content")
        if isinstance(content, str):
            chars += len(content)
        elif isinstance(content, list):  # tool-call blocks
            chars += sum(len(str(part)) for part in content)
    return chars // 4


@dataclass
class AgentReply:
    text: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    tokens: int = 0
    estimated: bool = False

    def json(self, default: Any = None) -> Any:
        """Pull the first JSON value out of the reply.

        Agents wrap JSON in prose or fences often enough that parsing the raw
        text directly fails a meaningful fraction of the time, and a failed
        parse costs a whole stage.
        """
        for candidate in _json_candidates(self.text):
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
        return default


def _json_candidates(text: str) -> list[str]:
    out = [m.group(1).strip() for m in _JSON_BLOCK.finditer(text)]
    out.append(text.strip())
    for opener, closer in (("[", "]"), ("{", "}")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            out.append(text[start : end + 1])
    return out


class HermesRole:
    """A single-purpose Hermes agent, built fresh per call for thread safety."""

    def __init__(
        self,
        name: str,
        system_prompt: str,
        model: str,
        toolsets: list[str] | None = None,
        max_iterations: int = 12,
    ) -> None:
        self.name = name
        self.system_prompt = system_prompt
        self.model = model
        self.toolsets = toolsets or []
        self.max_iterations = max_iterations

    def _build(self):
        from run_agent import AIAgent

        kwargs: dict[str, Any] = dict(
            model=self.model,
            quiet_mode=True,
            ephemeral_system_prompt=self.system_prompt,
            max_iterations=self.max_iterations,
            # MongoDB is the memory. Hermes's local stores stay off.
            skip_memory=True,
            skip_context_files=True,
            skip_background_review=True,
            # `save_trajectories` is deliberately NOT passed. Supplying it at
            # all -- True or False -- makes Hermes report total_tokens=0 and
            # session_total_tokens=0 for the whole call, which silently zeroes
            # the token ledger this project is measured on. Verified against
            # hermes-agent 0.20.0: omitted=1289 tokens, False=0, True=0.
        )
        if self.toolsets:
            kwargs["enabled_toolsets"] = self.toolsets
        return AIAgent(**kwargs)

    def run(
        self,
        user_message: str,
        task_id: str,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> AgentReply:
        require_llm_key()
        agent = self._build()
        result = agent.run_conversation(
            user_message=user_message,
            task_id=task_id,
            conversation_history=conversation_history,
        )
        # run_conversation reports usage for this call; the agent attribute is
        # the session accumulator. Prefer the per-call number and fall back,
        # because an under-counted ledger silently overstates the memory saving
        # -- the one number this whole project is judged on.
        text = result.get("final_response") or ""
        messages = result.get("messages") or []
        tokens = int(
            result.get("total_tokens")
            or getattr(agent, "session_total_tokens", 0)
            or 0
        )
        estimated = tokens == 0
        if estimated:
            tokens = estimate_tokens(messages, text)
        return AgentReply(
            text=text, messages=messages, tokens=tokens, estimated=estimated
        )


# --- role definitions --------------------------------------------------------

SCOUT = HermesRole(
    name="scout",
    model=SETTINGS.scout_model,
    toolsets=["web"],
    max_iterations=14,
    system_prompt="""You are an SEO topic scout.

Given a site's existing content inventory, propose candidate topics the site could
realistically rank for. Use web search to check what is currently being discussed
in this vertical.

Rules:
- Stay inside the site's topical strike zone. A site with no authority in a subject
  will not rank for it, however good the article is.
- Prefer commercial-investigation and problem-aware queries over broad definitional
  ones. Definitional queries are usually owned by documentation and encyclopedias.
- Favour specific long-tail shapes the site can actually win: comparisons
  ("X vs Y"), migrations ("switching from X"), and "X for <platform/segment>".
  Broad head terms are a waste of a slot.
- Do not propose a topic the inventory already covers.

Return ONLY a JSON array, no prose:
[{"query": "the search query a real person types",
  "rationale": "one sentence on why this site can win it",
  "intent": "informational|commercial|transactional"}]""",
)

GRADER = HermesRole(
    name="grader",
    model=SETTINGS.verdict_model,
    toolsets=["web"],
    max_iterations=10,
    system_prompt="""You are an SEO difficulty grader.

Search the live web for the exact query you are given and examine what actually
ranks. Then grade honestly -- an optimistic grade wastes the user's money on
content that will never rank.

Grade WINNABLE / CONTESTED / UNWINNABLE. Calibrate honestly in BOTH directions --
grading everything UNWINNABLE is as useless as grading everything WINNABLE:
- UNWINNABLE: the SERP is genuinely locked. Official docs for the exact product
  being asked about, or Wikipedia, or the intent cannot be served by this site.
- CONTESTED: strong incumbents, but a real gap in format, freshness, or depth.
- WINNABLE: long-tail or specific enough that no incumbent owns it, results are
  thin/outdated/off-intent, or the competitors are of comparable authority.
  A specific comparison, migration, or "X for Y" query is usually WINNABLE even
  when broad head terms in the same topic are not.

Return ONLY JSON, no prose:
{"grade": "WINNABLE|CONTESTED|UNWINNABLE",
 "reason": "one or two sentences citing what you actually saw on the SERP",
 "intent": "informational|commercial|transactional",
 "dominant_format": "docs|listicle|comparison|how-to|product|forum|news",
 "competitors": ["domain1.com", "domain2.com"]}""",
)

BRIEFER = HermesRole(
    name="briefer",
    model=SETTINGS.brief_model,
    toolsets=["web"],
    max_iterations=12,
    system_prompt="""You are a senior SEO strategist writing a content brief.

You will be given a target query, a SERP verdict, and a list of REAL internal pages
retrieved from the site's own inventory. Every internal link you recommend must come
from that list -- never invent a URL.

Return ONLY JSON, no prose:
{"target_keyword": "...",
 "intent": "informational|commercial|transactional",
 "why_now": "...",
 "angle": "the differentiated take that beats what already ranks",
 "competing_pages": ["url or domain currently ranking"],
 "internal_links": [{"url": "from the supplied inventory", "anchor": "..."}],
 "outline": ["H2 section", "H2 section"],
 "do_not_cannibalize": "which of our existing pages this must not overlap, and how to keep them distinct",
 "information_gain": "what this piece can say that no competitor can"}""",
)

RULEMAKER = HermesRole(
    name="rulemaker",
    model=SETTINGS.rule_model,
    toolsets=[],
    max_iterations=4,
    system_prompt="""You induce reusable SEO rules from observed outcomes.

You are given SERP verdicts and human rejections from one run on one site. Write
generalizations that would let a future run skip doomed topics without spending an
API call.

A good rule is specific enough to act on and general enough to fire again:
  good  "Definitional 'what is X' queries lose to official docs for this site."
  bad   "The query about vector search was unwinnable."

Every rule needs a polarity, and this matters -- `avoid` rules are used to VETO
future topics before any API call, so a mislabelled rule silently kills good work:
  "avoid"  -- topics matching this rule should NOT be pursued
  "prefer" -- topics matching this rule are GOOD targets (recorded to steer
              future scouting; never used to veto)

Only state what the evidence supports. Two or three rules is a good run; zero is a
valid answer if nothing generalizes.

Return ONLY a JSON array, no prose:
[{"rule": "...", "polarity": "avoid|prefer", "confidence": 0.0-1.0,
  "evidence": "what you saw that supports it"}]""",
)
