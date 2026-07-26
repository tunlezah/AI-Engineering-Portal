"""Content emission: snapshot -> Hugo content and data.

This is the "content adapter" seam from the design. The snapshot is the
interface; everything below this line is presentation, and can be replaced
without touching crawling, indexing or scoring.

Emits:
    site/content/harnesses/<id>.md      front matter (taxonomy terms) + README body
    site/data/cards/<id>.json           the full card, plus rendered SVG and Mermaid
    site/data/registry.json             estate statistics and the index report
    site/static/catalog.json            the faceting payload the browser downloads
    site/static/graph.json              the relationship graph for /graph/
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
from typing import Any

import yaml

from . import charts
from .index import build_catalog, build_graph, index_report, neighbourhood, safe_child
from .observe import now

LIFECYCLE_ORDER = ["certified", "org-ready", "team-ready", "prototype", "experimental",
                   "deprecated", "retired", "invalid"]
LIFECYCLE_LABELS = {
    "certified": "Certified", "org-ready": "Org Ready", "team-ready": "Team Ready",
    "prototype": "Prototype", "experimental": "Experimental", "deprecated": "Deprecated",
    "retired": "Retired", "invalid": "Invalid manifest",
}

METRIC_CHARTS = [
    ("pass_rate", "Pass rate", True, True),
    ("coverage", "Coverage", True, True),
    ("hallucination_rate", "Hallucination rate", True, False),
    ("p95_latency_ms", "p95 latency (ms)", False, False),
    ("cost_per_case", "Cost per case", False, False),
]


def emit(snapshot: pathlib.Path, site: pathlib.Path, taxonomy: dict) -> dict:
    snapshot, site = pathlib.Path(snapshot), pathlib.Path(site)
    cards = [json.loads(p.read_text())
             for p in sorted((snapshot / "harnesses").glob("*.json"))]
    graph = json.loads((snapshot / "graph" / "edges.json").read_text())
    report = json.loads((snapshot / "reports" / "index-report.json").read_text())

    content = site / "content" / "harnesses"
    data = site / "data"
    static = site / "static"
    if content.exists():
        shutil.rmtree(content)
    content.mkdir(parents=True)
    (data / "cards").mkdir(parents=True, exist_ok=True)
    for old in (data / "cards").glob("*.json"):
        old.unlink()
    static.mkdir(parents=True, exist_ok=True)

    # The section index is generated too: emit-content owns this directory
    # completely, so nothing hand-written can be orphaned here.
    (content / "_index.md").write_text(
        "---\ntitle: Harnesses\nsummary: Every indexed harness in the estate.\n---\n\n"
        "The complete list. For anything but browsing end to end, "
        "[search](/browse/) is faster.\n")

    for card in cards:
        enriched = enrich(card, graph)
        # Both of these are filesystem paths and URL segments derived from
        # registry data; safe_child refuses anything that escapes the directory.
        hid = card["spec"]["metadata"]["id"]
        # Render the page first: the README *is* the page body. Dropping it from
        # the card before rendering left every harness page with front matter and
        # nothing under it.
        markdown = page(enriched)
        enriched["status"].get("content", {}).pop("readme", None)  # not in the data file too
        safe_child(data / "cards", f"{hid}.json").write_text(
            json.dumps(enriched, indent=2, sort_keys=True))
        safe_child(content, f"{hid}.md").write_text(markdown)

    stats = estate_stats(cards, report, taxonomy)
    (data / "registry.json").write_text(json.dumps(stats, indent=2, sort_keys=True))
    (data / "index-report.json").write_text(json.dumps(report, indent=2))
    (static / "catalog.json").write_text(
        (snapshot / "catalog.json").read_text())
    (static / "graph.json").write_text(json.dumps(graph, separators=(",", ":")))
    _emit_facets(data, cards, taxonomy)
    return stats


# --------------------------------------------------------------------------
# per-card enrichment: charts, diagrams, derived display values
# --------------------------------------------------------------------------
def enrich(card: dict, graph: dict) -> dict:
    st = card["status"]
    trend = st.get("evaluation_trend", [])
    ev = st.get("evaluation") or {}
    gates = _gates(ev)

    st["charts"] = {
        "quality_bars": charts.score_bars(st["quality"].get("breakdown", {})),
        "sparklines": {
            key: charts.sparkline([p.get(key) for p in trend], good_high=good)
            for key, _, _, good in METRIC_CHARTS
        },
        "trends": {
            key: charts.trend_chart(trend, key, label, threshold=gates.get(key),
                                    percent=pct, good_high=good)
            for key, label, pct, good in METRIC_CHARTS
        },
    }
    st["mermaid"] = {
        "neighbourhood": mermaid_neighbourhood(card, graph),
        **{k: v for k, v in (st.get("content", {}).get("diagrams") or {}).items()},
    }
    st["display"] = {
        "lifecycle_label": LIFECYCLE_LABELS.get(st["lifecycle"]["effective"],
                                                st["lifecycle"]["effective"]),
        "banners": banners(card),
        "headline": headline(card),
    }
    # The changelog is rendered on the page too, so its repo-relative links need
    # the same treatment as the README's.
    src = st.get("source", {})
    if st.get("content", {}).get("changelog") and src.get("web_url"):
        st["content"]["changelog"] = _rewrite_repo_links(
            st["content"]["changelog"], src["web_url"], src.get("default_branch", "main"))

    return card


def _gates(ev: dict) -> dict[str, float]:
    """Pull gate thresholds out of the latest report so charts can draw them."""
    out: dict[str, float] = {}
    for suite in ev.get("suites", []):
        for name, m in suite.get("metrics", {}).items():
            if m.get("threshold") is None:
                continue
            if name in ("pass_rate",):
                out["pass_rate"] = m["threshold"]
            if name.startswith("p95"):
                out["p95_latency_ms"] = m["threshold"]
            if "cost" in name:
                out["cost_per_case"] = m["threshold"]
            if name == "coverage":
                out["coverage"] = m["threshold"]
            if "hallucinat" in name:
                out["hallucination_rate"] = m["threshold"]
    return out


def headline(card: dict) -> list[dict]:
    st = card["status"]
    ev = (st.get("evaluation") or {}).get("totals", {})
    ops = card["spec"].get("spec", {}).get("operations", {})
    items = [
        {"label": "Quality", "value": f"{st['quality']['score']}",
         "sub": f"band {st['quality']['band']}", "tone": st["quality"].get("colour", "")},
        {"label": "Pass rate",
         "value": f"{ev['pass_rate']:.1%}" if ev.get("pass_rate") is not None else "—",
         "sub": f"{(st.get('evaluation') or {}).get('age_days', '—')}d ago" if ev else "no evaluation",
         "tone": "green" if (ev.get("pass_rate") or 0) >= 0.9 else ("amber" if ev else "red")},
        {"label": "Coverage",
         "value": f"{ev['coverage']:.0%}" if ev.get("coverage") is not None else "—",
         "sub": "declared surface exercised", "tone": ""},
        {"label": "Consumers", "value": str(st["consumer_count"]),
         "sub": "skills depending on this", "tone": ""},
    ]
    if cost := ops.get("estimated_cost"):
        items.append({"label": "Cost", "value": f"{cost['value']:.2f} {cost['currency']}",
                      "sub": cost["unit"].replace("-", " "), "tone": ""})
    if lat := ops.get("estimated_latency"):
        if lat.get("p95_ms"):
            items.append({"label": "p95 latency", "value": f"{lat['p95_ms'] / 1000:.0f}s",
                          "sub": f"measured {lat.get('measured_on', '—')}", "tone": ""})
    return items


def banners(card: dict) -> list[dict]:
    """Bad news, rendered prominently. A registry that hides problems is used once."""
    st, spec = card["status"], card["spec"].get("spec", {})
    out: list[dict] = []

    if not st.get("valid"):
        errs = [f for f in st.get("findings", []) if f["severity"] == "error"]
        out.append({"tone": "error", "title": "This manifest failed validation",
                    "body": "; ".join(f"{f['code']}: {f['message']}" for f in errs[:4])})
    if st["lifecycle"].get("downgraded") and st.get("valid"):
        out.append({
            "tone": "warn",
            "title": f"Declared {LIFECYCLE_LABELS.get(st['lifecycle']['declared'], '?')}, "
                     f"published as {LIFECYCLE_LABELS.get(st['lifecycle']['effective'], '?')}",
            "body": " · ".join(st["lifecycle"]["reasons"][:2]),
        })
    if dep := spec.get("deprecation"):
        out.append({"tone": "warn", "title": f"Deprecated since {dep['since']}",
                    "body": f"Replaced by {dep['replaced_by']}. Removal after "
                            f"{dep['removal_after']}."})
    if st["approval"].get("overdue"):
        out.append({"tone": "warn", "title": "Governance review overdue",
                    "body": f"Due {st['approval']['review_due']}; owner "
                            f"{card['spec']['metadata'].get('owner')}."})
    for w in st["approval"].get("waivers", []):
        out.append({"tone": "warn", "title": f"Evaluation gate waived: {w.get('metric')}",
                    "body": f"{w.get('reason', '')} Expires {w.get('expires')}."})
    if st.get("pipeline_status") and st["pipeline_status"] != "success":
        out.append({"tone": "error", "title": f"Pipeline {st['pipeline_status']}",
                    "body": "The default branch is not building; treat published "
                            "evidence as stale."})
    if (ev := st.get("evaluation")) and ev.get("gated_failed"):
        out.append({"tone": "error", "title": f"{ev['gated_failed']} gated metric(s) failing",
                    "body": f"Latest run {ev['run_id']}."})
    if st.get("source", {}).get("archived"):
        out.append({"tone": "warn", "title": "Project archived", "body": "Read-only."})
    return out


def _mermaid_label(text: Any) -> str:
    """Escape a node label for interpolation inside a Mermaid quoted string.

    Labels are harness names and plugin ids — repository-controlled text. A
    quote or a newline in one would break out of the label and corrupt the
    diagram source. Mermaid's own entity escapes are used so the label still
    reads correctly; `#` is escaped first so it cannot forge the others.
    """
    return (str(text)
            .replace("#", "#35;")
            .replace('"', "#quot;")
            .replace("<", "#lt;")
            .replace(">", "#gt;")
            .replace("\r", " ")
            .replace("\n", " ")[:120])


def mermaid_neighbourhood(card: dict, graph: dict) -> str:
    """One-hop graph as Mermaid text, generated — never hand-drawn, never stale."""
    nb = neighbourhood(card, graph)
    if len(nb["nodes"]) <= 1:
        return ""
    hid = card["spec"]["metadata"]["id"]
    lines = ["flowchart LR"]
    ids: dict[str, str] = {}

    def nid(key: str) -> str:
        if key not in ids:
            ids[key] = "n" + re.sub(r"\W", "_", key)
        return ids[key]

    for n in nb["nodes"]:
        label = n.get("label", n.get("id", "?"))
        shape = {
            "harness": ("[", "]"), "skill": ("([", "])"), "plugin": ("[/", "/]"),
            "team": ("[(", ")]"), "reference-architecture": ("{{", "}}"),
        }.get(n.get("type", ""), ("[", "]"))
        extra = ""
        if n.get("type") == "harness" and n.get("lifecycle"):
            extra = f"<br/><small>{_mermaid_label(n['lifecycle'])}</small>"
        lines.append(f'    {nid(n["key"])}{shape[0]}"{_mermaid_label(label)}{extra}"{shape[1]}')
    for e in nb["edges"]:
        label = _mermaid_label(e["type"].replace("-", " "))
        lines.append(f'    {nid(e["from"])} -->|{label}| {nid(e["to"])}')
    lines.append(f'    class {nid(f"harness:{hid}")} centre;')
    lines.append("    classDef centre fill:#eef2ff,stroke:#4338ca,stroke-width:2px;")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Hugo page emission
# --------------------------------------------------------------------------
def page(card: dict) -> str:
    meta = card["spec"]["metadata"]
    spec = card["spec"].get("spec", {})
    st = card["status"]

    fm = {
        "title": meta.get("name", meta["id"]),
        "id": meta["id"],
        "summary": meta.get("summary", ""),
        "date": (st.get("last_updated") or now().isoformat())[:10] or None,
        "lastmod": (st.get("last_updated") or now().isoformat())[:10] or None,
        "type": "harnesses",
        "business": spec.get("domains", {}).get("business", []),
        "technical": spec.get("domains", {}).get("technical", []),
        "lifecycles": [st["lifecycle"]["effective"]],
        "patterns": [spec.get("architecture", {}).get("pattern")] if spec.get("architecture") else [],
        "teams": [meta.get("team")] if meta.get("team") else [],
        "owners": [meta.get("owner", "").replace("group:", "")] if meta.get("owner") else [],
        "models": spec.get("models", {}).get("supported", []),
        "plugins": [p["id"] for p in st.get("plugin_resolution", []) if p["kind"] == "required"],
        "tags": meta.get("tags", []),
        "quality": st["quality"]["score"],
        "consumers": st["consumer_count"],
        "version": st.get("released_version") or spec.get("version"),
        "valid": st.get("valid", False),
    }
    fm = {k: v for k, v in fm.items() if v not in (None, [], "")}
    body = _readme_body(card)
    return "---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True) + "---\n\n" + body


REL_LINK = re.compile(r"(\[[^\]]*\]\()(?!https?:|mailto:|#|/)([^)\s]+)(\))")


def _rewrite_repo_links(text: str, web_url: str, branch: str = "main") -> str:
    """Rewrite repo-relative links to the GitLab project.

    A README is written to be read inside its repository. Rendered here it is
    somewhere else entirely, so `docs/quickstart.md` has to become a project URL
    or it 404s — and a dead link on a card is read as a dead harness.
    """
    def sub(m: re.Match) -> str:
        target = m.group(2)
        kind = "tree" if target.endswith("/") else "blob"
        return f"{m.group(1)}{web_url}/-/{kind}/{branch}/{target.lstrip('./')}{m.group(3)}"

    return REL_LINK.sub(sub, text)


def _readme_body(card: dict) -> str:
    """README minus its H1 and minus the metadata table the card already renders."""
    readme = (card["status"].get("content", {}) or {}).get("readme", "")
    if not readme:
        readme = card["spec"]["metadata"].get("description", "") or ""
    src = card["status"].get("source", {})
    if src.get("web_url"):
        readme = _rewrite_repo_links(readme, src["web_url"],
                                     src.get("default_branch", "main"))
    # The page renders all of this above the body already: the title, the
    # identity table, and the "Overview" heading the body sits inside. Repeating
    # them opened every harness page with two Overview headings and a table of
    # facts the header had just shown. Only the *leading* copies are dropped —
    # a table further down is the author's content.
    out: list[str] = []
    skipped_h1 = False
    seen_section = False
    for line in readme.splitlines():
        stripped = line.strip()
        if not skipped_h1 and stripped.startswith("# "):
            skipped_h1 = True
            continue
        if not seen_section:
            if stripped.startswith("##"):
                seen_section = True
                if stripped.lower().lstrip("#").strip() == "overview":
                    continue
            elif stripped.startswith("|"):
                continue        # the identity table
        out.append(line)
    return "\n".join(out).strip() + "\n"


# --------------------------------------------------------------------------
# estate-level data
# --------------------------------------------------------------------------
def estate_stats(cards: list[dict], report: dict, taxonomy: dict) -> dict:
    lifecycles: dict[str, int] = {}
    business: dict[str, int] = {}
    technical: dict[str, int] = {}
    patterns: dict[str, int] = {}
    teams: dict[str, int] = {}
    quality_bands: dict[str, int] = {}

    for c in cards:
        st, spec = c["status"], c["spec"].get("spec", {})
        lifecycles[st["lifecycle"]["effective"]] = lifecycles.get(st["lifecycle"]["effective"], 0) + 1
        quality_bands[st["quality"]["band"]] = quality_bands.get(st["quality"]["band"], 0) + 1
        for t in spec.get("domains", {}).get("business", []):
            business[t] = business.get(t, 0) + 1
        for t in spec.get("domains", {}).get("technical", []):
            technical[t] = technical.get(t, 0) + 1
        if p := spec.get("architecture", {}).get("pattern"):
            patterns[p] = patterns.get(p, 0) + 1
        if tm := c["spec"]["metadata"].get("team"):
            teams[tm] = teams.get(tm, 0) + 1

    evaluated = [c for c in cards if c["status"].get("evaluation")]
    pass_rates = [c["status"]["evaluation"]["totals"].get("pass_rate")
                  for c in evaluated
                  if c["status"]["evaluation"]["totals"].get("pass_rate") is not None]
    recent = sorted(cards, key=lambda c: c["status"].get("last_updated") or "", reverse=True)[:8]
    popular = sorted(cards, key=lambda c: (-c["status"]["consumer_count"],
                                           -c["status"]["quality"]["score"]))[:8]
    best = sorted([c for c in cards if c["status"].get("valid")],
                  key=lambda c: -c["status"]["quality"]["score"])[:8]

    regressions = []
    for c in evaluated:
        trend = c["status"].get("evaluation_trend", [])
        rates = [p["pass_rate"] for p in trend if p.get("pass_rate") is not None]
        if len(rates) >= 2 and rates[-1] < rates[-2] - 0.005:
            regressions.append({
                "id": c["spec"]["metadata"]["id"],
                "name": c["spec"]["metadata"]["name"],
                "from": rates[-2], "to": rates[-1],
                "delta": rates[-1] - rates[-2],
                "model_build": trend[-1].get("model_build"),
                "version": trend[-1].get("version"),
            })

    return {
        "generated_at": now().isoformat(timespec="seconds"),
        "totals": {
            **report["totals"],
            "evaluated": len(evaluated),
            "certified": lifecycles.get("certified", 0),
            "mean_quality": round(sum(c["status"]["quality"]["score"] for c in cards) / len(cards), 1)
            if cards else 0,
            "mean_pass_rate": round(sum(pass_rates) / len(pass_rates), 3) if pass_rates else None,
            "consumers": sum(c["status"]["consumer_count"] for c in cards),
        },
        "lifecycles": lifecycles,
        "lifecycle_dist": charts.distribution(lifecycles, LIFECYCLE_ORDER, LIFECYCLE_LABELS),
        "quality_bands": quality_bands,
        "business": business,
        "technical": technical,
        "patterns": patterns,
        "teams": teams,
        "recent": [_summary(c) for c in recent],
        "popular": [_summary(c) for c in popular],
        "best": [_summary(c) for c in best],
        "regressions": sorted(regressions, key=lambda r: r["delta"])[:10],
        "leaderboards": _leaderboards(evaluated),
        "coverage_gaps": _coverage_gaps(cards),
    }


def _summary(c: dict) -> dict:
    st = c["status"]
    return {
        "id": c["spec"]["metadata"]["id"],
        "name": c["spec"]["metadata"].get("name"),
        "summary": c["spec"]["metadata"].get("summary", ""),
        "lifecycle": st["lifecycle"]["effective"],
        "quality": st["quality"]["score"],
        "band": st["quality"]["band"],
        "consumers": st["consumer_count"],
        "team": c["spec"]["metadata"].get("team"),
        "updated": (st.get("last_updated") or "")[:10],
        "pass_rate": ((st.get("evaluation") or {}).get("totals", {}) or {}).get("pass_rate"),
    }


def _leaderboards(evaluated: list[dict]) -> list[dict]:
    """Ranked within a comparable class only — a global ranking is noise."""
    by_pattern: dict[str, list[dict]] = {}
    for c in evaluated:
        pattern = c["spec"]["spec"].get("architecture", {}).get("pattern", "unknown")
        by_pattern.setdefault(pattern, []).append(c)
    out = []
    for pattern, group in sorted(by_pattern.items()):
        if len(group) < 2:
            continue          # a leaderboard of one is a boast, not information
        ranked = sorted(group, key=lambda c: -(c["status"]["evaluation"]["totals"]
                                               .get("pass_rate") or 0))
        out.append({"pattern": pattern, "entries": [_summary(c) for c in ranked[:6]]})
    return out


def _coverage_gaps(cards: list[dict]) -> dict:
    untested_guardrails, unevaluated_models, no_limitations = [], [], []
    for c in cards:
        if not c["status"].get("valid"):
            continue
        spec, meta = c["spec"]["spec"], c["spec"]["metadata"]
        tree = c["status"].get("content", {}).get("tree", [])
        has_tests = any(t.startswith("tests/") for t in tree)
        if spec.get("guardrails") and not has_tests:
            untested_guardrails.append({"id": meta["id"],
                                        "guardrails": [g["id"] for g in spec["guardrails"]]})
        if ev := c["status"].get("evaluation"):
            tested = {ev["model"]}
            if missing := set(spec.get("models", {}).get("supported", [])) - tested:
                unevaluated_models.append({"id": meta["id"], "models": sorted(missing)})
        if len(spec.get("limitations", [])) < 3:
            no_limitations.append({"id": meta["id"], "count": len(spec.get("limitations", []))})
    return {
        "untested_guardrails": untested_guardrails,
        "unevaluated_models": unevaluated_models,
        "few_limitations": no_limitations,
    }


def _emit_facets(data: pathlib.Path, cards: list[dict], taxonomy: dict) -> None:
    """Facet labels and descriptions, so the browse UI is not a list of slugs."""
    labels = {
        "business": {t["id"]: {"label": t["label"], "description": t.get("description", ""),
                               "steward": t.get("steward", "")} for t in taxonomy["business"]},
        "technical": {t["id"]: {"label": t["label"], "axis": t.get("axis", ""),
                                "description": t.get("description", "")}
                      for t in taxonomy["technical"]},
        "lifecycle": {k: {"label": v} for k, v in LIFECYCLE_LABELS.items()},
        "pattern": {t["id"]: {"label": t["id"].replace("-", " ").title()}
                    for t in taxonomy["reference_architectures"]},
    }
    (data / "facets.json").write_text(json.dumps(labels, indent=2, sort_keys=True))
