"""Indexing: turn crawled projects into the registry snapshot.

Produces, under snapshot/:

    registry.lock.yaml       id -> {project, commit, release, manifest digest}
    harnesses/<id>.json      card = declared spec + computed status
    graph/edges.json         typed nodes and edges (harness/skill/plugin/team/pattern)
    catalog.json             compact payload the browser downloads for faceting
    reports/index-report.json warnings, downgrades, orphans, unresolved refs

Invariant that makes the whole design work:

    spec   = what a human asserted, validated against harness.schema.json
    status = what the platform observed, never writable by the harness author
"""

from __future__ import annotations

import collections
import hashlib
import json
import pathlib
from datetime import date, timedelta
from typing import Any, Iterable

import yaml

from .observe import days_since, now, observe, unresolved_plugins
from .score import effective_lifecycle, review_interval_days, score
from .sources import MANIFEST
from .validate import ReferenceData, Finding, validate_manifest

CARD_API = "harness.registry.acme.internal/v1"


class Governance:
    """Approval, certification and review records held in the registry repo."""

    def __init__(self, root: pathlib.Path | None):
        self.records: dict[str, dict] = {}
        if root and pathlib.Path(root).is_dir():
            for kind in ("approvals", "certifications"):
                for f in sorted((pathlib.Path(root) / kind).glob("*.yaml")):
                    rec = yaml.safe_load(f.read_text()) or {}
                    self.records.setdefault(rec.get("harness", f.stem), {}).update(rec)

    def for_harness(self, hid: str) -> dict:
        return self.records.get(hid, {})


def build_card(project: Any, ref: ReferenceData, gov: Governance) -> dict:
    """One harness card. Never raises: a bad manifest yields an error card."""
    try:
        manifest = yaml.safe_load(project.manifest_text)
    except yaml.YAMLError as e:
        return _error_card(project, [Finding("error", "YAML_PARSE", str(e), "harness.yaml")])

    findings = validate_manifest(manifest, ref, project=project)
    errors = [f for f in findings if f.severity == "error"]
    if errors or not isinstance(manifest, dict) or "metadata" not in manifest:
        return _error_card(project, findings, manifest)

    meta, spec = manifest["metadata"], manifest["spec"]
    hid = meta["id"]
    governance = gov.for_harness(hid)
    evals = project.evaluations
    latest = evals[-1] if evals else None

    facts = observe(project, manifest, governance, ref, validation_errors=len(errors))
    quality = score(facts, ref.rubric)
    lifecycle = effective_lifecycle(spec["lifecycle"], facts, quality["score"],
                                    ref.rubric, latest)

    consumers = _consumers(hid, ref)
    review = _review(governance, lifecycle["effective"], ref.rubric, project)

    return {
        "apiVersion": CARD_API,
        "kind": "HarnessCard",
        "spec": manifest,
        "status": {
            "indexed_at": now().isoformat(timespec="seconds"),
            "valid": True,
            "source": {
                "project": project.project_path,
                "web_url": project.web_url,
                "commit": project.commit,
                "default_branch": project.default_branch,
                "visibility": project.visibility,
                "archived": project.archived,
                "forked_from": project.forked_from,
                "manifest_digest": "sha256:" + hashlib.sha256(
                    project.manifest_text.encode()).hexdigest(),
            },
            "released_version": _latest_release(project),
            "versions": [r["tag_name"] for r in project.releases][:50],
            "releases": project.releases[:20],
            "last_updated": project.last_activity_at,
            "last_updated_days": days_since(project.last_activity_at),
            "pipeline_status": (project.pipeline or {}).get("status"),
            "pipeline_url": (project.pipeline or {}).get("web_url"),
            "quality": quality,
            "lifecycle": lifecycle,
            "approval": review,
            "evaluation": _eval_summary(latest) if latest else None,
            "evaluation_trend": _trend(evals),
            "consumers": consumers,
            "consumer_count": len(consumers),
            "plugin_resolution": unresolved_plugins(spec, ref),
            "content": _content(project),
            "findings": [f.__dict__ for f in findings],
            "warnings": [f"{f.code}: {f.message}" for f in findings if f.severity != "info"],
        },
    }


def _error_card(project: Any, findings: list[Finding], manifest: Any = None) -> dict:
    """A harness that fails validation still gets a card — with the reason on it.

    Hiding broken harnesses would make the registry look healthier than the
    estate is, which is the opposite of the point.
    """
    hid = None
    if isinstance(manifest, dict):
        hid = (manifest.get("metadata") or {}).get("id")
    hid = hid or project.project_path.rsplit("/", 1)[-1]
    return {
        "apiVersion": CARD_API,
        "kind": "HarnessCard",
        "spec": manifest if isinstance(manifest, dict) else {
            "metadata": {"id": hid, "name": hid, "summary": "Manifest failed validation.",
                         "owner": "group:unknown", "team": "unknown", "maintainers": []},
            "spec": {},
        },
        "status": {
            "indexed_at": now().isoformat(timespec="seconds"),
            "valid": False,
            "source": {"project": project.project_path, "web_url": project.web_url,
                       "commit": project.commit, "visibility": project.visibility,
                       "default_branch": project.default_branch,
                       "archived": project.archived,
                       # Digest even for a broken manifest: the lockfile must
                       # identify exactly what the indexer read.
                       "manifest_digest": "sha256:" + hashlib.sha256(
                           project.files.get(MANIFEST, "").encode()).hexdigest()},
            "quality": {"score": 0, "band": "E", "colour": "red", "breakdown": {},
                        "capped_by": "schema-valid", "top_gaps": []},
            "lifecycle": {"declared": "unknown", "effective": "invalid",
                          "downgraded": True, "reasons": ["manifest failed validation"],
                          "next_tier_gaps": []},
            "approval": {"state": "unapproved"},
            "evaluation": None, "evaluation_trend": [], "consumers": [], "consumer_count": 0,
            "plugin_resolution": [], "content": {},
            "findings": [f.__dict__ for f in findings],
            "warnings": [f"{f.code}: {f.message}" for f in findings if f.severity != "info"],
        },
    }


# --------------------------------------------------------------------------
# status sub-objects
# --------------------------------------------------------------------------
def _latest_release(project: Any) -> str | None:
    return project.releases[0]["tag_name"].lstrip("v") if project.releases else None


def _review(governance: dict, tier: str, rubric: dict, project: Any) -> dict:
    last = governance.get("last_reviewed")
    interval = review_interval_days(tier, rubric)
    due = governance.get("review_due")
    if not due and last:
        due = (date.fromisoformat(last) + timedelta(days=interval)).isoformat()
    overdue = bool(due) and date.fromisoformat(due) < now().date()
    return {
        "state": governance.get("state", "unapproved"),
        "approved_by": governance.get("approved_by"),
        "approved_at": governance.get("approved_at"),
        "certified_at": governance.get("certified_at"),
        "certification_expires": governance.get("certification_expires"),
        "last_reviewed": last,
        "review_due": due,
        "review_interval_days": interval,
        "overdue": overdue,
        "waivers": governance.get("waivers", []),
        "notes": governance.get("notes"),
    }


def _eval_summary(latest: dict) -> dict:
    return {
        "run_id": latest["run"]["id"],
        "run_at": latest["run"]["started_at"],
        "age_days": days_since(latest["run"]["started_at"]),
        "version": latest["version"],
        "model": latest["run"]["environment"]["model"],
        "model_build": latest["run"]["environment"].get("model_build"),
        "pipeline_url": latest["run"].get("pipeline_url"),
        "totals": latest["totals"],
        "suites": [
            {
                "id": s["id"],
                "kind": s["kind"],
                "cases": s["cases"],
                "dataset": s["dataset"],
                "metrics": s["metrics"],
                "failures": s.get("failures", []),
                "failure_categories": _categories(s.get("failures", [])),
            }
            for s in latest["suites"]
        ],
        "human_review": latest.get("human_review"),
        "gated_failed": latest["totals"].get("gated_metrics_failed", 0),
    }


def _categories(failures: list[dict]) -> dict[str, int]:
    return dict(collections.Counter(f.get("category", "uncategorised") for f in failures))


def _trend(evals: list[dict]) -> list[dict]:
    return [
        {
            "run": e["run"]["started_at"][:10],
            "version": e["version"],
            "model": e["run"]["environment"]["model"],
            "model_build": e["run"]["environment"].get("model_build"),
            "pass_rate": e["totals"].get("pass_rate"),
            "coverage": e["totals"].get("coverage"),
            "hallucination_rate": e["totals"].get("hallucination_rate"),
            "p95_latency_ms": e["totals"].get("p95_latency_ms"),
            "cost_per_case": e["totals"].get("cost_per_case"),
        }
        for e in evals[-30:]
    ]


def _consumers(hid: str, ref: ReferenceData) -> list[dict]:
    """Reverse index: skills declare harnesses, never the other way round."""
    out = []
    for sid, s in ref.skills.items():
        for h in s.get("harnesses", []):
            if h.get("id") == hid:
                out.append({"id": sid, "name": s.get("name", sid), "team": s.get("team"),
                            "range": h.get("range"), "surfaces": s.get("surfaces", [])})
    return sorted(out, key=lambda c: c["id"])


def _content(project: Any) -> dict:
    """Documentation the site renders inline, pulled from the repository."""
    return {
        "readme": project.files.get("README.md", ""),
        "changelog": project.files.get("CHANGELOG.md", ""),
        "diagrams": {
            k.split("/")[-1].removesuffix(".mmd"): v
            for k, v in project.files.items()
            if k.startswith("architecture/") and k.endswith(".mmd")
        },
        "config_schema": _maybe_json(project.files.get("config/schema.json")),
        "tree": project.tree,
        "counts": {
            "prompts": sum(1 for t in project.tree if t.startswith("prompts/") and t.endswith(".md")),
            "tests": sum(1 for t in project.tree if t.startswith("tests/")),
            "adrs": sum(1 for t in project.tree if t.startswith("architecture/decisions/")),
            "conversations": sum(1 for t in project.tree if t.startswith("conversations/")),
            "datasets": len({t.split("/")[1] for t in project.tree
                             if t.startswith("datasets/") and "/" in t[9:]}),
        },
    }


def _maybe_json(text: str | None) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------
# graph
# --------------------------------------------------------------------------
def build_graph(cards: list[dict]) -> dict:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def node(key: str, **attrs) -> None:
        nodes.setdefault(key, {"key": key, **attrs})

    for c in cards:
        meta, spec = c["spec"].get("metadata", {}), c["spec"].get("spec", {})
        st = c["status"]
        hid = meta.get("id")
        if not hid:
            continue
        node(f"harness:{hid}", type="harness", id=hid, label=meta.get("name", hid),
             lifecycle=st["lifecycle"]["effective"], quality=st["quality"]["score"],
             team=meta.get("team"))
        if team := meta.get("team"):
            node(f"team:{team}", type="team", id=team, label=team)
            edges.append({"from": f"team:{team}", "to": f"harness:{hid}", "type": "owns"})
        for dep in spec.get("dependencies", []):
            node(f"harness:{dep['harness']}", type="harness", id=dep["harness"],
                 label=dep["harness"])
            edges.append({"from": f"harness:{hid}", "to": f"harness:{dep['harness']}",
                          "type": dep.get("kind", "composes"), "range": dep["range"]})
        for p in st.get("plugin_resolution", []):
            node(f"plugin:{p['id']}", type="plugin", id=p["id"], label=p["id"],
                 trust=p["trust_tier"])
            edges.append({"from": f"harness:{hid}", "to": f"plugin:{p['id']}",
                          "type": f"{p['kind']}-plugin"})
        for con in st.get("consumers", []):
            node(f"skill:{con['id']}", type="skill", id=con["id"], label=con["name"])
            edges.append({"from": f"skill:{con['id']}", "to": f"harness:{hid}",
                          "type": "implements-with", "range": con.get("range")})
        if pattern := spec.get("architecture", {}).get("pattern"):
            node(f"pattern:{pattern}", type="reference-architecture", id=pattern, label=pattern)
            edges.append({"from": f"harness:{hid}", "to": f"pattern:{pattern}",
                          "type": "implements"})
        if parent := st["source"].get("forked_from"):
            edges.append({"from": f"harness:{hid}", "to": f"harness:{parent}", "type": "forked-from"})

    cycles = find_cycles(edges)
    return {"nodes": list(nodes.values()), "edges": edges, "cycles": cycles}


def find_cycles(edges: list[dict]) -> list[list[str]]:
    adj: dict[str, list[str]] = collections.defaultdict(list)
    for e in edges:
        if e["type"] in ("extends", "composes", "calls"):
            adj[e["from"]].append(e["to"])
    state: dict[str, int] = {}
    cycles: list[list[str]] = []

    def visit(n: str, path: list[str]) -> None:
        if state.get(n) == 1:
            cycles.append(path[path.index(n):] + [n])
            return
        if state.get(n) == 2:
            return
        state[n] = 1
        for m in adj.get(n, []):
            visit(m, path + [n])
        state[n] = 2

    for node in list(adj):
        visit(node, [])
    return cycles


def neighbourhood(card: dict, graph: dict, limit: int = 24) -> dict:
    """One-hop subgraph for the harness page."""
    hid = card["spec"]["metadata"]["id"]
    me = f"harness:{hid}"
    edges = [e for e in graph["edges"] if e["from"] == me or e["to"] == me][:limit]
    keys = {me} | {e["from"] for e in edges} | {e["to"] for e in edges}
    by_key = {n["key"]: n for n in graph["nodes"]}
    return {"nodes": [by_key[k] for k in keys if k in by_key], "edges": edges, "centre": me}


# --------------------------------------------------------------------------
# catalog + snapshot
# --------------------------------------------------------------------------
def build_catalog(cards: list[dict]) -> dict:
    """The single file the browser downloads for faceted search.

    Short keys and no prose: that exclusion is what keeps this near 1 KB per
    harness, which is what makes client-side faceting viable at 5,000 harnesses.
    """
    return {
        "generated_at": now().isoformat(timespec="seconds"),
        "count": len(cards),
        "harnesses": [
            {
                "i": c["spec"]["metadata"]["id"],
                "n": c["spec"]["metadata"]["name"],
                "s": c["spec"]["metadata"].get("summary", ""),
                "o": c["spec"]["metadata"].get("owner", ""),
                "tm": c["spec"]["metadata"].get("team", ""),
                "tg": c["spec"]["metadata"].get("tags", []),
                "bd": c["spec"].get("spec", {}).get("domains", {}).get("business", []),
                "td": c["spec"].get("spec", {}).get("domains", {}).get("technical", []),
                "ar": c["spec"].get("spec", {}).get("architecture", {}).get("pattern", ""),
                "md": c["spec"].get("spec", {}).get("models", {}).get("supported", []),
                "rt": c["spec"].get("spec", {}).get("runtimes", []),
                "pl": [p["id"] for p in c["status"]["plugin_resolution"] if p["kind"] == "required"],
                "lc": c["status"]["lifecycle"]["effective"],
                "v": c["status"].get("released_version"),
                "q": c["status"]["quality"]["score"],
                "e": ((c["status"].get("evaluation") or {}).get("totals", {}) or {}).get("pass_rate"),
                "sc": c["spec"].get("spec", {}).get("security", {}).get("classification", ""),
                "rl": c["spec"].get("spec", {}).get("security", {}).get("risk_level", ""),
                "ap": c["status"]["approval"]["state"],
                "u": (c["status"].get("last_updated") or "")[:10],
                "c": c["status"]["consumer_count"],
                "w": len(c["status"]["warnings"]),
            }
            for c in cards
        ],
    }


def index_report(cards: list[dict], graph: dict, ref: ReferenceData) -> dict:
    invalid = [c for c in cards if not c["status"].get("valid")]
    downgraded = [c for c in cards if c["status"]["lifecycle"].get("downgraded")
                  and c["status"].get("valid")]
    overdue = [c for c in cards if c["status"]["approval"].get("overdue")]
    unresolved = [
        {"harness": c["spec"]["metadata"]["id"], "plugin": p["id"], "range": p["range"]}
        for c in cards for p in c["status"]["plugin_resolution"] if p["status"] != "ok"
    ]
    known = {c["spec"]["metadata"]["id"] for c in cards}
    dangling = [
        {"harness": c["spec"]["metadata"]["id"], "depends_on": d["harness"]}
        for c in cards for d in c["spec"].get("spec", {}).get("dependencies", [])
        if d["harness"] not in known
    ]
    stale = [c for c in cards if (c["status"].get("last_updated_days") or 0) > 180]
    unevaluated = [c for c in cards if c["status"].get("valid") and not c["status"]["evaluation"]]
    duplicates = [i for i, n in collections.Counter(
        c["spec"]["metadata"]["id"] for c in cards).items() if n > 1]

    return {
        "generated_at": now().isoformat(timespec="seconds"),
        "totals": {
            "harnesses": len(cards),
            "valid": len(cards) - len(invalid),
            "invalid": len(invalid),
            "downgraded": len(downgraded),
            "overdue_reviews": len(overdue),
            "unresolved_plugins": len(unresolved),
            "dangling_dependencies": len(dangling),
            "dependency_cycles": len(graph["cycles"]),
            "duplicate_ids": len(duplicates),
            "stale_180d": len(stale),
            "unevaluated": len(unevaluated),
        },
        "invalid": [
            {"id": c["spec"]["metadata"]["id"], "project": c["status"]["source"]["project"],
             "findings": c["status"]["findings"]}
            for c in invalid
        ],
        "downgraded": [
            {"id": c["spec"]["metadata"]["id"],
             "declared": c["status"]["lifecycle"]["declared"],
             "effective": c["status"]["lifecycle"]["effective"],
             "reasons": c["status"]["lifecycle"]["reasons"]}
            for c in downgraded
        ],
        "overdue_reviews": [
            {"id": c["spec"]["metadata"]["id"], "due": c["status"]["approval"]["review_due"],
             "owner": c["spec"]["metadata"]["owner"]}
            for c in overdue
        ],
        "warnings": [
            {"id": c["spec"]["metadata"]["id"], "warnings": c["status"]["warnings"]}
            for c in cards if c["status"]["warnings"]
        ],
        "unresolved_plugins": unresolved,
        "dangling_dependencies": dangling,
        "dependency_cycles": graph["cycles"],
        "duplicate_ids": duplicates,
        "stale": [{"id": c["spec"]["metadata"]["id"],
                   "days": c["status"].get("last_updated_days")} for c in stale],
        "unevaluated": [c["spec"]["metadata"]["id"] for c in unevaluated],
    }


def write_snapshot(cards: list[dict], out: pathlib.Path, ref: ReferenceData) -> dict:
    out = pathlib.Path(out)
    (out / "harnesses").mkdir(parents=True, exist_ok=True)
    (out / "graph").mkdir(exist_ok=True)
    (out / "reports").mkdir(exist_ok=True)

    cards = sorted(cards, key=lambda c: c["spec"]["metadata"]["id"])
    for c in cards:
        (out / "harnesses" / f"{c['spec']['metadata']['id']}.json").write_text(
            json.dumps(c, indent=2, sort_keys=True)
        )

    graph = build_graph(cards)
    (out / "graph" / "edges.json").write_text(json.dumps(graph, indent=2, sort_keys=True))
    (out / "catalog.json").write_text(json.dumps(build_catalog(cards), separators=(",", ":")))
    report = index_report(cards, graph, ref)
    (out / "reports" / "index-report.json").write_text(json.dumps(report, indent=2))
    (out / "registry.lock.yaml").write_text(yaml.safe_dump({
        "generated_at": now().isoformat(timespec="seconds"),
        "harnesses": {
            c["spec"]["metadata"]["id"]: {
                "project": c["status"]["source"]["project"],
                "commit": c["status"]["source"]["commit"],
                "release": c["status"].get("released_version"),
                "digest": c["status"]["source"].get("manifest_digest"),
                "lifecycle": c["status"]["lifecycle"]["effective"],
                "quality": c["status"]["quality"]["score"],
            }
            for c in cards
        },
    }, sort_keys=True))
    return report


def index_projects(projects: Iterable[Any], ref: ReferenceData, gov: Governance) -> list[dict]:
    return [build_card(p, ref, gov) for p in projects]
