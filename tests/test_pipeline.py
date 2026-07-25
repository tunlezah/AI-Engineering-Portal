"""End-to-end: crawl → index → snapshot → verify, over the fixture harnesses.

This is the test that would have caught every bug found while building the site:
signals that cannot be observed because the crawler does not fetch the file,
cards that silently disappear, catalogue records that drift from cards, and
graph edges pointing at nodes that do not exist.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.registryctl.index import (  # noqa: E402
    Governance, build_catalog, build_graph, find_cycles, index_projects, write_snapshot)
from tools.registryctl.sources import LocalSource  # noqa: E402
from tools.registryctl.validate import ReferenceData, resolve_range, satisfies  # noqa: E402
from tools.registryctl.verify import verify_snapshot  # noqa: E402


@pytest.fixture(scope="module")
def ref() -> ReferenceData:
    return ReferenceData(ROOT / "schema", ROOT / "vendor")


@pytest.fixture(scope="module")
def cards(ref: ReferenceData) -> list[dict]:
    projects = list(LocalSource(ROOT / "examples/harnesses").discover())
    return index_projects(projects, ref, Governance(ROOT / "governance"))


@pytest.fixture(scope="module")
def by_id(cards: list[dict]) -> dict[str, dict]:
    return {c["spec"]["metadata"]["id"]: c for c in cards}


def test_discovery_finds_every_manifest(cards: list[dict]) -> None:
    on_disk = {p.parent.name for p in (ROOT / "examples/harnesses").glob("*/harness.yaml")}
    assert {c["spec"]["metadata"]["id"] for c in cards} == on_disk


def test_a_project_without_a_manifest_is_not_a_harness(tmp_path: pathlib.Path) -> None:
    (tmp_path / "not-a-harness").mkdir()
    (tmp_path / "not-a-harness" / "README.md").write_text("# Nope")
    assert list(LocalSource(tmp_path).discover()) == []


def test_an_invalid_manifest_still_gets_a_card(by_id: dict) -> None:
    """Hiding broken harnesses would make the estate look healthier than it is."""
    card = by_id["meeting-notes"]
    assert card["status"]["valid"] is False
    assert card["status"]["quality"]["score"] == 0
    assert card["status"]["findings"], "an error card must say what is wrong"
    assert card["status"]["lifecycle"]["effective"] == "invalid"


def test_over_claimed_maturity_is_downgraded(by_id: dict) -> None:
    card = by_id["code-review-assistant"]
    assert card["spec"]["spec"]["lifecycle"] == "certified"
    assert card["status"]["lifecycle"]["effective"] != "certified"
    assert card["status"]["lifecycle"]["downgraded"] is True
    assert card["status"]["lifecycle"]["reasons"]


def test_evidence_backed_certification_survives(by_id: dict) -> None:
    card = by_id["contract-review"]
    assert card["status"]["lifecycle"]["effective"] == "certified"
    assert card["status"]["approval"]["certified_at"] == "2026-07-14"
    assert card["status"]["quality"]["score"] >= 90


def test_consumers_are_computed_not_declared(by_id: dict) -> None:
    """The reverse edge comes from skill manifests; a harness cannot inflate it."""
    card = by_id["contract-review"]
    assert card["status"]["consumer_count"] == 3
    assert {c["id"] for c in card["status"]["consumers"]} == {
        "contract-triage", "supplier-onboarding-review", "nda-fast-track"}
    # And it is not simply echoing the manifest's advisory list.
    assert card["spec"]["spec"]["example_skills"] != card["status"]["consumers"]


def test_plugin_ranges_are_resolved_against_the_marketplace(by_id: dict) -> None:
    resolved = {p["id"]: p for p in by_id["contract-review"]["status"]["plugin_resolution"]}
    assert resolved["document-parser"]["resolved"] == "3.4.1"
    assert resolved["document-parser"]["trust_tier"] == "core"
    unresolved = {p["id"]: p for p in by_id["sql-analyst"]["status"]["plugin_resolution"]}
    assert unresolved["semantic-layer"]["status"] == "unresolved"


def test_deprecation_is_surfaced(by_id: dict) -> None:
    card = by_id["policy-qa"]
    assert card["status"]["lifecycle"]["effective"] == "deprecated"
    assert card["spec"]["spec"]["deprecation"]["replaced_by"]


def test_evaluation_trend_is_chronological(by_id: dict) -> None:
    trend = by_id["ticket-triage"]["status"]["evaluation_trend"]
    assert len(trend) > 1
    assert [p["run"] for p in trend] == sorted(p["run"] for p in trend)


def test_overdue_review_is_flagged(by_id: dict) -> None:
    assert by_id["code-review-assistant"]["status"]["approval"]["overdue"] is True


def test_waivers_reach_the_card(by_id: dict) -> None:
    waivers = by_id["code-review-assistant"]["status"]["approval"]["waivers"]
    assert waivers and waivers[0]["metric"] == "p95_latency_ms"
    assert waivers[0]["expires"], "a waiver without an expiry is a permanent exception"


# --------------------------------------------------------------------------
# graph and catalogue
# --------------------------------------------------------------------------
def test_graph_edges_reference_known_nodes(cards: list[dict]) -> None:
    graph = build_graph(cards)
    keys = {n["key"] for n in graph["nodes"]}
    for e in graph["edges"]:
        assert e["from"] in keys and e["to"] in keys


def test_fixture_estate_is_acyclic(cards: list[dict]) -> None:
    assert build_graph(cards)["cycles"] == []


def test_dependency_cycles_are_detected() -> None:
    edges = [{"from": "harness:a", "to": "harness:b", "type": "composes"},
             {"from": "harness:b", "to": "harness:c", "type": "composes"},
             {"from": "harness:c", "to": "harness:a", "type": "composes"}]
    cycles = find_cycles(edges)
    assert cycles and len(cycles[0]) == 4


def test_catalog_has_one_record_per_card_and_stays_small(cards: list[dict]) -> None:
    catalog = build_catalog(cards)
    assert catalog["count"] == len(cards)
    assert {h["i"] for h in catalog["harnesses"]} == {c["spec"]["metadata"]["id"] for c in cards}
    per_record = len(json.dumps(catalog)) / max(catalog["count"], 1)
    # The budget that makes client-side faceting viable at 5,000 harnesses.
    assert per_record < 2048, f"{per_record:.0f} bytes per record"


def test_catalog_carries_no_prose(cards: list[dict]) -> None:
    """Full text belongs in the chunked full-text index, not the facet payload."""
    catalog = build_catalog(cards)
    for h in catalog["harnesses"]:
        assert "readme" not in h and len(h.get("s", "")) <= 200


# --------------------------------------------------------------------------
# snapshot integrity
# --------------------------------------------------------------------------
def test_snapshot_round_trips_and_verifies(cards, ref, tmp_path: pathlib.Path) -> None:
    report = write_snapshot(cards, tmp_path, ref)
    assert report["totals"]["harnesses"] == len(cards)
    result = verify_snapshot(tmp_path)
    assert result.ok, result.errors
    lock = yaml.safe_load((tmp_path / "registry.lock.yaml").read_text())
    assert set(lock["harnesses"]) == {c["spec"]["metadata"]["id"] for c in cards}
    for entry in lock["harnesses"].values():
        assert entry["digest"].startswith("sha256:")


def test_snapshot_is_deterministic(cards, ref, tmp_path: pathlib.Path) -> None:
    """Byte-identical output for identical input, or the snapshot branch churns."""
    a, b = tmp_path / "a", tmp_path / "b"
    write_snapshot(cards, a, ref)
    write_snapshot(cards, b, ref)
    for name in ["registry.lock.yaml", "graph/edges.json"]:
        assert (a / name).read_text() == (b / name).read_text()


def test_mass_disappearance_blocks_publication(cards, ref, tmp_path: pathlib.Path) -> None:
    """An expired token must not silently empty the registry."""
    base, head = tmp_path / "base", tmp_path / "head"
    write_snapshot(cards, base, ref)
    write_snapshot(cards[:2], head, ref)
    result = verify_snapshot(head, base=base, max_removed_pct=10)
    assert not result.ok
    assert "refusing to publish" in result.errors[0]


def test_small_removals_warn_but_publish(cards, ref, tmp_path: pathlib.Path) -> None:
    base, head = tmp_path / "base2", tmp_path / "head2"
    write_snapshot(cards, base, ref)
    write_snapshot(cards[:-1], head, ref)
    result = verify_snapshot(head, base=base, max_removed_pct=50)
    assert result.ok and result.warnings


# --------------------------------------------------------------------------
# semver range resolution
# --------------------------------------------------------------------------
@pytest.mark.parametrize("version,spec,expected", [
    ("3.4.1", "^3.2.0", True), ("4.0.0", "^3.2.0", False), ("3.1.0", "^3.2.0", False),
    ("0.9.2", "^0.9.0", True), ("0.10.0", "^0.9.0", False),
    ("1.2.3", "~1.2.0", True), ("1.3.0", "~1.2.0", False),
    ("2.0.0", ">=1.5.0", True), ("1.4.0", ">=1.5.0", False),
    ("1.0.0", "1.0.0", True), ("1.0.1", "1.0.0", False),
    ("7.7.7", "*", True),
])
def test_semver_satisfaction(version: str, spec: str, expected: bool) -> None:
    assert satisfies(version, spec) is expected


def test_resolution_picks_the_highest_match() -> None:
    assert resolve_range("^3.0.0", ["3.0.0", "3.4.1", "3.2.0", "4.0.0"]) == "3.4.1"
    assert resolve_range("^9.0.0", ["1.0.0"]) is None
