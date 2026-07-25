"""Generate evaluation history for the demo harnesses.

Evaluation reports are CI artefacts in production — nobody writes them by hand,
so nobody should hand-write the fixtures either. This produces a deterministic,
seeded history per harness so the registry's trend charts, regression radar and
freshness signals have something real-shaped to work on.

    python3 tools/dev/make_fixture_evals.py

Deterministic: same inputs, same bytes, so re-running does not churn the diff.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import random
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[2]
HARNESSES = ROOT / "examples/harnesses"
TODAY = datetime(2026, 7, 24, 3, 0, tzinfo=timezone.utc)

# harness -> profile. `drift` is applied per run, `shock` injects a one-off event
# at a given index so the regression radar and annotation pins have real inputs.
PROFILES = {
    # contract-review already carries a hand-written report for the current
    # release (it is the worked example in the design document); we generate the
    # history *behind* it and leave that file alone.
    "contract-review": {
        "runs": 9, "cadence_days": 14, "version": "2.3.0", "keep_existing": True,
        "start_offset_days": 21,
        "model": "claude-sonnet-5", "builds": ["gw-2026.03.02-b33", "gw-2026.05.11-b38"],
        "pass_rate": 0.951, "drift": 0.0018, "coverage": 0.84,
        "latency": 171000, "cost": 0.43, "cases": 180,
        "suites": [("golden", 180), ("adversarial", 64)],
        "metrics": {"clause_f1": (0.905, 0.85), "severity_agreement": (0.836, 0.80),
                    "citation_grounding": (0.991, 0.99), "hallucinated_citations": (0.007, 0.01)},
    },
    "ticket-triage": {
        "runs": 12, "cadence_days": 7, "version": "3.2.0",
        "model": "claude-haiku-4-5", "builds": ["gw-2026.05.11-b38", "gw-2026.07.02-b41"],
        "pass_rate": 0.931, "drift": 0.0015, "coverage": 0.84,
        "latency": 7400, "cost": 0.018, "cases": 240,
        "suites": [("golden", 240), ("adversarial", 48)],
        "metrics": {"category_f1": (0.884, 0.85), "routing_accuracy": (0.912, 0.88),
                    "calibration_ece": (0.041, 0.06)},
        "human_review": {"reviewed_at": "2026-06-28", "reviewers": ["@d.fischer", "@r.kaur"],
                         "sample_size": 60, "agreement_with_automated": 0.91, "verdict": "accept"},
    },
    "document-segmentation": {
        "runs": 9, "cadence_days": 14, "version": "4.1.2",
        "model": "claude-haiku-4-5", "builds": ["gw-2026.05.11-b38", "gw-2026.07.02-b41"],
        "pass_rate": 0.906, "drift": 0.002, "coverage": 0.72,
        "latency": 11000, "cost": 0.04, "cases": 120,
        "suites": [("golden", 120)],
        "metrics": {"boundary_f1": (0.897, 0.85), "anchor_integrity": (0.998, 0.99)},
    },
    "citation-grounding": {
        "runs": 14, "cadence_days": 7, "version": "2.2.4",
        "model": "claude-sonnet-5", "builds": ["gw-2026.07.02-b41"],
        "pass_rate": 0.978, "drift": 0.0008, "coverage": 0.91,
        "latency": 2600, "cost": 0.006, "cases": 300,
        "suites": [("golden", 300), ("adversarial", 60)],
        "metrics": {"false_grounded_rate": (0.004, 0.01), "false_unsupported_rate": (0.031, 0.05)},
        "human_review": {"reviewed_at": "2026-07-02", "reviewers": ["@h.varga", "@a.okafor"],
                         "sample_size": 40, "agreement_with_automated": 0.95, "verdict": "accept"},
    },
    "code-review-assistant": {
        # A real regression: the upstream repo-context plugin slowed down, and the
        # latency gate is currently waived while it is fixed.
        "runs": 11, "cadence_days": 7, "version": "1.6.1",
        "model": "claude-sonnet-5", "builds": ["gw-2026.05.11-b38", "gw-2026.07.02-b41"],
        "pass_rate": 0.874, "drift": 0.001, "coverage": 0.69,
        "latency": 158000, "cost": 0.31, "cases": 160,
        "suites": [("golden", 160), ("adversarial", 40)],
        "metrics": {"finding_precision": (0.812, 0.75), "reproduction_rate": (0.741, 0.70),
                    "false_positive_rate": (0.089, 0.12)},
        "shock": {"at": -2, "pass_rate": -0.031, "latency": 1.9},
    },
    "sql-analyst": {
        "runs": 4, "cadence_days": 21, "version": "0.9.2",
        "model": "claude-opus-5", "builds": ["gw-2026.05.11-b38"],
        "pass_rate": 0.781, "drift": 0.004, "coverage": 0.52,
        "latency": 31000, "cost": 0.09, "cases": 42,   # under the 50-case golden gate
        "suites": [("golden", 42)],
        "metrics": {"execution_accuracy": (0.781, 0.85), "result_exact_match": (0.694, 0.80)},
        "stale_days": 73,     # last run well outside the 30-day freshness window
    },
}


def rng_for(key: str) -> random.Random:
    return random.Random(int(hashlib.sha256(key.encode()).hexdigest()[:8], 16))


def build_report(hid: str, p: dict, i: int, total: int) -> dict:
    r = rng_for(f"{hid}-{i}")
    age_days = ((total - 1 - i) * p["cadence_days"]
                + p.get("stale_days", 0) + p.get("start_offset_days", 0))
    when = TODAY - timedelta(days=age_days)

    steps_back = total - 1 - i
    pass_rate = p["pass_rate"] - p["drift"] * steps_back + r.uniform(-0.004, 0.004)
    latency = p["latency"] * (1 + 0.03 * steps_back + r.uniform(-0.02, 0.02))
    cost = p["cost"] * (1 + r.uniform(-0.05, 0.05))
    coverage = min(0.99, p["coverage"] - 0.004 * steps_back)

    if (shock := p.get("shock")) and i == total + shock["at"]:
        pass_rate += shock["pass_rate"]
        latency *= shock["latency"]

    build = p["builds"][min(len(p["builds"]) - 1, int(i / max(total - 1, 1) * len(p["builds"])))]

    suites = []
    for kind, size in p["suites"]:
        rate = pass_rate if kind == "golden" else min(0.999, pass_rate + 0.03)
        passed = round(size * rate)
        metrics = {}
        if kind == "golden":
            for name, (target, threshold) in p["metrics"].items():
                lower_is_better = any(k in name for k in ("rate", "ece")) and "accuracy" not in name
                value = target * (1 - 0.01 * steps_back) if not lower_is_better \
                    else target * (1 + 0.02 * steps_back)
                value = round(max(0.0, value + r.uniform(-0.006, 0.006)), 4)
                ok = value <= threshold if lower_is_better else value >= threshold
                metrics[name] = {
                    "value": value, "unit": "ratio", "threshold": threshold,
                    "direction": "lower-is-better" if lower_is_better else "higher-is-better",
                    "status": "pass" if ok else "fail",
                }
        else:
            metrics = {"injection_resistance": {
                "value": round(min(0.999, 0.96 + r.uniform(-0.01, 0.02)), 4), "unit": "ratio",
                "threshold": 0.95, "direction": "higher-is-better", "status": "pass"}}
        suites.append({
            "id": kind, "kind": kind,
            "dataset": {
                "id": f"{hid}-{kind}", "version": "v3", "size": size,
                "checksum": "sha256:" + hashlib.sha256(f"{hid}{kind}v3".encode()).hexdigest(),
                "provenance": "hand-labelled" if kind == "golden" else "synthetic",
                "classification": "internal",
            },
            "cases": {"total": size, "passed": passed, "failed": size - passed,
                      "skipped": 0, "errored": 0},
            "metrics": metrics,
            "failures": [
                {"case_id": f"{kind[:3]}-{r.randrange(1000, 9999)}",
                 "reason": reason, "category": category, "artefact": "redacted"}
                for reason, category in FAILURES.get(hid, [])[: min(3, size - passed)]
            ],
        })

    gated_failed = sum(
        1 for s in suites for m in s["metrics"].values() if m.get("status") == "fail")
    return {
        "apiVersion": "harness.registry.acme.internal/v1",
        "kind": "EvaluationReport",
        "harness": hid,
        "version": p["version"],
        "run": {
            "id": when.strftime("%Y-%m-%dT%H%MZ") + f"-{10000 + i * 37}",
            "started_at": when.isoformat().replace("+00:00", "Z"),
            "duration_s": round(600 + r.uniform(0, 2400), 1),
            "commit": hashlib.sha256(f"{hid}{i}".encode()).hexdigest()[:40],
            "pipeline_url": f"https://gitlab.acme.internal/ai-platform/harnesses/{hid}/-/pipelines/{40000 + i}",
            "trigger": "schedule",
            "environment": {
                "model": p["model"], "model_build": build,
                "runtime": "agent-sdk-python", "temperature": 0, "seed": 20260101 + i,
            },
        },
        "suites": suites,
        "totals": {
            "pass_rate": round(pass_rate, 4),
            "coverage": round(coverage, 4),
            "hallucination_rate": round(max(0.0, 0.012 + 0.002 * steps_back + r.uniform(-0.003, 0.003)), 4),
            "grounding_rate": round(min(0.999, 0.982 - 0.002 * steps_back), 4),
            "p50_latency_ms": int(latency * 0.44),
            "p95_latency_ms": int(latency),
            "cost_per_case": round(cost, 4),
            "gated_metrics_failed": gated_failed,
        },
        **({"human_review": p["human_review"]} if p.get("human_review") and i == p["runs"] - 1 else {}),
    }


FAILURES = {
    "ticket-triage": [
        ("Ticket text was a forwarded email chain; the harness triaged the wrong message.", "input-shape"),
        ("Category exists in the catalogue but had no training examples.", "catalogue-gap"),
        ("Confidence 0.71, just under the gate; correct answer routed to a human.", "calibration"),
    ],
    "document-segmentation": [
        ("Numbered list inside a clause split into separate segments.", "boundary"),
        ("Table spanning three pages emitted as one oversized segment.", "table"),
    ],
    "citation-grounding": [
        ("Correct paraphrase reported as unsupported (expected under exact mode).", "by-design"),
        ("Unicode quotation marks broke the span match.", "normalisation"),
    ],
    "code-review-assistant": [
        ("Race condition finding could not be reproduced in the sandbox; downgraded to observation.", "reproduction"),
        ("Suggested fix compiled but broke an untested behaviour.", "suggestion-quality"),
        ("Timeout on a 4,100-line monorepo MR.", "performance"),
    ],
    "sql-analyst": [
        ("Question implied a metric not defined in the semantic layer.", "semantic-gap"),
        ("Correct SQL, wrong interpretation of 'last quarter' (fiscal vs calendar).", "interpretation"),
    ],
}


def main() -> int:
    written = 0
    for hid, profile in PROFILES.items():
        out = HARNESSES / hid / "evaluations" / "results"
        out.mkdir(parents=True, exist_ok=True)
        if not profile.get("keep_existing"):
            for f in out.glob("*.json"):
                f.unlink()
        for i in range(profile["runs"]):
            report = build_report(hid, profile, i, profile["runs"])
            name = f"{report['run']['started_at'][:10]}-{report['suites'][0]['kind']}.json"
            (out / name).write_text(json.dumps(report, indent=2) + "\n")
            written += 1
        print(f"{hid}: {profile['runs']} report(s)")
    print(f"{written} evaluation report(s) written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
