"""Guardrail tests. Every guardrail declared in harness.yaml must have one.

The registry's `harnessctl policy-check` asserts the mapping exists; these tests
assert the guardrails actually work. Both are required: a declared-but-broken
guardrail is worse than an undeclared one, because it buys false confidence.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml
from harnessctl.guardrails import AdviceFilter, GroundingCheck
from harnessctl.workflow import Workflow

MANIFEST = yaml.safe_load(pathlib.Path("harness.yaml").read_text())
DECLARED = {g["id"] for g in MANIFEST["spec"]["guardrails"]}


def test_every_declared_guardrail_is_wired_into_the_workflow() -> None:
    wf = Workflow.load("workflows/review.yaml")
    wired = {s["guardrail"] for s in wf.steps if "guardrail" in s}
    assert DECLARED == wired, f"declared but not wired: {DECLARED - wired}"


def test_no_inference_step_precedes_redaction() -> None:
    wf = Workflow.load("workflows/review.yaml")
    redact_idx = wf.index_of("redact")
    for i, step in enumerate(wf.steps):
        if step.get("uses") == "model":
            assert i > redact_idx, f"step {step['id']} calls a model before redaction"


@pytest.mark.parametrize(
    "text,expected_blocked",
    [
        ("The clause diverges from PB-IP-004; the indemnity is uncapped.", False),
        ("You should push back on this indemnity.", True),
        ("We advise rejecting clause 11.2.", True),
        ("In our legal opinion this clause is unenforceable.", True),
        ("A court would likely find this clause void.", True),
        ("The clause is unusual relative to the playbook position.", False),
        ("Recommend escalation to the responsible lawyer.", False),  # process, not advice
    ],
)
def test_advice_filter(text: str, expected_blocked: bool) -> None:
    f = AdviceFilter.from_rules("guardrails/no-advice.yaml")
    assert f.blocks(text) is expected_blocked


def test_grounding_rejects_paraphrased_evidence() -> None:
    source = "Supplier shall indemnify, defend and hold harmless Customer against any and all claims"
    g = GroundingCheck(source_text=source, retrieved_ids={"PB-IP-004"})
    verbatim = {"evidence": "indemnify, defend and hold harmless Customer", "citations": ["PB-IP-004"]}
    paraphrase = {"evidence": "Supplier must indemnify and defend the Customer", "citations": ["PB-IP-004"]}
    assert g.check(verbatim).ok
    assert not g.check(paraphrase).ok


def test_grounding_rejects_unretrieved_citation() -> None:
    g = GroundingCheck(source_text="text", retrieved_ids={"PB-IP-004"})
    result = g.check({"evidence": "text", "citations": ["PB-IP-004", "PB-DP-007"]})
    assert not result.ok
    assert "PB-DP-007" in result.reason


def test_cost_ceiling_returns_partial_rather_than_failing_open() -> None:
    wf = Workflow.load("workflows/review.yaml")
    assert wf.budget["on_exceeded"] == "return-partial"
    # A partial result must be labelled as such, or a reviewer will read a
    # truncated review as a complete one.
    assert "coverage_report" in wf.outputs


def test_human_signoff_is_terminal_and_blocking() -> None:
    wf = Workflow.load("workflows/review.yaml")
    assert wf.steps[-1]["id"] == "signoff"
    assert wf.steps[-1]["uses"] == "human"
    assert MANIFEST["spec"]["security"]["human_review"] == "required"
