"""Indexing: turn crawl output into the registry snapshot.

Reference implementation of `registryctl index`. Produces, under snapshot/:

    registry.lock.yaml      id -> {project, commit, tag, manifest digest}
    harnesses/<id>.json     card = declared spec + computed status
    graph/edges.json        typed edges between harnesses/skills/plugins/teams
    catalog.json            compact payload the browser downloads for faceting
    reports/index-report.json

Invariant that makes the whole design work:

    spec   = what a human asserted, validated against harness.schema.json
    status = what the platform observed, never writable by the harness author

Everything a consumer needs to *trust* (quality score, lifecycle, evaluation
results, consumers) lives in `status`.
"""

from __future__ import annotations

import collections
import dataclasses
import hashlib
import json
import pathlib
import re
from datetime import date, datetime, timezone
from typing import Any

import yaml

UTC_NOW = datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------
def score(card_facts: dict[str, bool | float], rubric: dict) -> dict:
    """Apply quality-rubric.yaml to observed facts. Pure function, unit tested."""
    breakdown: dict[str, dict] = {}
    total = 0.0
    for dim_name, dim in rubric["dimensions"].items():
        earned = sum(
            sig["points"]
            for sig in dim["signals"]
            if _signal_value(card_facts, sig["id"]) is True
        )
        earned += sum(
            sig["points"] * v
            for sig in dim["signals"]
            if isinstance(v := _signal_value(card_facts, sig["id"]), float)
        )
        possible = sum(s["points"] for s in dim["signals"])
        dim_score = (earned / possible) * dim["weight"] if possible else 0.0
        breakdown[dim_name] = {
            "earned": round(earned, 2),
            "possible": possible,
            "weighted": round(dim_score, 2),
            "failed_signals": [
                s["id"] for s in dim["signals"] if not _signal_value(card_facts, s["id"])
            ],
        }
        total += dim_score

    for gate in rubric["gates"]:
        if not _signal_value(card_facts, gate["id"]):
            total = min(total, gate["cap"])

    total = round(total)
    band = next(b["label"] for b in rubric["bands"] if total >= b["min"])
    return {"score": total, "band": band, "breakdown": breakdown}


def _signal_value(facts: dict, sid: str) -> bool | float:
    return facts.get(sid, False)


def effective_lifecycle(declared: str, facts: dict, computed_score: int, rubric: dict) -> tuple[str, list[str]]:
    """Downgrade over-claimed maturity. Never upgrades — promotion is a human act."""
    order = ["experimental", "prototype", "team-ready", "org-ready", "certified"]
    if declared in ("deprecated", "retired"):
        return declared, []
    reasons: list[str] = []
    level = declared
    while level in order:
        gate = rubric["lifecycle_gates"][level]
        missing = [r for r in gate.get("requires", []) if r in _all_signal_ids(rubric) and not facts.get(r)]
        if computed_score < gate.get("min_score", 0):
            missing.append(f"quality score {computed_score} < {gate['min_score']}")
        if gate.get("governance_record") and not facts.get("governance_record"):
            missing.append("no certification record in governance/certifications/")
        if not missing:
            break
        reasons.append(f"{level}: {', '.join(missing)}")
        idx = order.index(level)
        if idx == 0:
            break
        level = order[idx - 1]
    return level, reasons


def _all_signal_ids(rubric: dict) -> set[str]:
    return {s["id"] for d in rubric["dimensions"].values() for s in d["signals"]}


# --------------------------------------------------------------------------
# Card assembly
# --------------------------------------------------------------------------
@dataclasses.dataclass
class IndexContext:
    rubric: dict
    taxonomy: dict
    governance: dict
    skills: dict            # skill id -> skill manifest (from Skill Marketplace export)
    plugins: dict           # plugin id -> {versions, trust_tier}
    evaluations: dict       # harness id -> list[EvaluationReport]


def build_card(crawled: dict, ctx: IndexContext) -> dict:
    manifest = yaml.safe_load(crawled["files"]["harness.yaml"])
    meta, spec = manifest["metadata"], manifest["spec"]
    hid = meta["id"]

    evals = sorted(ctx.evaluations.get(hid, []), key=lambda e: e["run"]["started_at"])
    latest = evals[-1] if evals else None
    gov = ctx.governance.get(hid, {})

    facts = observe(crawled, manifest, evals, gov, ctx)
    quality = score(facts, ctx.rubric)
    lifecycle, downgrade_reasons = effective_lifecycle(
        spec["lifecycle"], facts, quality["score"], ctx.rubric
    )

    consumers = [
        {"id": sid, "name": s["name"], "team": s.get("team")}
        for sid, s in ctx.skills.items()
        if any(h["id"] == hid for h in s.get("harnesses", []))
    ]

    return {
        "apiVersion": "harness.registry.acme.internal/v1",
        "kind": "HarnessCard",
        "spec": manifest,
        "status": {
            "indexed_at": UTC_NOW.isoformat(timespec="seconds"),
            "source": {
                "project": crawled["project_path"],
                "project_id": crawled["project_id"],
                "web_url": crawled["web_url"],
                "commit": crawled["commit"],
                "visibility": crawled["visibility"],
                "manifest_digest": "sha256:" + hashlib.sha256(
                    crawled["files"]["harness.yaml"].encode()
                ).hexdigest(),
            },
            "released_version": _latest_release(crawled),
            "versions": [r["tag_name"] for r in crawled.get("releases", [])][:50],
            "last_updated": crawled["last_activity_at"],
            "last_commit_days": _days_since(crawled["last_activity_at"]),
            "pipeline_status": (crawled.get("pipeline") or {}).get("status"),
            "quality": quality,
            "lifecycle": {
                "declared": spec["lifecycle"],
                "effective": lifecycle,
                "downgraded": lifecycle != spec["lifecycle"],
                "reasons": downgrade_reasons,
            },
            "approval": {
                "state": gov.get("state", "unapproved"),
                "approved_by": gov.get("approved_by"),
                "approved_at": gov.get("approved_at"),
                "last_reviewed": gov.get("last_reviewed"),
                "review_due": gov.get("review_due"),
                "overdue": _overdue(gov.get("review_due")),
            },
            "evaluation": _eval_summary(evals) if latest else None,
            "evaluation_trend": [
                {
                    "run": e["run"]["started_at"][:10],
                    "version": e["version"],
                    "pass_rate": e["totals"]["pass_rate"],
                    "hallucination_rate": e["totals"].get("hallucination_rate"),
                    "p95_latency_ms": e["totals"].get("p95_latency_ms"),
                    "cost_per_case": e["totals"].get("cost_per_case"),
                }
                for e in evals[-30:]
            ],
            "consumers": consumers,
            "consumer_count": len(consumers),
            "plugin_resolution": _resolve_plugins(spec, ctx),
            "warnings": facts.get("_warnings", []),
        },
    }


def observe(crawled: dict, manifest: dict, evals: list, gov: dict, ctx: IndexContext) -> dict:
    """Every rubric signal, derived from evidence. Extended as the rubric grows."""
    readme = crawled["files"].get("README.md", "")
    spec = manifest["spec"]
    latest = evals[-1] if evals else None
    pipeline = crawled.get("pipeline") or {}
    required = spec.get("plugins", {}).get("required", [])

    facts: dict[str, Any] = {
        "schema-valid": True,  # crawl output only reaches here if validation passed
        "readme-sections": all(
            re.search(rf"^##\s+{s}", readme, re.M | re.I)
            for s in ["Overview", "Quick Start", "Configuration", "Limitations", "Support"]
        ),
        "architecture-diagram": "architecture/overview.mmd" in crawled["files"],
        "changelog": bool(crawled["files"].get("CHANGELOG.md", "")),
        "limitations-declared": len(spec.get("limitations", [])) >= 3,
        "pipeline-green": pipeline.get("status") == "success",
        "pinned-dependencies": "harness.lock" in crawled["files"],
        "owner-valid": True,
        "governance_record": bool(gov.get("certified_at")),
        "optional-completeness": _optional_field_ratio(manifest),
    }
    if latest:
        totals = latest["totals"]
        golden = [s for s in latest["suites"] if s["kind"] == "golden"]
        facts |= {
            "golden-suite-exists": bool(golden) and golden[0]["cases"]["total"] >= 50,
            "pass-rate": _band(totals["pass_rate"], [(0.95, 1.0), (0.90, 0.75), (0.80, 0.375)]),
            "coverage": _band(totals.get("coverage", 0), [(0.8, 1.0), (0.6, 0.6)]),
            "adversarial-suite": any(
                s["kind"] in ("adversarial", "guardrail") for s in latest["suites"]
            ),
            "freshness": _days_since(latest["run"]["started_at"]) <= 30,
            "reproducible-eval": all(
                k in latest["run"]["environment"] for k in ("model_build", "seed")
            ),
            "trend": _no_regression(evals),
        }
    facts["plugin-trust"] = _band(
        _trust_ratio(required, ctx), [(1.0, 1.0), (0.8, 0.5)]
    )
    return facts


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def _band(value: float, thresholds: list[tuple[float, float]]) -> float:
    for cutoff, fraction in thresholds:
        if value >= cutoff:
            return fraction
    return 0.0


def _days_since(ts: str) -> int:
    return (UTC_NOW - datetime.fromisoformat(ts.replace("Z", "+00:00"))).days


def _overdue(due: str | None) -> bool:
    return bool(due) and date.fromisoformat(due) < UTC_NOW.date()


def _latest_release(crawled: dict) -> str | None:
    rel = crawled.get("releases") or []
    return rel[0]["tag_name"] if rel else None


def _no_regression(evals: list) -> bool:
    recent = [e["totals"]["pass_rate"] for e in evals[-3:]]
    return len(recent) < 2 or min(recent[1:]) >= max(recent[:-1]) - 0.02


def _trust_ratio(required: list[dict], ctx: IndexContext) -> float:
    if not required:
        return 1.0
    trusted = sum(
        1 for p in required
        if ctx.plugins.get(p["id"], {}).get("trust_tier") in ("core", "certified")
    )
    return trusted / len(required)


def _optional_field_ratio(manifest: dict) -> float:
    optional = ["description", "tags", "retrieval", "guardrails", "policies",
                "operations", "roadmap", "dependencies", "example_skills"]
    present = sum(
        1 for f in optional
        if manifest["spec"].get(f) or manifest["metadata"].get(f)
    )
    return present / len(optional)


def _eval_summary(evals: list) -> dict:
    latest = evals[-1]
    return {
        "run_id": latest["run"]["id"],
        "run_at": latest["run"]["started_at"],
        "version": latest["version"],
        "model": latest["run"]["environment"]["model"],
        "totals": latest["totals"],
        "suites": [
            {
                "id": s["id"],
                "kind": s["kind"],
                "cases": s["cases"],
                "dataset": {"id": s["dataset"]["id"], "size": s["dataset"]["size"]},
                "metrics": {
                    k: {"value": v["value"], "status": v.get("status"), "unit": v.get("unit")}
                    for k, v in s["metrics"].items()
                },
            }
            for s in latest["suites"]
        ],
        "human_review": latest.get("human_review"),
    }


def _resolve_plugins(spec: dict, ctx: IndexContext) -> list[dict]:
    out = []
    for kind in ("required", "optional"):
        for p in spec.get("plugins", {}).get(kind, []):
            known = ctx.plugins.get(p["id"])
            out.append({
                "id": p["id"],
                "kind": kind,
                "range": p["range"],
                "resolved": known.get("latest_matching") if known else None,
                "trust_tier": known.get("trust_tier") if known else "unknown",
                "status": "ok" if known and known.get("latest_matching") else "unresolved",
            })
    return out


# --------------------------------------------------------------------------
# Graph + catalog
# --------------------------------------------------------------------------
def build_graph(cards: list[dict]) -> dict:
    nodes, edges = {}, []
    for c in cards:
        s, st = c["spec"], c["status"]
        hid = s["metadata"]["id"]
        nodes[f"harness:{hid}"] = {
            "type": "harness", "id": hid, "label": s["metadata"]["name"],
            "lifecycle": st["lifecycle"]["effective"], "team": s["metadata"]["team"],
        }
        nodes.setdefault(f"team:{s['metadata']['team']}", {"type": "team", "id": s["metadata"]["team"]})
        edges.append({"from": f"team:{s['metadata']['team']}", "to": f"harness:{hid}", "type": "owns"})
        for dep in s["spec"].get("dependencies", []):
            edges.append({"from": f"harness:{hid}", "to": f"harness:{dep['harness']}",
                          "type": dep.get("kind", "composes"), "range": dep["range"]})
        for p in st["plugin_resolution"]:
            nodes.setdefault(f"plugin:{p['id']}", {"type": "plugin", "id": p["id"], "trust": p["trust_tier"]})
            edges.append({"from": f"harness:{hid}", "to": f"plugin:{p['id']}", "type": f"{p['kind']}-plugin"})
        for con in st["consumers"]:
            nodes.setdefault(f"skill:{con['id']}", {"type": "skill", "id": con["id"], "label": con["name"]})
            edges.append({"from": f"skill:{con['id']}", "to": f"harness:{hid}", "type": "implements-with"})
        nodes.setdefault(f"pattern:{s['spec']['architecture']['pattern']}",
                         {"type": "reference-architecture", "id": s["spec"]["architecture"]["pattern"]})
        edges.append({"from": f"harness:{hid}",
                      "to": f"pattern:{s['spec']['architecture']['pattern']}", "type": "implements"})
    _assert_acyclic(edges)
    return {"nodes": list(nodes.values()), "edges": edges}


def _assert_acyclic(edges: list[dict]) -> None:
    adj = collections.defaultdict(list)
    for e in edges:
        if e["type"] in ("extends", "composes", "calls"):
            adj[e["from"]].append(e["to"])
    state: dict[str, int] = {}

    def visit(n: str, path: list[str]) -> None:
        if state.get(n) == 1:
            raise ValueError(f"dependency cycle: {' -> '.join(path + [n])}")
        if state.get(n) == 2:
            return
        state[n] = 1
        for m in adj[n]:
            visit(m, path + [n])
        state[n] = 2

    for node in list(adj):
        visit(node, [])


def build_catalog(cards: list[dict]) -> dict:
    """The single file the browser downloads for faceted search (~1.2 KB/harness).

    Deliberately excludes prose: full-text search is Pagefind's job, and keeping
    prose out is what holds the payload near 6 MB at 5,000 harnesses.
    """
    return {
        "generated_at": UTC_NOW.isoformat(timespec="seconds"),
        "count": len(cards),
        "harnesses": [
            {
                "i": c["spec"]["metadata"]["id"],
                "n": c["spec"]["metadata"]["name"],
                "s": c["spec"]["metadata"]["summary"],
                "o": c["spec"]["metadata"]["owner"],
                "tm": c["spec"]["metadata"]["team"],
                "tg": c["spec"]["metadata"].get("tags", []),
                "bd": c["spec"]["spec"]["domains"]["business"],
                "td": c["spec"]["spec"]["domains"]["technical"],
                "ar": c["spec"]["spec"]["architecture"]["pattern"],
                "md": c["spec"]["spec"]["models"]["supported"],
                "rt": c["spec"]["spec"]["runtimes"],
                "pl": [p["id"] for p in c["status"]["plugin_resolution"] if p["kind"] == "required"],
                "lc": c["status"]["lifecycle"]["effective"],
                "v": c["status"]["released_version"],
                "q": c["status"]["quality"]["score"],
                "e": (c["status"]["evaluation"] or {}).get("totals", {}).get("pass_rate"),
                "sc": c["spec"]["spec"]["security"]["classification"],
                "rl": c["spec"]["spec"]["security"].get("risk_level"),
                "ap": c["status"]["approval"]["state"],
                "u": c["status"]["last_updated"][:10],
                "c": c["status"]["consumer_count"],
            }
            for c in cards
        ],
    }


def write_snapshot(cards: list[dict], out: pathlib.Path) -> None:
    (out / "harnesses").mkdir(parents=True, exist_ok=True)
    for c in cards:
        (out / "harnesses" / f"{c['spec']['metadata']['id']}.json").write_text(
            json.dumps(c, indent=2, sort_keys=True)
        )
    (out / "graph").mkdir(exist_ok=True)
    (out / "graph" / "edges.json").write_text(json.dumps(build_graph(cards), indent=2))
    (out / "catalog.json").write_text(json.dumps(build_catalog(cards), separators=(",", ":")))
    (out / "registry.lock.yaml").write_text(
        yaml.safe_dump(
            {
                "generated_at": UTC_NOW.isoformat(timespec="seconds"),
                "harnesses": {
                    c["spec"]["metadata"]["id"]: {
                        "project": c["status"]["source"]["project"],
                        "commit": c["status"]["source"]["commit"],
                        "release": c["status"]["released_version"],
                        "digest": c["status"]["source"]["manifest_digest"],
                    }
                    for c in sorted(cards, key=lambda x: x["spec"]["metadata"]["id"])
                },
            },
            sort_keys=True,
        )
    )
