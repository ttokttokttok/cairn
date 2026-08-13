"""Content inventory from sitemap.xml. Zero auth, works on any domain.

This is the corpus that cannibalization detection, internal linking, and
topical-authority checks all compare against.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx
from lxml import etree, html as lxml_html

UA = "Mozilla/5.0 (compatible; CairnSEO/0.1; +https://github.com/)"
_WS = re.compile(r"\s+")

# Pages that exist on every site and teach us nothing about topical coverage.
_BORING = re.compile(
    r"/(privacy|terms|legal|cookie|login|signin|signup|register|cart|checkout"
    r"|account|careers?/\w|tag/|author/|feed|rss|\.xml$|\.pdf$)",
    re.I,
)


@dataclass
class Page:
    url: str
    title: str = ""
    h1: str = ""
    description: str = ""
    summary: str = ""
    target_keyword: str = ""
    words: int = 0

    def text_for_embedding(self) -> str:
        return _WS.sub(
            " ",
            f"{self.title}\n{self.h1}\n{self.description}\n{self.summary}",
        ).strip()


@dataclass
class CrawlResult:
    domain: str
    root: str
    urls: list[str] = field(default_factory=list)
    pages: list[Page] = field(default_factory=list)
    source: str = ""


def strip_www(host: str) -> str:
    # NB: str.lstrip("www.") strips *characters*, so it would turn
    # "web.example.com" into "eb.example.com". Use removeprefix.
    return host.lower().removeprefix("www.")


def normalize_domain(domain: str) -> tuple[str, str]:
    """Return (bare_domain, root_url)."""
    d = domain.strip()
    if not d.startswith(("http://", "https://")):
        d = "https://" + d
    parsed = urlparse(d)
    host = (parsed.netloc or parsed.path).lower()
    scheme = parsed.scheme or "https"
    return strip_www(host), f"{scheme}://{host}"


def _client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": UA},
        timeout=20.0,
        follow_redirects=True,
    )


def discover_sitemaps(client: httpx.Client, root: str) -> list[str]:
    found: list[str] = []
    try:
        r = client.get(urljoin(root, "/robots.txt"))
        if r.status_code == 200:
            found += re.findall(r"(?im)^\s*sitemap:\s*(\S+)", r.text)
    except httpx.HTTPError:
        pass
    for guess in ("/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml"):
        url = urljoin(root, guess)
        if url not in found:
            found.append(url)
    return found


def _parse_sitemap(content: bytes) -> tuple[list[str], list[str]]:
    """Return (page_urls, nested_sitemap_urls)."""
    try:
        tree = etree.fromstring(content)
    except etree.XMLSyntaxError:
        return [], []
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    nested = [e.text.strip() for e in tree.findall(".//s:sitemap/s:loc", ns) if e.text]
    pages = [e.text.strip() for e in tree.findall(".//s:url/s:loc", ns) if e.text]
    if not pages and not nested:  # namespace-less sitemaps exist in the wild
        locs = [e.text.strip() for e in tree.iter() if e.tag.endswith("loc") and e.text]
        pages = locs
    return pages, nested


def collect_urls(root: str, max_pages: int) -> tuple[list[str], str]:
    """Walk sitemaps; fall back to homepage links if there are none."""
    with _client() as client:
        seen_maps: set[str] = set()
        queue = discover_sitemaps(client, root)
        urls: list[str] = []
        source = ""
        while queue and len(urls) < max_pages * 4:
            sm = queue.pop(0)
            if sm in seen_maps:
                continue
            seen_maps.add(sm)
            try:
                r = client.get(sm)
            except httpx.HTTPError:
                continue
            if r.status_code != 200 or b"<" not in r.content[:200]:
                continue
            pages, nested = _parse_sitemap(r.content)
            if pages or nested:
                source = source or sm
            urls += pages
            queue += [n for n in nested if n not in seen_maps]

        if not urls:
            urls, source = _homepage_links(client, root), "homepage links"

    host = strip_www(urlparse(root).netloc)
    clean, seen = [], set()
    for u in urls:
        if strip_www(urlparse(u).netloc) != host:
            continue
        u = u.split("#")[0].rstrip("/") or u
        if u in seen or _BORING.search(u):
            continue
        seen.add(u)
        clean.append(u)
    return clean[:max_pages], source or "none"


def _homepage_links(client: httpx.Client, root: str) -> list[str]:
    try:
        r = client.get(root)
        r.raise_for_status()
    except httpx.HTTPError:
        return []
    doc = lxml_html.fromstring(r.content)
    return [urljoin(root, a) for a in doc.xpath("//a/@href") if not a.startswith("#")]


def fetch_page(url: str) -> Page | None:
    try:
        with _client() as client:
            r = client.get(url)
            r.raise_for_status()
            if "html" not in r.headers.get("content-type", ""):
                return None
            doc = lxml_html.fromstring(r.content)
    except (httpx.HTTPError, etree.ParserError, ValueError):
        return None

    for bad in doc.xpath("//script|//style|//nav|//footer|//header|//noscript"):
        bad.getparent().remove(bad)

    def first(xpath: str) -> str:
        vals = doc.xpath(xpath)
        return _WS.sub(" ", str(vals[0])).strip() if vals else ""

    body = _WS.sub(" ", doc.text_content()).strip()
    page = Page(
        url=url,
        title=first("//title/text()"),
        h1=first("//h1//text()"),
        description=first("//meta[@name='description']/@content")
        or first("//meta[@property='og:description']/@content"),
        summary=body[:1200],
        words=len(body.split()),
    )
    if _is_soft_404(page):
        return None
    page.target_keyword = _guess_keyword(page)
    return page


_SOFT_404 = re.compile(r"page not found|not found|404|coming soon", re.I)


def _is_soft_404(page: Page) -> bool:
    """Many sites return 200 for missing pages. Indexing those poisons the
    inventory, which then poisons cannibalization checks and internal links."""
    heading = f"{page.title} {page.h1}"
    return bool(_SOFT_404.search(heading)) or page.words < 25


def _guess_keyword(page: Page) -> str:
    """Best-effort primary keyword: the H1 or title minus brand boilerplate."""
    raw = page.h1 or page.title
    raw = re.split(r"\s[|–—-]\s", raw)[0]
    return raw.strip().lower()[:120]


def crawl_site(domain: str, max_pages: int, workers: int = 8) -> CrawlResult:
    bare, root = normalize_domain(domain)
    urls, source = collect_urls(root, max_pages)
    result = CrawlResult(domain=bare, root=root, urls=urls, source=source)
    if not urls:
        return result
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for page in pool.map(fetch_page, urls):
            if page and (page.title or page.h1):
                result.pages.append(page)
    return result
