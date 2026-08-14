"""Repository inspection and permission-enforced writing.

The security model here is the point, so it is worth stating plainly:

    The author agent has NO filesystem tools.

It never touches the repo. It returns structured JSON describing what it wants
written, and the code in this module decides whether any of that is allowed. The
blast radius is therefore defined by a validator, not by whether a language model
chose to respect its instructions.

What is permitted:

    CREATE  new files inside the allowlisted content directory
    EDIT    existing files inside that directory, and only:
              - specific front-matter scalar fields (title, description, ...)
              - inserting a markdown link into the body
    DENY    everything else -- config, CI, lockfiles, source, deletes, renames,
            and anything at all outside the content directory

Every rejected operation is recorded and surfaced, never silently dropped: an
agent quietly failing to make a change looks identical to an agent deciding not
to, and those are very different things.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Front-matter keys an edit may touch. Deliberately tiny: these are the fields a
# metadata fix legitimately needs. `slug`, `date`, `draft`, and anything routing-
# related stay off the list, because changing them can unpublish a page or break
# its URL.
EDITABLE_FRONTMATTER = {
    "title",
    "description",
    "excerpt",
    "summary",
    "seo_title",
    "seoTitle",
    "meta_description",
    "metaDescription",
    "og_description",
    "ogDescription",
}

# Where content lives, in rough order of how common each convention is.
CONTENT_DIR_CANDIDATES = [
    "src/content/blog",
    "src/content/posts",
    "content/blog",
    "content/posts",
    "src/pages/blog",
    "_posts",
    "posts",
    "blog",
    "content",
]

_FM = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.S)


class PermissionDenied(RuntimeError):
    pass


@dataclass
class Conventions:
    """What this repo's existing posts look like, learned by reading them."""

    content_dir: Path
    extension: str = ".md"
    frontmatter_keys: list[str] = field(default_factory=list)
    samples: list[str] = field(default_factory=list)  # raw text of example posts
    post_count: int = 0

    def describe(self) -> str:
        return (
            f"content directory: {self.content_dir}\n"
            f"file extension: {self.extension}\n"
            f"front-matter keys used by existing posts: "
            f"{', '.join(self.frontmatter_keys) or '(none found)'}\n"
            f"existing posts: {self.post_count}"
        )


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    m = _FM.match(text)
    if not m:
        return {}, text
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}, text
    return (data if isinstance(data, dict) else {}), m.group(2)


def join_frontmatter(data: dict[str, Any], body: str) -> str:
    if not data:
        return body
    fm = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).rstrip()
    return f"---\n{fm}\n---\n\n{body.lstrip()}"


def detect_conventions(repo: Path, content_dir: str | None = None) -> Conventions:
    """Find the content directory and learn its conventions from real posts."""
    repo = repo.resolve()
    if content_dir:
        target = (repo / content_dir).resolve()
        if not target.is_dir():
            raise PermissionDenied(f"{target} is not a directory")
    else:
        target = next(
            (
                p
                for c in CONTENT_DIR_CANDIDATES
                if (p := (repo / c)).is_dir() and _has_posts(p)
            ),
            None,
        )
        if target is None:
            raise PermissionDenied(
                "Could not find a content directory. Pass --content-dir "
                f"explicitly. Looked for: {', '.join(CONTENT_DIR_CANDIDATES)}"
            )
        target = target.resolve()

    posts = _posts_in(target)
    keys: list[str] = []
    samples: list[str] = []
    ext = ".md"
    for p in posts[:3]:
        text = p.read_text(encoding="utf-8", errors="replace")
        fm, _ = split_frontmatter(text)
        for k in fm:
            if k not in keys:
                keys.append(k)
        samples.append(text[:2500])
        ext = p.suffix
    return Conventions(
        content_dir=target,
        extension=ext,
        frontmatter_keys=keys,
        samples=samples,
        post_count=len(posts),
    )


def _posts_in(d: Path) -> list[Path]:
    return sorted(
        p
        for p in d.rglob("*")
        if p.is_file() and p.suffix in {".md", ".mdx", ".markdown"}
    )


def _has_posts(d: Path) -> bool:
    return bool(_posts_in(d))


@dataclass
class Change:
    kind: str  # "create" | "frontmatter" | "link"
    path: Path
    detail: str


class RepoWriter:
    """Applies agent-proposed changes, enforcing the permission scope in code."""

    def __init__(self, repo: Path, conventions: Conventions) -> None:
        self.repo = repo.resolve()
        self.conventions = conventions
        self.applied: list[Change] = []
        self.denied: list[str] = []

    # --- guards ----------------------------------------------------------

    def _resolve(self, relative: str) -> Path:
        """Resolve a path and prove it stays inside the content directory.

        Resolves before comparing so `../../.github/workflows/x.yml` and symlinks
        are both caught -- a string prefix check would not stop either.
        """
        candidate = (self.repo / relative).resolve()
        content = self.conventions.content_dir
        if not candidate.is_relative_to(content):
            raise PermissionDenied(
                f"{relative} is outside the content directory ({content})"
            )
        if candidate.suffix not in {".md", ".mdx", ".markdown"}:
            raise PermissionDenied(f"{relative} is not a markdown file")
        return candidate

    def _deny(self, message: str) -> None:
        self.denied.append(message)

    # --- operations ------------------------------------------------------

    def create_post(
        self, relative: str, frontmatter: dict[str, Any], body: str
    ) -> Path | None:
        try:
            path = self._resolve(relative)
        except PermissionDenied as exc:
            self._deny(f"create refused: {exc}")
            return None
        if path.exists():
            self._deny(f"create refused: {relative} already exists (never overwrite)")
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(join_frontmatter(frontmatter, body), encoding="utf-8")
        self.applied.append(Change("create", path, f"{len(body.split())} words"))
        return path

    def set_frontmatter(self, relative: str, updates: dict[str, Any]) -> None:
        try:
            path = self._resolve(relative)
        except PermissionDenied as exc:
            self._deny(f"metadata edit refused: {exc}")
            return
        if not path.exists():
            self._deny(f"metadata edit refused: {relative} does not exist")
            return

        text = path.read_text(encoding="utf-8")
        fm, body = split_frontmatter(text)
        if not fm:
            self._deny(f"metadata edit refused: {relative} has no front-matter")
            return

        changed = []
        for key, value in (updates or {}).items():
            if key not in EDITABLE_FRONTMATTER:
                self._deny(f"field '{key}' is not editable ({relative})")
                continue
            if not isinstance(value, str) or not value.strip():
                self._deny(f"field '{key}' must be a non-empty string ({relative})")
                continue
            fm[key] = value.strip()
            changed.append(key)

        if changed:
            path.write_text(join_frontmatter(fm, body), encoding="utf-8")
            self.applied.append(
                Change("frontmatter", path, f"set {', '.join(changed)}")
            )

    def insert_link(self, relative: str, anchor: str, url: str) -> None:
        """Add one markdown link into an existing post's body.

        Appends a short "Related" line rather than rewriting prose. Editing
        someone's sentences to slot a link in is exactly the kind of change a
        reviewer cannot skim, and the SEO value is in the link existing at all.
        """
        try:
            path = self._resolve(relative)
        except PermissionDenied as exc:
            self._deny(f"link insert refused: {exc}")
            return
        if not path.exists():
            self._deny(f"link insert refused: {relative} does not exist")
            return
        if not anchor.strip() or not url.strip():
            self._deny(f"link insert refused: empty anchor or url ({relative})")
            return

        text = path.read_text(encoding="utf-8")
        if url in text:
            self._deny(f"link insert skipped: {relative} already links to {url}")
            return

        fm, body = split_frontmatter(text)
        body = body.rstrip() + f"\n\nRelated: [{anchor.strip()}]({url.strip()})\n"
        path.write_text(join_frontmatter(fm, body), encoding="utf-8")
        self.applied.append(Change("link", path, f"-> {url}"))


# --- git ---------------------------------------------------------------------


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def assert_clean(repo: Path) -> None:
    if git(repo, "status", "--porcelain"):
        raise RuntimeError(
            "the repository has uncommitted changes. Commit or stash them first "
            "so the agent's diff is the only thing in the branch."
        )


def create_branch(repo: Path, slug: str) -> str:
    branch = f"cairn/{slug}"
    git(repo, "checkout", "-b", branch)
    return branch


def commit_all(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-m", message)


@dataclass
class PrAuth:
    ok: bool
    identity: str = ""
    scopes: list[str] = field(default_factory=list)
    source: str = ""
    problem: str = ""

    @property
    def overbroad(self) -> list[str]:
        """Scopes this agent has no business holding.

        Our validator already refuses to write outside the content directory, but
        the credential itself should not be able to either -- if the token cannot
        touch CI, a bug in our code cannot become a supply-chain problem.
        """
        return [s for s in self.scopes if s in {"workflow", "admin:org", "delete_repo"}]


def check_pr_auth() -> PrAuth:
    """Verify a PR can actually be opened, before spending tokens writing one."""
    import os
    import shutil

    if shutil.which("gh") is None:
        return PrAuth(False, problem="the `gh` CLI is not installed")

    env_token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    result = subprocess.run(
        ["gh", "auth", "status"], capture_output=True, text=True
    )
    if result.returncode != 0:
        return PrAuth(
            False,
            problem="gh is not authenticated. Set GH_TOKEN to a fine-grained "
            "token, or run `gh auth login`.",
        )

    out = result.stdout + result.stderr
    identity = ""
    if m := re.search(r"account (\S+)", out):
        identity = m.group(1)
    scopes: list[str] = []
    if m := re.search(r"Token scopes: (.+)", out):
        scopes = [s.strip().strip("'\"") for s in m.group(1).split(",")]
    return PrAuth(
        True,
        identity=identity,
        scopes=scopes,
        source="GH_TOKEN" if env_token else "gh keyring",
    )


def open_pr(repo: Path, branch: str, title: str, body: str) -> str:
    git(repo, "push", "-u", "origin", branch)
    result = subprocess.run(
        ["gh", "pr", "create", "--title", title, "--body", body, "--head", branch],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh pr create failed: {result.stderr.strip()}")
    return result.stdout.strip().splitlines()[-1]
