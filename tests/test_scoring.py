"""Scoring and lifecycle gating.

These are the tests that matter most politically: the quality score and the
auto-downgrade are the two mechanisms teams will challenge, so both must be
reproducible from stored inputs and explainable line by line.
"""

from __future__ import annotations

import pathlib
import sys

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tools.registryctl.score import LADDER, effective_lifecycle, score  # noqa: E402

RUBRIC = yaml.safe_load(
    (pathlib.Path(__file__).resolve().parents[1] / "schema/quality-rubric.yaml").read_text())


def all_signals(value=True) -> dict:
    return {s["id"]: value
            for d in RUBRIC["dimensions"].values() for s in d["signals"]} | {
        "governance_record": True}


def test_perfect_evidence_scores_100() -> None:
    result = score(all_signals(True), RUBRIC)
    assert result["score"] == 100
    assert result["band"] == "A"
    assert result["capped_by"] is None
    assert result["top_gaps"] == []


def test_no_evidence_scores_zero() -> None:
    assert score(all_signals(False), RUBRIC)["score"] == 0


def test_weights_sum_to_the_scale() -> None:
    assert sum(d["weight"] for d in RUBRIC["dimensions"].values()) == RUBRIC["scale"]


def test_invalid_schema_caps_the_score_at_zero() -> None:
    """A harness cannot buy its way past a broken manifest with good docs."""
    facts = all_signals(True) | {"schema-valid": False}
    result = score(facts, RUBRIC)
    assert result["score"] == 0
    assert result["capped_by"] == "schema-valid"


def test_leaked_secret_caps_the_score_at_zero() -> None:
    result = score(all_signals(True) | {"secret-scan": False}, RUBRIC)
    assert result["score"] == 0
    assert result["capped_by"] == "secret-scan"


def test_red_pipeline_caps_the_score_below_sixty() -> None:
    result = score(all_signals(True) | {"pipeline-green": False}, RUBRIC)
    assert result["score"] == 59
    assert result["capped_by"] == "pipeline-green"


def test_fractional_signals_are_partially_credited() -> None:
    half = score(all_signals(True) | {"pass-rate": 0.5}, RUBRIC)["score"]
    full = score(all_signals(True), RUBRIC)["score"]
    none = score(all_signals(True) | {"pass-rate": 0.0}, RUBRIC)["score"]
    assert none < half < full


def test_breakdown_names_every_failed_signal() -> None:
    """A score without a remedy is a scold. The breakdown must say what to fix."""
    result = score(all_signals(True) | {"sbom": False, "changelog": False}, RUBRIC)
    assert "sbom" in result["breakdown"]["security"]["failed"]
    assert "changelog" in result["breakdown"]["engineering"]["failed"]
    assert {g["signal"] for g in result["top_gaps"]} == {"sbom", "changelog"}


def test_top_gaps_are_ordered_by_points_available() -> None:
    result = score(all_signals(False), RUBRIC)
    points = [g["points"] for g in result["top_gaps"]]
    assert points == sorted(points, reverse=True)


# --------------------------------------------------------------------------
# lifecycle gating
# --------------------------------------------------------------------------
GOOD_EVAL = {"totals": {"pass_rate": 0.97},
             "human_review": {"verdict": "accept"}}


def test_full_evidence_sustains_a_certified_claim() -> None:
    lc = effective_lifecycle("certified", all_signals(True), 95, RUBRIC, GOOD_EVAL)
    assert lc["effective"] == "certified"
    assert lc["downgraded"] is False


def test_certification_requires_a_governance_record() -> None:
    """The separation-of-duties rule: a team cannot certify itself."""
    facts = all_signals(True) | {"governance_record": False}
    lc = effective_lifecycle("certified", facts, 95, RUBRIC, GOOD_EVAL)
    assert lc["effective"] == "org-ready"
    assert any("certification record" in r for r in lc["reasons"])


def test_low_pass_rate_downgrades_a_certified_claim() -> None:
    lc = effective_lifecycle("certified", all_signals(True), 95, RUBRIC,
                             {"totals": {"pass_rate": 0.80}, "human_review": {"verdict": "accept"}})
    assert lc["effective"] in ("team-ready", "prototype")
    assert any("pass rate" in r for r in lc["reasons"])


def test_missing_evaluation_downgrades_below_team_ready() -> None:
    facts = all_signals(True) | {"golden-suite-exists": False}
    lc = effective_lifecycle("team-ready", facts, 90, RUBRIC, None)
    assert lc["effective"] == "prototype"


def test_the_indexer_never_upgrades() -> None:
    """Promotion is a human act with a record; demotion is a fact."""
    lc = effective_lifecycle("experimental", all_signals(True), 100, RUBRIC, GOOD_EVAL)
    assert lc["effective"] == "experimental"
    assert lc["downgraded"] is False


def test_deprecated_and_retired_are_never_downgraded() -> None:
    for tier in ("deprecated", "retired"):
        lc = effective_lifecycle(tier, all_signals(False), 0, RUBRIC, None)
        assert lc["effective"] == tier


def test_downgrade_explains_the_next_tier_gaps() -> None:
    facts = all_signals(True) | {"sbom": False, "adversarial-suite": False}
    lc = effective_lifecycle("org-ready", facts, 95, RUBRIC, GOOD_EVAL)
    assert lc["effective"] == "team-ready"
    assert set(lc["next_tier_gaps"]) >= {"sbom", "adversarial-suite"}
    assert lc["next_tier"] == "org-ready"


@pytest.mark.parametrize("tier", LADDER)
def test_every_ladder_tier_has_gates_and_a_review_interval(tier: str) -> None:
    gate = RUBRIC["lifecycle_gates"][tier]
    assert gate["requires"], f"{tier} declares no requirements"
    assert gate["review_interval_days"] > 0


def test_gates_reference_only_real_signals() -> None:
    known = {s["id"] for d in RUBRIC["dimensions"].values() for s in d["signals"]}
    for tier, gate in RUBRIC["lifecycle_gates"].items():
        for req in gate.get("requires", []):
            assert req in known or req in LADDER, f"{tier} requires unknown signal {req}"
