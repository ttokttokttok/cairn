"""Pipeline orchestration with checkpoints at every stage boundary."""

from __future__ import annotations

import json
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from pymongo.errors import OperationFailure, PyMongoError
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import store
from .agents import BRIEFER, GRADER, RULEMAKER, SCOUT
from .config import SETTINGS
from .crawl import crawl_site, normalize_domain
from .db import get_db
from .gate import evaluate, note_rule_applied

console = Console()

# Used to price what a memory veto saved. Seeded with a conservative default and
# replaced by this run's measured average as soon as any verdict completes.
DEFAULT_VERDICT_COST = 2000


class TokenMeter:
    def __init__(self) -> None:
        self.total = 0
        self.verdict_tokens = 0
        self.verdict_count = 0
        self._lock = threading.Lock()

    def record(self, tokens: int, is_verdict: bool = False) -> None:
        with self._lock:
            self.total += tokens
            if is_verdict:
                self.verdict_tokens += tokens
                self.verdict_count += 1

    @property
    def avg_verdict_cost(self) -> int:
        if not self.verdict_count:
            return DEFAULT_VERDICT_COST
        return int(self.verdict_tokens / self.verdict_count)


def run_pipeline(
    domain: str,
    resume_run_id: str | None = None,
    max_pages: int | None = None,
    candidates: int | None = None,
) -> dict[str, Any]:
    site, _ = normalize_domain(domain)
    max_pages = max_pages or SETTINGS.crawl_max_pages
    candidates = candidates or SETTINGS.candidates_per_run

    if resume_run_id:
        run = store.get_run(resume_run_id)
        if not run:
            raise SystemExit(f"No such run: {resume_run_id}")
        run_id, site = resume_run_id, run["site"]
        start_stage = run.get("stage", "inventory")
        cp = run.get("checkpoint", {})
        console.print(
            f"[bold yellow]resuming[/] {run_id} from stage [bold]{start_stage}[/]"
        )
    else:
        run_id = store.new_run(site)
        start_stage, cp = "inventory", {}

    run_doc = store.get_run(run_id) or {}
    cold = run_doc.get("coldStart", True)
    meter = TokenMeter()

    console.print(
        Panel.fit(
            f"[bold]{site}[/]\nrun [cyan]{run_id}[/]\n"
            + ("[yellow]COLD START[/] — no memory of this site"
               if cold else "[green]WARM START[/] — memory available"),
            title="cairn",
            border_style="cyan",
        )
    )

    def at_or_after(stage: str) -> bool:
        return store.STAGES.index(start_stage) <= store.STAGES.index(stage)

    # --- 0. inventory --------------------------------------------------------
    if at_or_after("inventory"):
        _stage_inventory(site, run_id, max_pages, cold)
        store.checkpoint(run_id, "candidates")

    inventory = store.page_inventory(site)

    # --- 1. candidates -------------------------------------------------------
    if at_or_after("candidates"):
        cand = _stage_candidates(site, run_id, inventory, candidates, meter)
        store.checkpoint(run_id, "gate", {"candidates": cand})
    else:
        cand = cp.get("candidates", [])

    # --- 2. memory gate ------------------------------------------------------
    if at_or_after("gate"):
        survivors = _stage_gate(site, run_id, cand, meter)
        store.checkpoint(run_id, "verdicts", {"survivors": survivors})
    else:
        survivors = cp.get("survivors", [])

    # --- 3 + 4. verdicts, briefs (coupled by a change stream) ----------------
    if at_or_after("verdicts"):
        winnable = _stage_verdicts_and_briefs(site, run_id, survivors, meter)
        store.checkpoint(run_id, "rules", {"winnable": winnable})

    # --- 5. rule induction ---------------------------------------------------
    if at_or_after("rules"):
        _stage_rules(site, run_id, meter)

    store.finish_run(run_id)
    store.add_tokens(run_id, meter.total)
    return {"runId": run_id, "site": site, "tokens": meter.total}


# --- stages ------------------------------------------------------------------


def _stage_inventory(site: str, run_id: str, max_pages: int, cold: bool) -> None:
    console.rule("[bold]0 · site memory")
    known = store.known_urls(site)
    with console.status("crawling sitemap…"):
        crawl = crawl_site(site, max_pages=max_pages)
    store.upsert_site(crawl)

    fresh = [p for p in crawl.pages if p.url not in known]
    if cold or not known:
        written = store.store_pages(site, crawl.pages)
        console.print(
            f"  cold crawl · [bold]{written}[/] pages indexed "
            f"(source: {crawl.source})"
        )
    else:
        written = store.store_pages(site, fresh)
        console.print(
            f"  warm · [bold]{len(known)}[/] pages already in memory, "
            f"[bold]{written}[/] new — skipped re-indexing "
            f"{len(crawl.pages) - written} pages"
        )


def _stage_candidates(
    site: str, run_id: str, inventory: list[dict], n: int, meter: TokenMeter
) -> list[dict]:
    console.rule("[bold]1 · candidate topics")
    listing = "\n".join(
        f"- {p.get('title', '')} ({p.get('url', '')})" for p in inventory[:60]
    ) or "(no pages indexed — treat this as a brand-new site)"

    prompt = (
        f"Site: {site}\n\n"
        f"Existing content inventory ({len(inventory)} pages):\n{listing}\n\n"
        f"Propose exactly {n} candidate topics for this site."
    )
    with console.status(f"Hermes scouting {n} candidates…"):
        reply = SCOUT.run(prompt, task_id=f"{run_id}:candidates")
    meter.record(reply.tokens)
    store.save_trajectory(run_id, "candidates", reply.messages)

    cands = reply.json(default=[]) or []
    cands = [c for c in cands if isinstance(c, dict) and c.get("query")][:n]
    if not cands:
        # A run with zero candidates would sail through every later stage and
        # report success having done nothing. Fail where the fault actually is.
        raise SystemExit(
            f"SCOUT returned no usable topics from {reply.tokens:,} tokens.\n"
            f"Raw reply: {reply.text[:300]!r}\n"
            f"Model was {SCOUT.model}. Reasoning-only models often emit no "
            f"visible content for this task -- try CAIRN_SCOUT_MODEL=<instruct model>."
        )
    mark = "~" if reply.estimated else ""
    console.print(f"  Hermes proposed [bold]{len(cands)}[/] topics "
                  f"([dim]{mark}{reply.tokens:,} tokens[/])")
    return cands


def _stage_gate(
    site: str, run_id: str, cands: list[dict], meter: TokenMeter
) -> list[dict]:
    console.rule("[bold]2 · memory gate[/] [dim](zero API calls)[/]")
    survivors, vetoed = [], []
    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
    table.add_column("", width=2)
    table.add_column("topic", overflow="fold")
    table.add_column("verdict")
    table.add_column("evidence from MongoDB", overflow="fold", style="dim")

    for c in cands:
        d = evaluate(site, c["query"])
        saved = meter.avg_verdict_cost if not d.passed else 0
        store.store_topic(site, run_id, d, stage_cost=saved)
        if d.passed:
            survivors.append(c)
            table.add_row("[green]✓[/]", c["query"], "[green]proceed[/]", d.reason)
        else:
            note_rule_applied(d)
            vetoed.append(d)
            store.add_tokens(run_id, 0, saved=saved)
            table.add_row(
                "[red]✗[/]",
                c["query"],
                f"[red]{d.reason}[/]",
                f"{d.evidence}  [{d.kind} · {d.score:.2f}]",
            )

    console.print(table)
    saved_total = len(vetoed) * meter.avg_verdict_cost
    console.print(
        f"\n  [bold red]{len(vetoed)}[/] vetoed by memory before spending anything · "
        f"[bold green]{len(survivors)}[/] proceed · "
        f"~[bold]{saved_total:,}[/] tokens not spent"
    )
    return survivors


def _stage_verdicts_and_briefs(
    site: str, run_id: str, survivors: list[dict], meter: TokenMeter
) -> list[str]:
    console.rule("[bold]3 · live SERP verdicts[/] → [bold]4 · briefs")
    if not survivors:
        console.print("  [dim]nothing survived the gate — memory did all the work[/]")
        return []

    # Resume lands here with the stage's checkpoint, but grading is per-topic:
    # a crash after N of M verdicts would otherwise re-pay for those N. The
    # verdicts already in MongoDB are the record of what was finished.
    done = set(get_db().verdicts.distinct("query", {"runId": run_id}))
    if done:
        survivors = [c for c in survivors if c["query"] not in done]
        console.print(
            f"  [green]{len(done)}[/] verdict(s) already in MongoDB from before "
            f"the crash — not re-grading them"
        )
        if not survivors:
            console.print("  [dim]all verdicts already graded[/]")

    winnable_q: queue.Queue = queue.Queue()
    stop = threading.Event()
    watcher = threading.Thread(
        target=_watch_verdicts,
        args=(site, run_id, winnable_q, stop),
        daemon=True,
    )
    watcher.start()

    graded: list[str] = []
    with ThreadPoolExecutor(max_workers=SETTINGS.verdict_workers) as pool:
        futures = {
            pool.submit(_grade_one, site, run_id, c): c["query"] for c in survivors
        }
        for fut in as_completed(futures):
            query = futures[fut]
            try:
                verdict, tokens = fut.result()
            except Exception as exc:  # noqa: BLE001
                console.print(f"  [red]![/] {query}: grading failed ({exc})")
                continue
            meter.record(tokens, is_verdict=True)
            grade = verdict.get("grade", "?")
            colour = {"WINNABLE": "green", "CONTESTED": "yellow"}.get(grade, "red")
            console.print(
                f"  [{colour}]{grade:<10}[/] {query}\n"
                f"             [dim]{verdict.get('reason', '')[:150]}[/]"
            )
            graded.append(query)

    # Give the change stream a moment to deliver the last inserts.
    time.sleep(1.5)
    stop.set()

    winnable: list[dict] = []
    seen: set[str] = set()
    while not winnable_q.empty():
        doc = winnable_q.get()
        if doc.get("query") not in seen:
            seen.add(doc.get("query"))
            winnable.append(doc)

    # The change stream only fires on inserts, so winners graded before a crash
    # would never reach the brief stage on resume. Pick up any that still have
    # no brief.
    db = get_db()
    for v in db.verdicts.find({"runId": run_id, "grade": "WINNABLE"}):
        if v["query"] in seen:
            continue
        if db.briefs.find_one({"site": site, "query": v["query"]}, {"_id": 1}):
            continue
        seen.add(v["query"])
        winnable.append(v)
        console.print(f"  [green]recovered[/] un-briefed winner: {v['query']}")

    if not winnable:
        console.print("\n  [dim]no WINNABLE verdicts — no briefs this run[/]")
        return []

    console.print(
        f"\n  change stream delivered [bold]{len(winnable)}[/] WINNABLE verdict(s) "
        f"to the brief stage"
    )
    for v in winnable:
        _write_brief(site, run_id, v, meter)
    return [v["query"] for v in winnable]


def _watch_verdicts(
    site: str, run_id: str, out: queue.Queue, stop: threading.Event
) -> None:
    """Change stream on `verdicts`: briefs start the moment a winner lands.

    Grading and briefing stay decoupled — the brief stage never waits for the
    whole grading batch. Falls back to polling where change streams are absent.
    """
    db = get_db()
    try:
        with db.verdicts.watch(
            [
                {
                    "$match": {
                        "operationType": "insert",
                        "fullDocument.runId": run_id,
                        "fullDocument.grade": "WINNABLE",
                    }
                }
            ],
            max_await_time_ms=1000,
        ) as stream:
            while not stop.is_set():
                change = stream.try_next()
                if change:
                    out.put(change["fullDocument"])
    except (OperationFailure, PyMongoError):
        _poll_verdicts(site, run_id, out, stop)


def _poll_verdicts(
    site: str, run_id: str, out: queue.Queue, stop: threading.Event
) -> None:
    seen: set[Any] = set()
    db = get_db()
    while not stop.is_set():
        for doc in db.verdicts.find({"runId": run_id, "grade": "WINNABLE"}):
            if doc["_id"] not in seen:
                seen.add(doc["_id"])
                out.put(doc)
        time.sleep(1.0)


def _grade_one(site: str, run_id: str, cand: dict) -> tuple[dict, int]:
    """One SERP verdict. Runs in its own thread with its own AIAgent."""
    query = cand["query"]
    reply = GRADER.run(
        f"Query to grade: {query}\n"
        f"Site that would target it: {site}\n"
        f"Scout's rationale: {cand.get('rationale', '')}\n\n"
        "Search the live web and grade it.",
        task_id=f"{run_id}:verdict",
    )
    verdict = reply.json(default={}) or {}
    verdict.setdefault("grade", "CONTESTED")
    verdict.setdefault("reason", reply.text[:300])
    store.store_verdict(site, run_id, query, verdict)
    return verdict, reply.tokens


def _write_brief(site: str, run_id: str, verdict: dict, meter: TokenMeter) -> None:
    from .gate import knn

    query = verdict["query"]
    # Internal link targets come out of the database, so they cannot be invented.
    links = knn("pages", site, query, limit=6, projection={"url": 1, "title": 1})
    link_list = "\n".join(
        f"- {p.get('url')} — {p.get('title', '')}" for p in links
    ) or "(no internal pages indexed)"

    prompt = (
        f"Target query: {query}\n"
        f"Site: {site}\n\n"
        f"SERP verdict: {json.dumps(verdict.get('reason', ''))}\n"
        f"Intent: {verdict.get('intent', '')}\n"
        f"Dominant format: {verdict.get('dominantFormat', '')}\n"
        f"Currently ranking: {', '.join(verdict.get('competitors', []) or [])}\n\n"
        f"REAL internal pages available to link (use only these URLs):\n{link_list}\n\n"
        "Write the brief."
    )
    with console.status(f"briefing “{query}”…"):
        reply = BRIEFER.run(prompt, task_id=f"{run_id}:brief")
    meter.record(reply.tokens)
    brief = reply.json(default={}) or {"raw": reply.text}
    store.store_brief(site, run_id, query, brief)
    console.print(
        f"  [bold green]brief[/] {query} — angle: "
        f"[italic]{str(brief.get('angle', ''))[:110]}[/]"
    )


def _stage_rules(site: str, run_id: str, meter: TokenMeter) -> None:
    console.rule("[bold]5 · rule induction")
    db = get_db()
    verdicts = list(
        db.verdicts.find(
            {"runId": run_id},
            {"query": 1, "grade": 1, "reason": 1, "dominantFormat": 1, "_id": 0},
        )
    )
    rejections = list(
        db.briefs.find(
            {"site": site, "status": "rejected"},
            {"query": 1, "humanFeedback": 1, "_id": 0},
        ).limit(20)
    )
    if not verdicts and not rejections:
        console.print("  [dim]nothing new to generalize from[/]")
        return

    prompt = (
        f"Site: {site}\n\n"
        f"SERP verdicts from this run:\n{json.dumps(verdicts, indent=2)}\n\n"
        f"Human rejections on this site:\n{json.dumps(rejections, indent=2)}\n\n"
        "Induce reusable rules."
    )
    with console.status("inducing rules…"):
        reply = RULEMAKER.run(prompt, task_id=f"{run_id}:rules")
    meter.record(reply.tokens)
    rules = reply.json(default=[]) or []
    written = store.store_rules(site, run_id, rules)
    for r in rules:
        console.print(f"  [magenta]rule[/] {r.get('rule', '')} "
                      f"[dim](conf {r.get('confidence', 0)})[/]")
    console.print(
        f"\n  [bold]{written}[/] new rule(s) written · "
        f"{len(rules) - written} existing rule(s) reinforced"
    )
