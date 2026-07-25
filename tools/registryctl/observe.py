"""Observation: turn evidence into rubric signals.

This module is the enforcement point for the design's central rule — a harness
author declares intent, the platform observes facts. Every signal here is
derived from something that cannot be asserted in a manifest: the repository
tree, the Git history, the pipeline result, an evaluation report, or a
governance record.

`observe()` is a pure function of (project, manifest, governance record,
reference data). That purity is deliberate: scoring is the part of the system
most likely to be argued about, so it must be trivially unit-testable and
reproducible from stored inputs.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from .validate import ReferenceData, resolve_range

REQUIRED_README_SECTIONS = ["Overview", "Quick Start", "Configuration", "Limitations", "Support"]

OPTIONAL_MANIFEST_FIELDS = [
    ("metadata", "description"), ("metadata", "tags"),
    ("spec", "retrieval"), ("spec", "guardrails"), ("spec", "policies"),
    ("spec", "operations"), ("spec", "roadmap"), ("spec", "dependencies"),
    ("spec", "example_skills"),
]


def now() -> datetime:
    return datetime.now(timezone.utc)


def days_since(ts: str | None) -> int | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now() - dt).days


def observe(project: Any, manifest: dict, governance: dict, ref: ReferenceData,
            *, validation_errors: int = 0) -> dict[str, Any]:
    """Every rubric signal. Values are bool (earned/not) or float in [0,1]."""
    spec = manifest["spec"]
    readme = project.files.get("README.md", "")
    evals = project.evaluations
    latest = evals[-1] if evals else None
    pipeline_status = (project.pipeline or {}).get("status")

    f: dict[str, Any] = {}

    # ---- documentation ---------------------------------------------------
    present = [s for s in REQUIRED_README_SECTIONS
               if re.search(rf"^##\s+{re.escape(s)}\b", readme, re.M | re.I)]
    f["readme-sections"] = len(present) == len(REQUIRED_README_SECTIONS)
    f["_readme_missing"] = [s for s in REQUIRED_README_SECTIONS if s not in present]
    # "Executable" means CI runs it: a fenced block tagged `shell exec` is what
    # the smoke job picks up. Documentation that cannot rot is the only kind
    # that does not.
    f["quickstart-executable"] = "shell exec" in project.files.get("docs/quickstart.md", "")
    f["architecture-diagram"] = (
        any(t.startswith("architecture/") and t.endswith(".mmd") for t in project.tree)
        or "```mermaid" in readme
    )
    f["no-broken-links"] = _links_ok(project)

    # ---- metadata --------------------------------------------------------
    f["schema-valid"] = validation_errors == 0
    f["optional-completeness"] = _optional_ratio(manifest)
    f["limitations-declared"] = len(spec.get("limitations", [])) >= 3

    # ---- evaluation ------------------------------------------------------
    golden = _suites(latest, "golden")
    f["golden-suite-exists"] = bool(golden) and sum(s["cases"]["total"] for s in golden) >= 50
    if latest:
        totals = latest["totals"]
        f["pass-rate"] = _band(totals.get("pass_rate", 0), [(0.95, 1.0), (0.90, 0.75), (0.80, 0.375)])
        f["coverage"] = _band(totals.get("coverage", 0), [(0.80, 1.0), (0.60, 0.6)])
        f["adversarial-suite"] = bool(_suites(latest, "adversarial") or _suites(latest, "guardrail"))
        f["freshness"] = (days_since(latest["run"]["started_at"]) or 999) <= 30
        env = latest["run"]["environment"]
        f["reproducible-eval"] = bool(env.get("model_build")) and env.get("seed") is not None
        f["trend"] = _no_regression(evals)
    else:
        f |= {k: False for k in
              ["pass-rate", "coverage", "adversarial-suite", "freshness", "reproducible-eval", "trend"]}

    # ---- engineering -----------------------------------------------------
    f["pipeline-green"] = pipeline_status == "success"
    f["unit-tests"] = any(t.startswith("tests/") for t in project.tree)
    f["pinned-dependencies"] = "harness.lock" in project.files or not _has_ranges(spec)
    f["changelog"] = bool(project.files.get("CHANGELOG.md")) and (
        not project.releases
        or project.releases[0]["tag_name"].lstrip("v") in project.files.get("CHANGELOG.md", "")
    )

    # ---- security --------------------------------------------------------
    f["secret-scan"] = project.secret_scan_clean
    f["sbom"] = project.sbom_published
    f["signed-release"] = project.signed_tag
    f["plugin-trust"] = _trust_ratio(spec, ref)

    # ---- stewardship -----------------------------------------------------
    members = ref.owner_groups.get(manifest["metadata"]["owner"])
    f["owner-valid"] = members is None or members >= 2
    f["codeowners"] = _codeowners_ok(project, manifest)
    f["review-recency"] = _review_current(governance)
    f["responsiveness"] = (
        project.open_issue_stats.get("counts", {}).get("opened", 0) == 0
        or (project.median_issue_response_days is not None
            and project.median_issue_response_days <= 5)
    )

    # ---- governance evidence (not a scored signal; gates use it) ---------
    f["governance_record"] = bool(governance.get("certified_at"))
    return f


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _read(project: Any, path: str) -> str:
    return project.files.get(path, "")


def _suites(report: dict | None, kind: str) -> list[dict]:
    return [s for s in (report or {}).get("suites", []) if s.get("kind") == kind]


def _band(value: float, thresholds: list[tuple[float, float]]) -> float:
    for cutoff, fraction in thresholds:
        if value >= cutoff:
            return fraction
    return 0.0


def _no_regression(evals: list[dict]) -> bool:
    rates = [e["totals"].get("pass_rate", 0) for e in evals[-3:]]
    if len(rates) < 2:
        return True
    return all(rates[i + 1] >= rates[i] - 0.02 for i in range(len(rates) - 1))


def _optional_ratio(manifest: dict) -> float:
    present = sum(1 for top, field in OPTIONAL_MANIFEST_FIELDS if manifest.get(top, {}).get(field))
    return present / len(OPTIONAL_MANIFEST_FIELDS)


def _has_ranges(spec: dict) -> bool:
    return bool(spec.get("dependencies")) or bool(
        spec.get("plugins", {}).get("required") or spec.get("plugins", {}).get("optional")
    )


def _trust_ratio(spec: dict, ref: ReferenceData) -> float:
    required = spec.get("plugins", {}).get("required", [])
    if not required:
        return 1.0
    if not ref.plugins:
        return 1.0
    scored = []
    for p in required:
        tier = ref.plugins.get(p["id"], {}).get("trust_tier", "experimental")
        scored.append({"core": 1.0, "certified": 1.0, "community": 0.5}.get(tier, 0.0))
    return sum(scored) / len(scored)


def _codeowners_ok(project: Any, manifest: dict) -> bool:
    co = project.files.get("CODEOWNERS")
    if not co:
        return False
    owners = set(re.findall(r"@[\w.\-]+", co))
    return set(manifest["metadata"]["maintainers"]) <= owners


def _review_current(governance: dict) -> bool:
    due = governance.get("review_due")
    if not due:
        return False
    try:
        return date.fromisoformat(due) >= now().date()
    except ValueError:
        return False


_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def _links_ok(project: Any) -> bool:
    """Repo-relative links in fetched Markdown must resolve within the project."""
    known = set(project.tree) | set(project.files)
    for path, text in project.files.items():
        if not path.endswith(".md"):
            continue
        base = path.rsplit("/", 1)[0] if "/" in path else ""
        for target in _LINK_RE.findall(text):
            if re.match(r"^(https?:|mailto:|#)", target):
                continue
            clean = target.split("#")[0].rstrip("/")
            if not clean:
                continue
            resolved = _normalise(f"{base}/{clean}" if base else clean)
            if resolved in known or any(t.startswith(resolved + "/") for t in known):
                continue
            return False
    return True


def _normalise(path: str) -> str:
    parts: list[str] = []
    for seg in path.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
            continue
        parts.append(seg)
    return "/".join(parts)


def unresolved_plugins(spec: dict, ref: ReferenceData) -> list[dict]:
    """Plugin resolution table shown on the card."""
    out = []
    for kind in ("required", "optional"):
        for p in spec.get("plugins", {}).get(kind, []):
            known = ref.plugins.get(p["id"])
            resolved = resolve_range(p["range"], known.get("versions", [])) if known else None
            out.append({
                "id": p["id"],
                "kind": kind,
                "range": p["range"],
                "purpose": p.get("purpose", ""),
                "resolved": resolved,
                "trust_tier": (known or {}).get("trust_tier", "unknown"),
                "status": "ok" if resolved else "unresolved",
            })
    return out
