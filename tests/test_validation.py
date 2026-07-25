"""Validation rules — schema, references, policy, consistency.

Each test states a rule the registry enforces and shows the manifest that breaks
it. When someone proposes relaxing a rule, this file is the argument.
"""

from __future__ import annotations

import copy
import pathlib
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.registryctl.sources import read_local_project  # noqa: E402
from tools.registryctl.validate import ReferenceData, validate_manifest  # noqa: E402

EXAMPLE = ROOT / "examples/harnesses/contract-review"


@pytest.fixture(scope="module")
def ref() -> ReferenceData:
    return ReferenceData(ROOT / "schema", ROOT / "vendor")


@pytest.fixture()
def manifest() -> dict:
    return yaml.safe_load((EXAMPLE / "harness.yaml").read_text())


def codes(findings, severity=None) -> set[str]:
    return {f.code for f in findings if severity is None or f.severity == severity}


def test_the_worked_example_is_clean(manifest, ref) -> None:
    project = read_local_project(EXAMPLE)
    findings = validate_manifest(manifest, ref, project=project)
    assert codes(findings, "error") == set(), [str(f) for f in findings]


def test_every_fixture_harness_parses_and_is_reported_on(ref) -> None:
    for d in sorted((ROOT / "examples/harnesses").glob("*/harness.yaml")):
        m = yaml.safe_load(d.read_text())
        findings = validate_manifest(m, ref, project=read_local_project(d.parent))
        assert isinstance(findings, list)   # never raises, whatever the input


# ---- schema ---------------------------------------------------------------
def test_unknown_top_level_field_is_rejected(manifest, ref) -> None:
    manifest["spec"]["surprise"] = True
    assert "SCHEMA" in codes(validate_manifest(manifest, ref), "error")


def test_limitations_may_not_be_empty(manifest, ref) -> None:
    """A harness with no stated limitations has not been evaluated honestly."""
    manifest["spec"]["limitations"] = []
    assert "SCHEMA" in codes(validate_manifest(manifest, ref), "error")


def test_owner_must_be_a_group_not_a_person(manifest, ref) -> None:
    manifest["metadata"]["owner"] = "@a.okafor"
    assert "SCHEMA" in codes(validate_manifest(manifest, ref), "error")


def test_id_must_be_url_safe(manifest, ref) -> None:
    manifest["metadata"]["id"] = "Contract Review!"
    assert "SCHEMA" in codes(validate_manifest(manifest, ref), "error")


# ---- references -----------------------------------------------------------
def test_unknown_taxonomy_term_is_rejected(manifest, ref) -> None:
    manifest["spec"]["domains"]["technical"] = ["summarization"]   # US spelling: not a term
    assert "UNKNOWN_TECHNICAL_DOMAIN" in codes(validate_manifest(manifest, ref))


def test_unknown_model_is_rejected(manifest, ref) -> None:
    manifest["spec"]["models"]["supported"] = ["gpt-imaginary"]
    manifest["spec"]["models"]["default"] = "gpt-imaginary"
    assert "UNKNOWN_MODEL" in codes(validate_manifest(manifest, ref))


def test_default_model_must_be_supported(manifest, ref) -> None:
    manifest["spec"]["models"]["default"] = "claude-haiku-4-5"
    assert "DEFAULT_MODEL_NOT_SUPPORTED" in codes(validate_manifest(manifest, ref))


def test_unknown_plugin_is_an_error_but_an_unresolvable_range_is_a_warning(manifest, ref) -> None:
    unknown = copy.deepcopy(manifest)
    unknown["spec"]["plugins"]["required"][0]["id"] = "no-such-plugin"
    assert "UNKNOWN_PLUGIN" in codes(validate_manifest(unknown, ref), "error")

    stale = copy.deepcopy(manifest)
    stale["spec"]["plugins"]["required"][0]["range"] = "^99.0.0"
    findings = validate_manifest(stale, ref)
    assert "UNRESOLVABLE_PLUGIN_RANGE" in codes(findings, "warning")
    assert "UNRESOLVABLE_PLUGIN_RANGE" not in codes(findings, "error")


def test_owner_group_must_have_two_members(manifest, ref) -> None:
    manifest["metadata"]["owner"] = "group:productivity-tools"   # one member in teams.json
    manifest["metadata"]["team"] = "productivity-tools"
    assert "OWNER_GROUP_TOO_SMALL" in codes(validate_manifest(manifest, ref))


# ---- lifecycle-conditional -------------------------------------------------
def test_deprecated_requires_a_deprecation_block(manifest, ref) -> None:
    manifest["spec"]["lifecycle"] = "deprecated"
    assert "DEPRECATION_REQUIRED" in codes(validate_manifest(manifest, ref))


def test_deprecation_block_is_forbidden_when_not_deprecated(manifest, ref) -> None:
    manifest["spec"]["deprecation"] = {
        "since": "2026-01-01", "replaced_by": "x", "removal_after": "2026-12-01"}
    assert "DEPRECATION_FORBIDDEN" in codes(validate_manifest(manifest, ref))


def test_zero_version_cannot_claim_maturity(manifest, ref) -> None:
    manifest["spec"]["version"] = "0.9.0"
    assert "ZEROVER_TOO_MATURE" in codes(validate_manifest(manifest, ref))


# ---- policy ---------------------------------------------------------------
def test_personal_data_requires_a_redaction_guardrail(manifest, ref) -> None:
    manifest["spec"]["guardrails"] = [
        g for g in manifest["spec"]["guardrails"] if g["type"] != "pii-redaction"]
    assert "POL_PII_REDACTION" in codes(validate_manifest(manifest, ref), "error")


def test_external_egress_is_blocked(manifest, ref) -> None:
    manifest["spec"]["security"]["egress"] = ["api.example.com"]
    assert "POL_EXTERNAL_EGRESS" in codes(validate_manifest(manifest, ref), "error")


def test_endpoints_may_not_appear_in_the_manifest(manifest, ref) -> None:
    """Manifests are published on an internal-visible site; they are not a network map."""
    manifest["spec"]["retrieval"]["index"] = "https://vector.internal:8080/legal"
    assert "POL_ENDPOINT_IN_MANIFEST" in codes(validate_manifest(manifest, ref), "error")


# ---- repository consistency ------------------------------------------------
def test_declared_guardrail_must_be_wired_into_a_workflow(manifest, ref) -> None:
    manifest["spec"]["guardrails"].append(
        {"id": "decorative-guardrail", "type": "output-filter", "enforcement": "blocking"})
    findings = validate_manifest(manifest, ref, project=read_local_project(EXAMPLE))
    assert "GUARDRAIL_NOT_WIRED" in codes(findings, "error")


def test_maintainers_must_appear_in_codeowners(manifest, ref) -> None:
    manifest["metadata"]["maintainers"] = ["@someone.else"]
    findings = validate_manifest(manifest, ref, project=read_local_project(EXAMPLE))
    assert "CODEOWNERS_MISMATCH" in codes(findings, "warning")


def test_missing_config_schema_is_caught(manifest, ref) -> None:
    manifest["spec"]["interfaces"]["config_schema"] = "config/nope.json"
    findings = validate_manifest(manifest, ref, project=read_local_project(EXAMPLE))
    assert "CONFIG_SCHEMA_MISSING" in codes(findings, "error")


def test_version_must_match_the_latest_release(manifest, ref) -> None:
    manifest["spec"]["version"] = "9.9.9"
    findings = validate_manifest(manifest, ref, project=read_local_project(EXAMPLE))
    assert "VERSION_TAG_MISMATCH" in codes(findings, "warning")


def test_validation_never_raises_on_rubbish(ref) -> None:
    for rubbish in [None, [], "a string", {"apiVersion": "wrong"}, {}]:
        assert isinstance(validate_manifest(rubbish, ref), list)
