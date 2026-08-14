"""Google Search Console — the one source of the site's *own* truth.

Everything else cairn knows is external: what the sitemap claims, and what Google
shows the public. GSC is what actually happened -- which queries you appear for,
how often, and at what position.

That enables the move the rest of the pipeline structurally cannot make:

    "You already rank #7 for this with 4,000 monthly impressions.
     Improve that page. Don't write a new one."

Auth is a service account, deliberately. The user creates one, downloads a JSON
key, and adds its email as a user in Search Console. No consent screen, no
browser redirect, no refresh-token storage, and it works headlessly under cron --
which matters because a scheduled run is the natural way to use this.

Talks to the REST API directly with google-auth for signing, rather than pulling
in google-api-python-client for one endpoint.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Iterator

import httpx

from .config import SETTINGS

SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
API = "https://searchconsole.googleapis.com/webmasters/v3"


class GSCUnavailable(RuntimeError):
    """Raised when GSC is not configured or the site is not accessible.

    Never fatal: GSC is strictly additive. Without it the pipeline behaves
    exactly as it always has.
    """


@dataclass
class PerformanceRow:
    query: str
    page: str
    impressions: int
    clicks: int
    ctr: float
    position: float


def _credentials():
    if not SETTINGS.gsc_service_account:
        raise GSCUnavailable(
            "GOOGLE_SA_JSON is not set. Create a service account, download its "
            "JSON key, and add the account's email as a user in Search Console."
        )
    try:
        from google.oauth2 import service_account
    except ImportError as exc:  # pragma: no cover
        raise GSCUnavailable(f"google-auth is not installed: {exc}") from exc

    try:
        return service_account.Credentials.from_service_account_file(
            SETTINGS.gsc_service_account, scopes=[SCOPE]
        )
    except (OSError, ValueError) as exc:
        raise GSCUnavailable(f"could not read {SETTINGS.gsc_service_account}: {exc}")


def _token() -> str:
    from google.auth.transport.requests import Request

    creds = _credentials()
    creds.refresh(Request())
    return creds.token


def service_account_email() -> str | None:
    """The address the user must grant access to in Search Console."""
    try:
        return getattr(_credentials(), "service_account_email", None)
    except GSCUnavailable:
        return None


def list_sites() -> list[str]:
    """Every property this service account can read."""
    r = httpx.get(
        f"{API}/sites",
        headers={"Authorization": f"Bearer {_token()}"},
        timeout=30.0,
    )
    if r.status_code == 403:
        raise GSCUnavailable(
            "Search Console rejected the service account. Add "
            f"{service_account_email()} as a user on the property."
        )
    r.raise_for_status()
    return [
        s["siteUrl"]
        for s in r.json().get("siteEntry", [])
        if s.get("permissionLevel") != "siteUnverifiedUser"
    ]


def resolve_property(site: str) -> str:
    """Map a bare domain to the property string GSC actually uses.

    A property is registered either as a domain property (`sc-domain:example.com`)
    or a URL prefix (`https://example.com/`), and which one exists is the user's
    choice, not something we can assume.
    """
    available = list_sites()
    candidates = [
        f"sc-domain:{site}",
        f"https://{site}/",
        f"https://www.{site}/",
        f"http://{site}/",
    ]
    for c in candidates:
        if c in available:
            return c
    raise GSCUnavailable(
        f"no Search Console property matches {site}. "
        f"This account can read: {', '.join(available) or '(nothing)'}"
    )


def fetch_performance(
    site: str, days: int | None = None, row_limit: int = 25000
) -> Iterator[PerformanceRow]:
    """Query + page performance for the trailing window, paginated."""
    days = days or SETTINGS.gsc_lookback_days
    prop = resolve_property(site)
    end = time.strftime("%Y-%m-%d", time.gmtime())
    start = time.strftime("%Y-%m-%d", time.gmtime(time.time() - days * 86400))
    token = _token()
    start_row = 0

    while True:
        body: dict[str, Any] = {
            "startDate": start,
            "endDate": end,
            "dimensions": ["query", "page"],
            "rowLimit": min(row_limit, 25000),
            "startRow": start_row,
        }
        r = httpx.post(
            f"{API}/sites/{_quote(prop)}/searchAnalytics/query",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
            timeout=60.0,
        )
        r.raise_for_status()
        rows = r.json().get("rows", [])
        if not rows:
            return
        for row in rows:
            query, page = row["keys"][0], row["keys"][1]
            yield PerformanceRow(
                query=query,
                page=page,
                impressions=int(row.get("impressions", 0)),
                clicks=int(row.get("clicks", 0)),
                ctr=float(row.get("ctr", 0.0)),
                position=float(row.get("position", 0.0)),
            )
        if len(rows) < body["rowLimit"]:
            return
        start_row += len(rows)


def _quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")
