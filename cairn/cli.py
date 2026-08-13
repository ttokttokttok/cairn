"""cairn — an SEO agent that gets cheaper every run."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import store
from .config import SETTINGS
from .db import (
    ensure_collections,
    ensure_search_indexes,
    get_db,
    wait_for_search_indexes,
)
from .embed import active_backend

app = typer.Typer(
    add_completion=False,
    help="An SEO agent that gets cheaper every run, because MongoDB remembers "
    "what didn't work.",
    no_args_is_help=True,
)
console = Console()


@app.command()
def init(
    wait: bool = typer.Option(False, help="Block until vector indexes are queryable."),
) -> None:
    """Create collections and Atlas search indexes. Idempotent."""
    created = ensure_collections()
    console.print(
        f"collections ready"
        + (f" (created: {', '.join(created)})" if created else " (all existed)")
    )
    status = ensure_search_indexes()
    table = Table(box=None, header_style="bold")
    table.add_column("index")
    table.add_column("status")
    for name, state in status.items():
        table.add_row(name, state)
    console.print(table)
    console.print(f"embedding backend: [bold]{active_backend()}[/]")
    if wait:
        with console.status("waiting for vector indexes to become queryable…"):
            ok = wait_for_search_indexes()
        console.print("indexes queryable" if ok else "[yellow]timed out — the gate "
                      "will fall back to exact cosine scan[/]")


@app.command()
def run(
    domain: str = typer.Argument(..., help="Any domain, e.g. example.com"),
    resume: str = typer.Option(None, help="Resume a run by id."),
    pages: int = typer.Option(None, help="Max pages to crawl."),
    candidates: int = typer.Option(None, help="Candidate topics to propose."),
) -> None:
    """Run the pipeline against a domain."""
    from .pipeline import run_pipeline

    ensure_collections()
    result = run_pipeline(
        domain, resume_run_id=resume, max_pages=pages, candidates=candidates
    )
    console.print(
        Panel.fit(
            f"run [cyan]{result['runId']}[/] complete\n"
            f"tokens spent this run: [bold]{result['tokens']:,}[/]\n\n"
            f"[dim]cairn review {result['site']}   ·   cairn stats {result['site']}[/]",
            border_style="green",
        )
    )


@app.command()
def review(domain: str) -> None:
    """Approve or reject pending briefs. Rejections become memory."""
    site, _ = _site(domain)
    db = get_db()
    pending = list(db.briefs.find({"site": site, "status": "pending_approval"}))
    if not pending:
        console.print("no briefs pending approval")
        return

    for doc in pending:
        b = doc.get("brief", {})
        body = "\n".join(
            filter(
                None,
                [
                    f"[bold]intent[/] {b.get('intent', '')}",
                    f"[bold]why now[/] {b.get('why_now', '')}",
                    f"[bold]angle[/] {b.get('angle', '')}",
                    f"[bold]information gain[/] {b.get('information_gain', '')}",
                    f"[bold]outline[/] " + " · ".join(b.get("outline", []) or []),
                    f"[bold]internal links[/] "
                    + ", ".join(
                        f"{l.get('anchor', '')} → {l.get('url', '')}"
                        for l in (b.get("internal_links") or [])
                    ),
                    f"[bold]do not cannibalize[/] {b.get('do_not_cannibalize', '')}",
                ],
            )
        )
        console.print(Panel(body, title=doc["query"], border_style="cyan"))
        choice = typer.prompt("approve / reject / skip", default="skip")
        if choice.startswith("a"):
            db.briefs.update_one({"_id": doc["_id"]}, {"$set": {"status": "approved"}})
        elif choice.startswith("r"):
            why = typer.prompt("why? (this becomes a rule)")
            db.briefs.update_one(
                {"_id": doc["_id"]},
                {"$set": {"status": "rejected", "humanFeedback": why}},
            )


@app.command()
def briefs(
    domain: str,
    status: str = typer.Option(None, help="Filter: pending_approval, approved, rejected."),
    full: bool = typer.Option(False, "--full", help="Include the section outline."),
) -> None:
    """Read the briefs. This is what the agent actually produced for you."""
    site, _ = _site(domain)
    query: dict = {"site": site}
    if status:
        query["status"] = status
    docs = list(get_db().briefs.find(query).sort("createdAt", -1))
    if not docs:
        console.print(f"no briefs for {site} yet — run `cairn run {site}` first")
        return

    for doc in docs:
        b = doc.get("brief") or {}
        colour = {"approved": "green", "rejected": "red"}.get(doc.get("status"), "cyan")
        lines = [
            f"[bold]{doc.get('status', '').replace('_', ' ')}[/]  ·  "
            f"{b.get('intent', '')}  ·  target: {b.get('target_keyword', '')}",
            "",
            f"[bold]Angle[/]\n{b.get('angle', '—')}",
            f"\n[bold]Why now[/]\n{b.get('why_now', '—')}",
        ]
        if b.get("information_gain"):
            lines.append(f"\n[bold]Information gain[/]\n{b['information_gain']}")
        links = b.get("internal_links") or []
        lines.append(
            "\n[bold]Internal links[/]\n"
            + (
                "\n".join(
                    f"  {l.get('anchor', '')} → {l.get('url', '')}"
                    for l in links
                    if isinstance(l, dict)
                )
                if links
                else "  [dim]none — no pages indexed, so nothing real to link[/]"
            )
        )
        if full and b.get("outline"):
            lines.append(
                "\n[bold]Outline[/]\n"
                + "\n".join(f"  {i}. {s}" for i, s in enumerate(b["outline"], 1))
            )
        if b.get("do_not_cannibalize"):
            lines.append(f"\n[bold]Do not cannibalize[/]\n{b['do_not_cannibalize']}")
        if doc.get("humanFeedback"):
            lines.append(f"\n[bold]Your rejection reason[/]\n{doc['humanFeedback']}")
        console.print(
            Panel("\n".join(lines), title=doc["query"], border_style=colour, padding=(1, 2))
        )


@app.command()
def report(
    domain: str,
    out: str = typer.Option(None, help="Output path (default: <domain>-cairn.html)."),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open when done."),
) -> None:
    """Write a shareable HTML report: decisions, evidence, briefs, learned rules."""
    import webbrowser
    from pathlib import Path

    from .report import build_report

    site, _ = _site(domain)
    path = Path(out or f"{site}-cairn.html").resolve()
    path.write_text(build_report(site), encoding="utf-8")
    console.print(f"wrote [bold]{path}[/]")
    if open_browser:
        webbrowser.open(path.as_uri())


@app.command()
def stats(domain: str) -> None:
    """How much of each run memory answered for free, run over run.

    Deliberately NOT "tokens per brief": as memory saturates, a run correctly
    produces fewer briefs because less is genuinely new, so that ratio rises
    even though the system is working. The honest measure is what share of
    candidates were resolved without paying for a live SERP read.
    """
    site, _ = _site(domain)
    db = get_db()
    rows = list(
        db.runs.aggregate(
            [
                {"$match": {"site": site}},
                {"$sort": {"startedAt": 1}},
                {
                    "$lookup": {
                        "from": "briefs",
                        "localField": "runId",
                        "foreignField": "runId",
                        "as": "briefs",
                    }
                },
                {
                    "$lookup": {
                        "from": "topics",
                        "localField": "runId",
                        "foreignField": "runId",
                        "as": "topics",
                    }
                },
                {
                    "$lookup": {
                        "from": "verdicts",
                        "localField": "runId",
                        "foreignField": "runId",
                        "as": "verdicts",
                    }
                },
                {
                    "$project": {
                        "runId": 1,
                        "coldStart": 1,
                        "tokens": 1,
                        "briefs": {"$size": "$briefs"},
                        "graded": {"$size": "$verdicts"},
                        "considered": {"$size": "$topics"},
                        "vetoed": {
                            "$size": {
                                "$filter": {
                                    "input": "$topics",
                                    "cond": {"$eq": ["$$this.status", "vetoed"]},
                                }
                            }
                        },
                    }
                },
            ]
        )
    )
    if not rows:
        console.print(f"no runs recorded for {site}")
        return

    table = Table(title=f"{site} — memory compounding", header_style="bold")
    for col in ("run", "start", "considered", "resolved from memory",
                "paid SERP reads", "briefs", "tokens"):
        table.add_column(col)
    for r in rows:
        considered = r.get("considered", 0)
        vetoed = r.get("vetoed", 0)
        pct = f"{vetoed}/{considered}" + (
            f"  ({100 * vetoed // considered}%)" if considered else ""
        )
        table.add_row(
            r["runId"][-8:],
            "cold" if r.get("coldStart") else "warm",
            str(considered),
            pct,
            str(r.get("graded", 0)),
            str(r.get("briefs", 0)),
            f"{r.get('tokens', 0):,}",
        )
    console.print(table)
    console.print(
        "[dim]`resolved from memory` rising is the system working: those "
        "candidates cost zero API calls.\n"
        "token counts fall back to a ~4-chars-per-token estimate when the "
        "provider returns no usage data.[/]"
    )

    counts = {c: db[c].count_documents({"site": site}) for c in
              ("pages", "verdicts", "rules", "briefs")}
    console.print("memory: " + " · ".join(f"{k} [bold]{v}[/]" for k, v in counts.items()))


@app.command()
def memory(domain: str) -> None:
    """Show what the system has learned about this site."""
    site, _ = _site(domain)
    db = get_db()

    rules = list(db.rules.find({"site": site}).sort("confidence", -1))
    if rules:
        table = Table(title="learned rules", header_style="bold", show_lines=False)
        table.add_column("rule", overflow="fold")
        table.add_column("conf")
        table.add_column("applied")
        for r in rules:
            table.add_row(
                r.get("rule", ""),
                f"{r.get('confidence', 0):.2f}",
                str(r.get("timesApplied", 0)),
            )
        console.print(table)
    else:
        console.print("[dim]no rules learned yet[/]")

    verdicts = list(db.verdicts.find({"site": site}).sort("observedAt", -1).limit(15))
    if verdicts:
        table = Table(title="recent SERP verdicts", header_style="bold")
        table.add_column("grade")
        table.add_column("query", overflow="fold")
        table.add_column("reason", overflow="fold", style="dim")
        for v in verdicts:
            table.add_row(v.get("grade", ""), v.get("query", ""),
                          (v.get("reason", "") or "")[:120])
        console.print(table)


@app.command()
def reset(
    domain: str,
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation."),
) -> None:
    """Wipe all memory for one site, to demo a cold start again."""
    site, _ = _site(domain)
    if not yes and not typer.confirm(f"Delete ALL cairn memory for {site}?"):
        raise typer.Abort()
    db = get_db()
    deleted = {
        c: db[c].delete_many({"site": site}).deleted_count
        for c in ("sites", "pages", "topics", "verdicts", "rules", "briefs", "runs")
    }
    db.sites.delete_many({"domain": site})
    console.print("deleted: " + " · ".join(f"{k}={v}" for k, v in deleted.items()))


@app.command()
def doctor() -> None:
    """Check credentials and connectivity before a demo."""
    import os

    rows = [
        ("MONGODB_URI", "set" if SETTINGS.mongodb_uri else "[red]MISSING[/]"),
        (
            "LLM key",
            "set"
            if any(
                os.getenv(k)
                for k in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")
            )
            else "[red]MISSING[/]",
        ),
        ("embedding backend", active_backend()),
        ("verdict model", SETTINGS.verdict_model),
        ("brief model", SETTINGS.brief_model),
    ]
    try:
        get_db().command("ping")
        rows.append(("mongo ping", "[green]ok[/]"))
        try:
            get_db().client.admin.command("replSetGetStatus")
            rows.append(("change streams", "[green]available[/]"))
        except Exception:  # noqa: BLE001
            rows.append(("change streams", "[yellow]unavailable — will poll[/]"))
    except Exception as exc:  # noqa: BLE001
        rows.append(("mongo ping", f"[red]{exc}[/]"))

    try:
        from run_agent import AIAgent  # noqa: F401

        rows.append(("hermes", "[green]importable[/]"))
    except Exception as exc:  # noqa: BLE001
        rows.append(("hermes", f"[red]{exc}[/]"))

    table = Table(box=None, header_style="bold")
    table.add_column("check")
    table.add_column("value")
    for k, v in rows:
        table.add_row(k, str(v))
    console.print(table)


def _site(domain: str) -> tuple[str, str]:
    from .crawl import normalize_domain

    return normalize_domain(domain)


if __name__ == "__main__":
    app()
