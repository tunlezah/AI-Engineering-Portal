"""Scoring and lifecycle gating.

Pure functions over the fact dictionary produced by `observe`. Two outputs:

  score()              -> 0-100, a band, and a per-dimension breakdown that
                          always names the failed signals, because a score
                          without a remedy is a scold rather than a tool.
  effective_lifecycle() -> the maturity tier the evidence supports. Downgrades
                          over-claims; never upgrades. Promotion is a human act
                          with a governance record; demotion is a fact.
"""

from __future__ import annotations

from typing import Any


def signal_ids(rubric: dict) -> set[str]:
    return {s["id"] for d in rubric["dimensions"].values() for s in d["signals"]}


def _value(facts: dict, sid: str) -> float:
    """Signals are bool or a fraction in [0,1]; anything missing scores zero."""
    v = facts.get(sid, False)
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return max(0.0, min(1.0, float(v)))
    return 0.0


def score(facts: dict, rubric: dict) -> dict:
    breakdown: dict[str, dict] = {}
    total = 0.0

    for name, dim in rubric["dimensions"].items():
        possible = sum(s["points"] for s in dim["signals"])
        earned = sum(s["points"] * _value(facts, s["id"]) for s in dim["signals"])
        weighted = (earned / possible) * dim["weight"] if possible else 0.0
        breakdown[name] = {
            "weight": dim["weight"],
            "earned": round(earned, 2),
            "possible": possible,
            "weighted": round(weighted, 2),
            "signals": [
                {
                    "id": s["id"],
                    "points": s["points"],
                    "value": round(_value(facts, s["id"]), 2),
                    "rule": s["rule"],
                }
                for s in dim["signals"]
            ],
            "failed": [s["id"] for s in dim["signals"] if _value(facts, s["id"]) < 1.0],
        }
        total += weighted

    capped_by = None
    for gate in rubric.get("gates", []):
        if _value(facts, gate["id"]) < 1.0 and total > gate["cap"]:
            total = float(gate["cap"])
            capped_by = gate["id"]

    final = int(round(total))
    band = next(b["label"] for b in rubric["bands"] if final >= b["min"])
    colour = next(b["colour"] for b in rubric["bands"] if final >= b["min"])
    return {
        "score": final,
        "band": band,
        "colour": colour,
        "capped_by": capped_by,
        "breakdown": breakdown,
        "top_gaps": _top_gaps(breakdown),
    }


def _top_gaps(breakdown: dict) -> list[dict]:
    """The three cheapest wins, so the card can say what to do next."""
    gaps = [
        {"dimension": dim, "signal": s["id"], "points": s["points"], "rule": s["rule"]}
        for dim, d in breakdown.items()
        for s in d["signals"]
        if s["value"] < 1.0
    ]
    return sorted(gaps, key=lambda g: -g["points"])[:3]


LADDER = ["experimental", "prototype", "team-ready", "org-ready", "certified"]


def effective_lifecycle(declared: str, facts: dict, computed_score: int,
                        rubric: dict, latest_eval: dict | None) -> dict:
    """Highest tier at or below `declared` whose gates the evidence supports."""
    if declared in ("deprecated", "retired"):
        return {"declared": declared, "effective": declared, "downgraded": False,
                "reasons": [], "next_tier_gaps": []}

    known = signal_ids(rubric)
    gates = rubric["lifecycle_gates"]
    reasons: list[str] = []
    level = declared

    while level in LADDER:
        missing = _tier_gaps(level, facts, computed_score, rubric, latest_eval, known, gates)
        if not missing:
            break
        reasons.append(f"{level}: " + "; ".join(missing))
        idx = LADDER.index(level)
        if idx == 0:
            break
        level = LADDER[idx - 1]

    next_gaps: list[str] = []
    if level in LADDER and LADDER.index(level) < len(LADDER) - 1:
        nxt = LADDER[LADDER.index(level) + 1]
        next_gaps = _tier_gaps(nxt, facts, computed_score, rubric, latest_eval, known, gates)

    return {
        "declared": declared,
        "effective": level,
        "downgraded": level != declared,
        "reasons": reasons,
        "next_tier": LADDER[LADDER.index(level) + 1] if level in LADDER[:-1] else None,
        "next_tier_gaps": next_gaps,
    }


def _tier_gaps(tier: str, facts: dict, computed_score: int, rubric: dict,
               latest_eval: dict | None, known: set[str], gates: dict) -> list[str]:
    """Everything standing between the evidence and this tier. Recursive on `requires`."""
    gate = gates.get(tier, {})
    missing: list[str] = []

    for req in gate.get("requires", []):
        if req in LADDER:                      # a tier requirement, e.g. org-ready requires team-ready
            missing += _tier_gaps(req, facts, computed_score, rubric, latest_eval, known, gates)
        elif req in known and _value(facts, req) < 1.0:
            missing.append(req)

    if computed_score < gate.get("min_score", 0):
        missing.append(f"score {computed_score} < {gate['min_score']}")

    if (min_pass := gate.get("min_pass_rate")) is not None:
        rate = (latest_eval or {}).get("totals", {}).get("pass_rate")
        if rate is None:
            missing.append("no evaluation report")
        elif rate < min_pass:
            missing.append(f"pass rate {rate:.3f} < {min_pass}")

    if gate.get("human_review") == "required":
        if not (latest_eval or {}).get("human_review", {}).get("verdict") in ("accept", "accept-with-conditions"):
            missing.append("no accepted human review")

    if gate.get("governance_record") and not facts.get("governance_record"):
        missing.append("no certification record in governance/certifications/")

    # Deduplicate while preserving order — recursion can surface the same gap twice.
    seen: set[str] = set()
    return [m for m in missing if not (m in seen or seen.add(m))]


def review_interval_days(tier: str, rubric: dict) -> int:
    return rubric["lifecycle_gates"].get(tier, {}).get("review_interval_days", 180)
