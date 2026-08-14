"""Build a standalone HTML report of what the agent did and why.

The brief is the product. Everything else in cairn exists to decide which briefs
are worth writing, so the reporting surface has to show both: the strategy (what
memory rejected, and on what evidence) and the deliverable (the briefs
themselves, in full).

Self-contained output -- no external CSS, fonts, or scripts -- so the file can be
opened from disk or mailed to a colleague.
"""

from __future__ import annotations

import html
import time
from typing import Any

from .db import get_db

CSS = """
:root{--ground:#EAEBE7;--surface:#F4F5F2;--surface-2:#DEE0DA;--ink:#1B2024;
--ink-soft:#4C555C;--ink-faint:#79838B;--rule:#C9CDC5;--rule-soft:#D8DBD4;
--lichen:#6F7C31;--lichen-bg:#E4E7D2;--veto:#9E4A32;--veto-bg:#F0DED8;
--paid:#9A6A1F;--paid-bg:#F2E4CC;--pass:#3E6B52;--pass-bg:#D8E5DC;
--mono:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,monospace;
--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
--ground:#14171A;--surface:#1B1F23;--surface-2:#262B30;--ink:#E8EAE5;
--ink-soft:#A9B2B8;--ink-faint:#77828A;--rule:#313940;--rule-soft:#262D33;
--lichen:#B4C35C;--lichen-bg:#2C331A;--veto:#DB8A72;--veto-bg:#38211A;
--paid:#D6A55B;--paid-bg:#362A16;--pass:#7FB795;--pass-bg:#1C2E24}}
:root[data-theme="dark"]{--ground:#14171A;--surface:#1B1F23;--surface-2:#262B30;
--ink:#E8EAE5;--ink-soft:#A9B2B8;--ink-faint:#77828A;--rule:#313940;
--rule-soft:#262D33;--lichen:#B4C35C;--lichen-bg:#2C331A;--veto:#DB8A72;
--veto-bg:#38211A;--paid:#D6A55B;--paid-bg:#362A16;--pass:#7FB795;--pass-bg:#1C2E24}
*{box-sizing:border-box}
body{background:var(--ground);color:var(--ink);font-family:var(--sans);
line-height:1.6;margin:0;padding:0 1.25rem 5rem;-webkit-font-smoothing:antialiased}
.wrap{max-width:58rem;margin:0 auto}
header{padding:3.5rem 0 2rem;border-bottom:2px solid var(--ink)}
h1{font-family:var(--mono);font-size:clamp(2rem,5vw,3rem);letter-spacing:-.04em;
margin:0;line-height:1}
h1 .dot{color:var(--lichen)}
.sub{color:var(--ink-soft);margin:.9rem 0 0}
.stamp{display:flex;flex-wrap:wrap;gap:.4rem 1.4rem;margin-top:1.4rem;
font-family:var(--mono);font-size:.72rem;letter-spacing:.06em;
text-transform:uppercase;color:var(--ink-faint)}
section{padding-top:3rem}
h2{font-family:var(--mono);font-size:1rem;margin:0 0 .3rem;letter-spacing:-.01em}
.lede{color:var(--ink-soft);margin:0 0 1.3rem;max-width:68ch}
h3{font-family:var(--mono);font-size:.8rem;letter-spacing:.04em;
text-transform:uppercase;color:var(--ink-faint);margin:2rem 0 .7rem}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(9rem,1fr));gap:.7rem}
.kpi{background:var(--surface);border:1px solid var(--rule);border-radius:3px;
padding:.9rem 1rem}
.kpi b{display:block;font-family:var(--mono);font-size:1.6rem;
font-variant-numeric:tabular-nums;letter-spacing:-.03em}
.kpi span{font-size:.74rem;letter-spacing:.05em;text-transform:uppercase;
color:var(--ink-faint);font-family:var(--mono)}
.scroller{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:.86rem;min-width:44rem;
table-layout:fixed}
table.gate col.c-topic{width:26%}
table.gate col.c-out{width:19%}
table.gate col.c-ev{width:47%}
table.gate col.c-sc{width:8%}
table.verdicts col.c-grade{width:13%}
table.verdicts col.c-q{width:32%}
td{overflow-wrap:anywhere}
th{text-align:left;font-family:var(--mono);font-size:.68rem;letter-spacing:.07em;
text-transform:uppercase;color:var(--ink-faint);padding:0 .65rem .5rem;
border-bottom:1px solid var(--rule)}
td{padding:.58rem .65rem;border-bottom:1px solid var(--rule-soft);vertical-align:top}
tbody tr:last-child td{border-bottom:none}
td.sc{font-family:var(--mono);font-variant-numeric:tabular-nums;
color:var(--ink-faint);text-align:right;white-space:nowrap}
td.ev{color:var(--ink-faint);font-size:.81rem}
.tag{display:inline-block;font-family:var(--mono);font-size:.66rem;
letter-spacing:.05em;text-transform:uppercase;padding:.16rem .42rem;
border-radius:2px;max-width:100%;overflow-wrap:anywhere}
.t-pages{background:var(--veto-bg);color:var(--veto)}
.t-verdicts{background:var(--paid-bg);color:var(--paid)}
.t-briefs{background:var(--lichen-bg);color:var(--lichen)}
.t-rules{background:var(--lichen-bg);color:var(--lichen)}
.t-pass{background:var(--pass-bg);color:var(--pass)}
.t-WINNABLE{background:var(--pass-bg);color:var(--pass)}
.t-CONTESTED{background:var(--paid-bg);color:var(--paid)}
.t-UNWINNABLE{background:var(--veto-bg);color:var(--veto)}
.brief{background:var(--surface);border:1px solid var(--rule);border-radius:3px;
padding:1.4rem 1.5rem;margin-bottom:1rem}
.brief h4{font-size:1.08rem;margin:0 0 .2rem;text-wrap:balance}
.brief .meta{font-family:var(--mono);font-size:.72rem;letter-spacing:.05em;
text-transform:uppercase;color:var(--ink-faint);margin-bottom:1rem;
display:flex;flex-wrap:wrap;gap:.4rem .9rem;align-items:center}
.field{margin:.9rem 0}
.field .k{font-family:var(--mono);font-size:.7rem;letter-spacing:.07em;
text-transform:uppercase;color:var(--ink-faint);margin-bottom:.2rem}
.field .v{color:var(--ink-soft);font-size:.92rem}
ol.outline{margin:.3rem 0 0;padding-left:1.3rem;color:var(--ink-soft);font-size:.9rem}
ol.outline li{margin:.22rem 0}
ul.plain{margin:.3rem 0 0;padding-left:1.1rem;color:var(--ink-soft);font-size:.9rem}
.empty{color:var(--ink-faint);font-style:italic;font-size:.9rem}
.note{background:var(--surface);border-left:3px solid var(--lichen);
padding:.85rem 1.05rem;margin:1.2rem 0;font-size:.92rem;color:var(--ink-soft)}
footer{margin-top:4rem;padding-top:1.3rem;border-top:1px solid var(--rule);
font-family:var(--mono);font-size:.72rem;letter-spacing:.05em;
text-transform:uppercase;color:var(--ink-faint)}
"""


def _e(v: Any) -> str:
    return html.escape(str(v if v is not None else ""))


def _tag(text: str, kind: str) -> str:
    return f'<span class="tag t-{_e(kind)}">{_e(text)}</span>'


def _field(key: str, value: Any) -> str:
    if not value:
        return ""
    return (
        f'<div class="field"><div class="k">{_e(key)}</div>'
        f'<div class="v">{_e(value)}</div></div>'
    )


def _upgrade_card(doc: dict[str, Any]) -> str:
    """An improve-existing brief. Different shape entirely from a new-article one:
    it targets a URL that already ranks, so there is no outline or angle."""
    b = doc.get("brief") or {}
    status = doc.get("status", "")
    status_kind = {"approved": "pass", "rejected": "pages"}.get(status, "verdicts")

    def _list(key: str, label: str) -> str:
        items = b.get(key) or []
        if not items:
            return ""
        li = "".join(f"<li>{_e(i)}</li>" for i in items)
        return (
            f'<div class="field"><div class="k">{_e(label)}</div>'
            f'<ul class="plain">{li}</ul></div>'
        )

    return f"""
    <article class="brief">
      <h4>Improve an existing page — {_e(doc.get("query"))}</h4>
      <div class="meta">
        {_tag(status.replace("_", " ") or "pending", status_kind)}
        {_tag("improve existing", "verdicts")}
        <span>priority {_e(b.get("priority", "?"))}</span>
      </div>
      {_field("Page to change", doc.get("improveUrl"))}
      {_field("Diagnosis — why it's stuck", b.get("diagnosis"))}
      {_field("Rewrite the title to", b.get("title_rewrite"))}
      {_field("Rewrite the meta description to", b.get("meta_rewrite"))}
      {_list("content_changes", "Content changes, in order")}
      {_list("sections_to_add", "Sections competitors have that this page lacks")}
      {_field("Expected effect", b.get("expected_effect"))}
    </article>"""


def _brief_card(doc: dict[str, Any]) -> str:
    if doc.get("kind") == "improve_existing":
        return _upgrade_card(doc)
    b = doc.get("brief") or {}
    status = doc.get("status", "")
    status_kind = {"approved": "pass", "rejected": "pages"}.get(status, "verdicts")

    outline = "".join(f"<li>{_e(s)}</li>" for s in (b.get("outline") or []))
    outline_html = (
        f'<div class="field"><div class="k">Outline</div>'
        f'<ol class="outline">{outline}</ol></div>'
        if outline
        else ""
    )

    links = b.get("internal_links") or []
    if links:
        items = "".join(
            f'<li>{_e(l.get("anchor", ""))} → {_e(l.get("url", ""))}</li>'
            for l in links
            if isinstance(l, dict)
        )
        links_html = (
            f'<div class="field"><div class="k">Internal links '
            f'<span style="text-transform:none;letter-spacing:0">'
            f'(retrieved from the site\'s own inventory)</span></div>'
            f'<ul class="plain">{items}</ul></div>'
        )
    else:
        links_html = (
            '<div class="field"><div class="k">Internal links</div>'
            '<div class="empty">None — no pages were indexed for this site, '
            'so there was nothing real to link to. The agent left it empty '
            'rather than inventing URLs.</div></div>'
        )

    competing = b.get("competing_pages") or []
    competing_html = ""
    if competing:
        items = "".join(f"<li>{_e(c)}</li>" for c in competing)
        competing_html = (
            f'<div class="field"><div class="k">Currently ranking</div>'
            f'<ul class="plain">{items}</ul></div>'
        )

    feedback = ""
    if doc.get("humanFeedback"):
        feedback = _field("Your rejection reason", doc["humanFeedback"])

    return f"""
    <article class="brief">
      <h4>{_e(doc.get("query"))}</h4>
      <div class="meta">
        {_tag(status.replace("_", " ") or "pending", status_kind)}
        <span>{_e(b.get("intent", ""))}</span>
        <span>{_e(b.get("target_keyword", ""))}</span>
      </div>
      {_field("Angle — how this beats what already ranks", b.get("angle"))}
      {_field("Why now", b.get("why_now"))}
      {_field("Information gain", b.get("information_gain"))}
      {outline_html}
      {links_html}
      {competing_html}
      {_field("Do not cannibalize", b.get("do_not_cannibalize"))}
      {feedback}
    </article>"""


def build_report(site: str) -> str:
    db = get_db()
    runs = list(db.runs.find({"site": site}).sort("startedAt", 1))
    briefs = list(db.briefs.find({"site": site}).sort("createdAt", -1))
    verdicts = list(db.verdicts.find({"site": site}).sort("observedAt", -1))
    rules = list(db.rules.find({"site": site}).sort("confidence", -1))
    latest = runs[-1] if runs else None

    topics = (
        list(db.topics.find({"runId": latest["runId"]})) if latest else []
    )
    vetoed = [t for t in topics if t.get("status") == "vetoed"]

    def _row(t: dict[str, Any]) -> str:
        stopped = t.get("status") == "vetoed"
        if stopped:
            label = t.get("vetoReason") or "stopped"
            label = label.replace(" (cannibalization risk)", "")
            kind = t.get("vetoedBy", "").split(":")[0] or "pages"
            if t.get("action") == "improve":
                kind = "verdicts"  # amber: redirected, not killed
            evidence = _e(t.get("evidence"))
            score = f'{(t.get("score") or 0):.3f}'
        else:
            # Nothing in memory matched, so this one went out to a live search.
            label, kind = "searched live", "pass"
            evidence = '<span class="empty">nothing in memory matched</span>'
            score = "—"
        return (
            f'<tr><td>{_e(t.get("query"))}</td><td>{_tag(label, kind)}</td>'
            f'<td class="ev">{evidence}</td><td class="sc">{score}</td></tr>'
        )

    rows = "".join(_row(t) for t in topics) or (
        '<tr><td colspan="4" class="empty">No topics recorded.</td></tr>'
    )

    vrows = "".join(
        f'<tr><td>{_tag(v.get("grade",""), v.get("grade",""))}</td>'
        f'<td>{_e(v.get("query"))}</td>'
        f'<td class="ev">{_e(v.get("reason"))}</td></tr>'
        for v in verdicts[:20]
    ) or '<tr><td colspan="3" class="empty">No SERP verdicts yet.</td></tr>'

    rrows = "".join(
        f'<tr><td>{_tag(r.get("polarity","prefer"), "rules")}</td>'
        f'<td>{_e(r.get("rule"))}</td>'
        f'<td class="sc">{(r.get("confidence") or 0):.2f}</td></tr>'
        for r in rules
    ) or '<tr><td colspan="3" class="empty">Nothing generalized yet.</td></tr>'

    brief_cards = "".join(_brief_card(b) for b in briefs) or (
        '<p class="empty">No briefs yet. Either every candidate was resolved '
        'from memory, or no SERP graded WINNABLE this run.</p>'
    )

    tokens = latest.get("tokens", 0) if latest else 0
    pending = sum(1 for b in briefs if b.get("status") == "pending_approval")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(site)} — cairn</title><style>{CSS}</style></head><body>
<div class="wrap">
<header>
  <h1>cairn<span class="dot">.</span></h1>
  <p class="sub">What the agent decided for <strong>{_e(site)}</strong>, and why.</p>
  <div class="stamp">
    <span>{_e(site)}</span>
    <span>{len(runs)} run(s)</span>
    <span>{time.strftime("%d %b %Y, %H:%M")}</span>
  </div>
</header>

<section>
  <h2>Latest run</h2>
  <p class="lede">The agent proposes topics, then checks its own memory before spending
  anything. Only what survives reaches a live search.</p>
  <div class="kpis">
    <div class="kpi"><b>{len(topics)}</b><span>considered</span></div>
    <div class="kpi"><b>{len(vetoed)}</b><span>stopped by memory</span></div>
    <div class="kpi"><b>{len(topics) - len(vetoed)}</b><span>searched live</span></div>
    <div class="kpi"><b>{tokens:,}</b><span>tokens</span></div>
  </div>
  <div class="note">Anything stopped by memory cost <strong>nothing</strong> —
  no model call, no web request. The evidence column names the exact record that stopped it.</div>
  <div class="scroller"><table class="gate">
    <colgroup><col class="c-topic"><col class="c-out"><col class="c-ev"><col class="c-sc"></colgroup>
    <thead><tr><th>Topic the agent considered</th><th>Outcome</th><th>Evidence</th><th style="text-align:right">Score</th></tr></thead>
    <tbody>{rows}</tbody></table></div>
</section>

<section>
  <h2>The briefs</h2>
  <p class="lede">This is the deliverable — a writer should be able to start from one of these.
  {pending} awaiting your approval.</p>
  {brief_cards}
</section>

<section>
  <h2>What the agent saw on Google</h2>
  <p class="lede">Its read of each live search result page. These are kept, so the same
  question is never paid for twice.</p>
  <div class="scroller"><table class="verdicts">
    <colgroup><col class="c-grade"><col class="c-q"><col></colgroup>
    <thead><tr><th>Verdict</th><th>Query</th><th>Reasoning</th></tr></thead>
    <tbody>{vrows}</tbody></table></div>
</section>

<section>
  <h2>What it has learned</h2>
  <p class="lede">Generalizations induced from its own results and from topics you rejected.
  These steer what it proposes next time.</p>
  <div class="scroller"><table class="verdicts">
    <colgroup><col class="c-grade"><col><col class="c-grade"></colgroup>
    <thead><tr><th></th><th>Rule</th><th style="text-align:right">Conf.</th></tr></thead>
    <tbody>{rrows}</tbody></table></div>
</section>

<footer>cairn · generated from live MongoDB Atlas data</footer>
</div></body></html>"""
