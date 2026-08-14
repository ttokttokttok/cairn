"""Turn an approved brief into a pull request.

This is the only part of cairn that writes anywhere other than MongoDB, so the
boundaries are deliberate and narrow:

  - it runs on APPROVED briefs only -- the human gate stays in front of it
  - the agent has no filesystem tools; `repo.RepoWriter` validates and applies
  - it ends at a pull request, never a deploy

It does not touch `sitemap.xml`. Static site generators regenerate that at build
time, so a hand-edit is both unnecessary and overwritten on the next deploy.
Adding the post file is what publishes the page.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from rich.console import Console

from . import store
from .agents import HermesRole
from .config import SETTINGS
from .db import get_db
from .repo import (
    Conventions,
    RepoWriter,
    assert_clean,
    commit_all,
    create_branch,
    detect_conventions,
    git,
    open_pr,
)

console = Console()

AUTHOR = HermesRole(
    name="author",
    model=SETTINGS.brief_model,
    # No file toolset, deliberately. The agent returns content; our code decides
    # what may be written. `web` stays on so it can check claims while drafting.
    toolsets=["web"],
    max_iterations=14,
    system_prompt="""You write a publish-ready article from an SEO brief, matching an
existing repository's conventions exactly.

You will be given the brief, the repo's front-matter schema, and two or three real
existing posts. Match their voice, structure, heading depth, and front-matter shape.
If existing posts use a field, use it. If they don't, don't invent it.

Rules that matter:
- Follow the brief's outline. It was chosen against a live SERP.
- The brief's "information gain" is the part only this company can write. Lead with
  it rather than restating what every competitor already says.
- Use ONLY the internal links supplied in the brief. Never invent a URL.
- Write the whole article. No placeholders, no "TODO", no "[insert example]".
- Markdown body only. Do not include the front-matter fence in `body` -- return
  front-matter as structured data instead.

You may also propose small edits to EXISTING posts:
- `backlinks`: existing posts that should link TO this new article. Pick ones whose
  topic genuinely relates; a forced link is worse than none.
- `metadata`: front-matter fixes on an existing post. Only title/description-style
  fields are permitted, and only when the brief calls for it.

Return ONLY JSON, no prose:
{"slug": "url-safe-file-slug",
 "frontmatter": {"title": "...", "description": "...", ...matching the repo},
 "body": "the full article in markdown, starting at the first H2",
 "backlinks": [{"file": "path/relative/to/repo.md", "anchor": "...", "url": "..."}],
 "metadata": [{"file": "path/relative/to/repo.md", "updates": {"title": "..."}}],
 "summary": "one sentence for the pull request description"}""",
)


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:70]


def author_brief(
    brief_id: str,
    repo_path: str,
    content_dir: str | None = None,
    open_pull_request: bool = True,
) -> dict[str, Any]:
    from bson import ObjectId

    db = get_db()
    try:
        doc = db.briefs.find_one({"_id": ObjectId(brief_id)})
    except Exception:  # noqa: BLE001 - invalid id shape
        doc = None
    if not doc:
        raise SystemExit(f"no brief with id {brief_id}")
    if doc.get("status") != "approved":
        raise SystemExit(
            f"brief is '{doc.get('status')}', not 'approved'. "
            f"Run `cairn review {doc.get('site')}` first — the human gate stays "
            f"in front of the author agent."
        )

    repo = Path(repo_path).expanduser().resolve()
    if not (repo / ".git").is_dir():
        raise SystemExit(f"{repo} is not a git repository")
    assert_clean(repo)

    conventions = detect_conventions(repo, content_dir)
    console.print(f"[dim]{conventions.describe()}[/]")

    brief = doc.get("brief") or {}
    reply = AUTHOR.run(_prompt(doc, brief, conventions), task_id=f"author:{brief_id}")
    plan = reply.json(default=None)
    if not isinstance(plan, dict) or not plan.get("body"):
        raise SystemExit(
            f"author returned nothing usable ({reply.tokens:,} tokens).\n"
            f"{reply.text[:400]!r}"
        )

    slug = _slugify(plan.get("slug") or doc.get("query", "post"))
    branch_created = False
    try:
        branch = create_branch(repo, slug)
        branch_created = True
        writer = RepoWriter(repo, conventions)

        rel = conventions.content_dir.relative_to(repo) / f"{slug}{conventions.extension}"
        writer.create_post(str(rel), plan.get("frontmatter") or {}, plan["body"])

        for link in plan.get("backlinks") or []:
            if isinstance(link, dict):
                writer.insert_link(
                    link.get("file", ""), link.get("anchor", ""), link.get("url", "")
                )
        for meta in plan.get("metadata") or []:
            if isinstance(meta, dict):
                writer.set_frontmatter(meta.get("file", ""), meta.get("updates") or {})

        if not writer.applied:
            raise SystemExit(
                "nothing was written — every proposed change was denied:\n  "
                + "\n  ".join(writer.denied)
            )

        for change in writer.applied:
            console.print(
                f"  [green]{change.kind:<11}[/] "
                f"{change.path.relative_to(repo)}  [dim]{change.detail}[/]"
            )
        for denial in writer.denied:
            console.print(f"  [yellow]denied[/]      {denial}")

        title = f"Add: {doc.get('query')}"
        body = _pr_body(doc, plan, writer, reply.tokens)
        commit_all(repo, f"{title}\n\n{plan.get('summary', '')}")

        url = ""
        if open_pull_request:
            url = open_pr(repo, branch, title, body)
            console.print(f"\n  [bold green]PR opened[/] {url}")
        else:
            console.print(
                f"\n  [dim]committed to {branch}. "
                f"Review with: git -C {repo} diff main...{branch}[/]"
            )

        db.briefs.update_one(
            {"_id": doc["_id"]},
            {
                "$set": {
                    "status": "pr_open" if url else "drafted",
                    "prUrl": url,
                    "branch": branch,
                    "authoredAt": __import__("time").time(),
                }
            },
        )
        return {
            "branch": branch,
            "url": url,
            "applied": len(writer.applied),
            "denied": len(writer.denied),
            "tokens": reply.tokens,
        }
    except Exception:
        # Leave the working tree as we found it rather than stranding the user on
        # a half-written branch.
        if branch_created:
            try:
                git(repo, "checkout", "-f", "-")
                git(repo, "branch", "-D", f"cairn/{slug}")
            except RuntimeError:
                pass
        raise


def _prompt(doc: dict, brief: dict, conventions: Conventions) -> str:
    existing = "\n\n".join(
        f"--- existing post {i + 1} ---\n{s}" for i, s in enumerate(conventions.samples)
    ) or "(no existing posts to learn from)"
    return (
        f"Site: {doc.get('site')}\n"
        f"Target query: {doc.get('query')}\n\n"
        f"THE BRIEF:\n{json.dumps(brief, indent=2)}\n\n"
        f"REPO CONVENTIONS:\n{conventions.describe()}\n\n"
        f"EXISTING POSTS — match this voice and front-matter shape:\n{existing}\n\n"
        "Write the article."
    )


def _pr_body(doc: dict, plan: dict, writer: RepoWriter, tokens: int) -> str:
    brief = doc.get("brief") or {}
    lines = [
        plan.get("summary", ""),
        "",
        f"**Target keyword:** `{brief.get('target_keyword', doc.get('query'))}`",
        f"**Intent:** {brief.get('intent', '?')}",
        "",
        "**Angle**",
        f"> {brief.get('angle', '')}",
        "",
        "### Changes",
    ]
    for c in writer.applied:
        lines.append(f"- `{c.kind}` {c.path.name} — {c.detail}")
    if writer.denied:
        lines += ["", "### Refused by the permission scope"]
        lines += [f"- {d}" for d in writer.denied]
    if brief.get("do_not_cannibalize"):
        lines += ["", "### Do not cannibalize", brief["do_not_cannibalize"]]
    lines += [
        "",
        "---",
        f"Generated by cairn from an approved brief ({tokens:,} tokens). "
        "The agent cannot modify anything outside the content directory.",
    ]
    return "\n".join(lines)
