"""Deterministic prompt-contract tests. No model calls, runs in < 20 seconds.

These catch the failure class that costs the most in production: a prompt edit
that renders wrongly, blows the token budget, or leaks an unfilled placeholder.
Behavioural quality is the evaluation suite's job, not this file's.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest
import yaml
from harnessctl.prompts import Prompt, render
from harnessctl.tokens import count_tokens

PROMPTS = sorted(pathlib.Path("prompts").glob("*.md"))
FIXTURES = yaml.safe_load(pathlib.Path("tests/fixtures/prompt-vars.yaml").read_text())
MANIFEST = yaml.safe_load(pathlib.Path("harness.yaml").read_text())


@pytest.mark.parametrize("path", PROMPTS, ids=lambda p: p.stem)
def test_frontmatter_is_complete(path: pathlib.Path) -> None:
    p = Prompt.load(path)
    assert p.meta["id"] == path.stem
    assert re.fullmatch(r"\d+\.\d+\.\d+", p.meta["version"])
    assert p.meta["role"] in {"system", "user", "assistant"}
    assert p.meta["models"], "prompt must declare the models it was written for"
    # A prompt may not claim support for a model the harness does not support.
    assert set(p.meta["models"]) <= set(MANIFEST["spec"]["models"]["supported"])


@pytest.mark.parametrize("path", PROMPTS, ids=lambda p: p.stem)
def test_renders_with_every_fixture(path: pathlib.Path) -> None:
    p = Prompt.load(path)
    for name, variables in FIXTURES[p.meta["id"]].items():
        out = render(p, variables)
        assert "{{" not in out and "{%" not in out, f"unrendered template in {name}"
        assert out.strip(), f"empty render for {name}"


@pytest.mark.parametrize("path", PROMPTS, ids=lambda p: p.stem)
def test_declared_variables_match_template(path: pathlib.Path) -> None:
    p = Prompt.load(path)
    used = set(re.findall(r"\{\{\s*([a-z_][a-z0-9_]*)", p.body))
    declared = {v["name"] for v in p.meta.get("variables", [])}
    loop_locals = set(re.findall(r"\{%\s*for\s+([a-z_]+)", p.body))
    assert used - declared - loop_locals == set(), "template uses undeclared variables"
    assert declared - used == set(), "manifest declares unused variables"


@pytest.mark.parametrize("path", PROMPTS, ids=lambda p: p.stem)
def test_token_budget(path: pathlib.Path) -> None:
    p = Prompt.load(path)
    worst = max(
        count_tokens(render(p, v)) for v in FIXTURES[p.meta["id"]].values()
    )
    budget = p.meta["token_budget"]
    assert worst <= budget, f"{worst} tokens exceeds declared budget {budget}"
    # Warn early rather than discovering the cliff in production.
    if worst > budget * 0.9:
        pytest.warns(UserWarning, match="within 10% of token budget")


def test_system_prompt_states_the_non_negotiables() -> None:
    body = Prompt.load(pathlib.Path("prompts/system.md")).body
    for rule in ["No legal advice", "No invented references", "UNMATCHED", "REDACTED"]:
        assert rule in body, f"system prompt lost the '{rule}' rule"


def test_response_schemas_are_valid_and_referenced() -> None:
    workflow = yaml.safe_load(pathlib.Path("workflows/review.yaml").read_text())
    referenced = {
        s["with"]["response_schema"]
        for s in workflow["steps"]
        if s.get("uses") == "model" and "response_schema" in s.get("with", {})
    }
    for ref in referenced:
        schema = json.loads(pathlib.Path(ref).read_text())
        assert schema["$schema"].startswith("https://json-schema.org/")
        assert schema.get("additionalProperties") is False, (
            "response schemas must be closed, or the model will invent fields"
        )


def test_every_prompt_is_covered_by_an_evaluation_suite() -> None:
    suites = pathlib.Path("evaluations/suites").glob("*.yaml")
    covered = {
        Prompt.load(p).meta.get("evaluated_by", "").split("#")[-1]
        for p in PROMPTS
        if Prompt.load(p).meta.get("evaluated_by")
    }
    suite_ids = {yaml.safe_load(s.read_text())["id"] for s in suites}
    uncovered = [p.stem for p in PROMPTS if p.stem not in covered and p.stem != "system"]
    assert not uncovered, f"prompts with no evaluation coverage: {uncovered}"
    assert "golden" in suite_ids
