"""Where harnesses come from, and how that is configured.

The registry's source and the harnesses it indexes are separate on purpose:
different projects, different groups, in the general case a different GitLab
instance with a different credential. These tests hold that separation in place
— the resolution order for the instance and the token, the exclusion patterns,
and the monorepo case where one project holds many harnesses.

The GitLab tests run against a stub client rather than an instance. That is the
same trick the crawl/index split exists for: `GitLabSource` only ever talks to
`Client`, so replacing it exercises discovery end to end offline.
"""

from __future__ import annotations

import io
import json
import pathlib
import sys
import zipfile

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.registryctl.crawl import (  # noqa: E402
    GitLabSource, _harness_dirs, _relative, _reports_from_zip)
from tools.registryctl.sources import (  # noqa: E402
    ConfigError, LocalSource, ProjectRef, SourceConfig)


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
class TestSourceConfig:
    def test_the_shipped_configuration_parses(self) -> None:
        cfg = SourceConfig.load(ROOT / "sources.yaml")
        assert cfg.groups
        assert cfg.token_env == "REGISTRY_READ_TOKEN"

    def test_a_bare_group_list_still_works(self) -> None:
        """The old groups.yaml shape, so an existing deployment keeps working."""
        cfg = SourceConfig.from_dict({"groups": ["a/b"]})
        assert cfg.groups == ["a/b"]
        assert cfg.projects == []

    def test_a_configuration_that_names_nothing_is_an_error(self) -> None:
        with pytest.raises(ConfigError, match="no harness sources"):
            SourceConfig.from_dict({"gitlab": {"base_url": "https://x"}})

    @pytest.mark.parametrize("entry,expected", [
        ("g/p", ProjectRef("g/p", nested=False, ref=None)),
        ({"path": "g/p"}, ProjectRef("g/p", nested=False, ref=None)),
        ({"path": "g/p", "nested": True, "ref": "trunk"},
         ProjectRef("g/p", nested=True, ref="trunk")),
    ])
    def test_project_entries_take_either_form(self, entry, expected) -> None:
        assert SourceConfig.from_dict({"projects": [entry]}).projects == [expected]

    def test_a_project_entry_without_a_path_is_an_error(self) -> None:
        with pytest.raises(ConfigError, match="no `path`"):
            SourceConfig.from_dict({"projects": [{"nested": True}]})


class TestInstanceResolution:
    """A crawl of the wrong instance finds nothing and publishes an empty
    registry, so every fallback here is deliberate and the last resort is an
    error rather than a guess."""

    def test_the_flag_wins_over_everything(self, monkeypatch) -> None:
        monkeypatch.setenv("CI_SERVER_URL", "https://ci.example")
        cfg = SourceConfig.from_dict(
            {"groups": ["a"], "gitlab": {"base_url": "https://configured.example"}})
        assert cfg.resolve_base_url("https://flag.example") == "https://flag.example"

    def test_the_configuration_wins_over_the_pipeline(self, monkeypatch) -> None:
        monkeypatch.setenv("CI_SERVER_URL", "https://ci.example")
        cfg = SourceConfig.from_dict(
            {"groups": ["a"], "gitlab": {"base_url": "https://harnesses.example/"}})
        assert cfg.resolve_base_url() == "https://harnesses.example"

    def test_a_dedicated_variable_wins_over_the_pipeline(self, monkeypatch) -> None:
        monkeypatch.setenv("CI_SERVER_URL", "https://ci.example")
        monkeypatch.setenv("REGISTRY_GITLAB_URL", "https://harnesses.example")
        cfg = SourceConfig.from_dict({"groups": ["a"]})
        assert cfg.resolve_base_url() == "https://harnesses.example"

    def test_the_pipeline_instance_is_the_last_fallback(self, monkeypatch) -> None:
        monkeypatch.delenv("REGISTRY_GITLAB_URL", raising=False)
        monkeypatch.setenv("CI_SERVER_URL", "https://ci.example")
        assert SourceConfig.from_dict({"groups": ["a"]}).resolve_base_url() == \
            "https://ci.example"

    def test_no_instance_at_all_fails_loudly(self, monkeypatch) -> None:
        monkeypatch.delenv("REGISTRY_GITLAB_URL", raising=False)
        monkeypatch.delenv("CI_SERVER_URL", raising=False)
        with pytest.raises(ConfigError, match="no GitLab URL"):
            SourceConfig.from_dict({"groups": ["a"]}).resolve_base_url()

    def test_the_token_variable_is_named_by_configuration(self, monkeypatch) -> None:
        monkeypatch.setenv("OTHER_INSTANCE_TOKEN", "glpat-xxx")
        cfg = SourceConfig.from_dict(
            {"groups": ["a"], "gitlab": {"token_env": "OTHER_INSTANCE_TOKEN"}})
        assert cfg.resolve_token() == "glpat-xxx"

    def test_a_missing_token_names_the_variable_it_wanted(self, monkeypatch) -> None:
        monkeypatch.delenv("OTHER_INSTANCE_TOKEN", raising=False)
        cfg = SourceConfig.from_dict(
            {"groups": ["a"], "gitlab": {"token_env": "OTHER_INSTANCE_TOKEN"}})
        with pytest.raises(ConfigError, match="OTHER_INSTANCE_TOKEN"):
            cfg.resolve_token()


class TestExclusion:
    @pytest.mark.parametrize("path,excluded", [
        ("ai-platform/templates/harness-template", True),
        ("ai-platform/harnesses/legal/contract-review", False),
        ("ai-platform/harnesses/sandbox/spike", True),          # glob
        ("ai-platform/mono/harnesses/retired-thing", True),     # nested harness path
        ("ai-platform/mono/harnesses/live-thing", False),
    ])
    def test_patterns_match_projects_and_nested_harnesses(self, path, excluded) -> None:
        cfg = SourceConfig.from_dict({"groups": ["a"], "exclude": [
            "ai-platform/templates/*",
            "ai-platform/harnesses/sandbox/*",
            "*/retired-*",
        ]})
        assert cfg.excluded(path) is excluded

    def test_a_local_tree_honours_the_same_patterns(self, tmp_path) -> None:
        for name in ("keep", "drop"):
            d = tmp_path / name
            d.mkdir()
            (d / "harness.yaml").write_text("metadata: {}\n")
        found = {p.project_path.rsplit("/", 1)[-1]
                 for p in LocalSource(tmp_path, exclude=["drop"]).discover()}
        assert found == {"keep"}


# --------------------------------------------------------------------------
# Discovery over a stub GitLab
# --------------------------------------------------------------------------
MANIFEST_TEXT = "apiVersion: harness/v1\nmetadata:\n  id: {id}\n"


class StubClient:
    """Serves a fixed set of projects, trees and blobs. Records every path it
    was asked for, so the tests can assert on request *shape* as well as on the
    records produced — the crawler's cost model is part of its contract."""

    api = "https://harnesses.example/api/v4"

    def __init__(self, projects: list[dict], blobs: dict[tuple[int, str], str]):
        self.projects = {p["path_with_namespace"]: p for p in projects}
        self.blobs = blobs
        self.asked: list[str] = []

    # -- the bits GitLabSource uses ---------------------------------------
    def get(self, path: str, **params):
        self.asked.append(path)
        if path.startswith("/projects/") and path.count("/") == 2:
            return self.projects[_unquote(path.split("/")[-1])]
        if "/repository/files/" in path:
            pid = int(path.split("/")[2])
            name = _unquote(path.split("/repository/files/")[1])
            if (pid, name) not in self.blobs:
                raise _NotFound()
            return {"content": _b64(self.blobs[(pid, name)])}
        if "/repository/commits/" in path:
            return {"id": "c" * 40}
        if path.endswith("/pipelines"):
            return []
        if path.endswith("/issues_statistics"):
            return {"statistics": {"counts": {"opened": 0}}}
        raise AssertionError(f"unexpected GET {path}")

    def paged(self, path: str, **params):
        self.asked.append(path)
        if path.startswith("/groups/"):
            group = _unquote(path.split("/")[2])
            yield from (p for p in self.projects.values()
                        if p["path_with_namespace"].startswith(group + "/"))
        elif path.endswith("/repository/tree"):
            pid = int(path.split("/")[2])
            yield from ({"type": "blob", "path": name}
                        for (p, name) in sorted(self.blobs) if p == pid)
        elif path.endswith("/releases"):
            return
        else:
            raise AssertionError(f"unexpected paged GET {path}")

    def quote(self, s: str) -> str:
        return s.replace("/", "%2F")

    def save_cache(self) -> None:
        pass


class _NotFound(Exception):
    class response:            # what crawl.py inspects to tell 404 from failure
        status_code = 404


def _unquote(s: str) -> str:
    return s.replace("%2F", "/")


def _b64(text: str) -> str:
    import base64
    return base64.b64encode(text.encode()).decode()


def _project(pid: int, path: str) -> dict:
    return {"id": pid, "path_with_namespace": path,
            "web_url": f"https://harnesses.example/{path}",
            "default_branch": "main", "visibility": "internal",
            "last_activity_at": "2026-07-01T00:00:00Z"}


def _source(client: StubClient, **config) -> GitLabSource:
    return GitLabSource(SourceConfig.from_dict(config), client=client)


class TestSeparateProject:
    def test_a_project_named_outright_is_crawled(self) -> None:
        """The separation the whole configuration exists for: the harnesses are
        in a project this repository has nothing to do with."""
        client = StubClient(
            [_project(7, "somebody-else/contract-review")],
            {(7, "harness.yaml"): MANIFEST_TEXT.format(id="contract-review"),
             (7, "README.md"): "# Contract review"})
        found = _source(client, projects=["somebody-else/contract-review"]).discover()
        assert [p.project_path for p in found] == ["somebody-else/contract-review"]
        assert found[0].files["README.md"] == "# Contract review"

    def test_a_project_without_a_manifest_is_not_a_harness(self) -> None:
        client = StubClient([_project(7, "g/not-a-harness")], {(7, "README.md"): "# no"})
        assert _source(client, projects=["g/not-a-harness"]).discover() == []

    def test_a_non_harness_project_costs_one_request(self) -> None:
        """A recursive tree listing per project in a crawled group would dominate
        the run, so the root manifest is probed first and nothing follows it."""
        client = StubClient([_project(7, "g/not-a-harness")], {(7, "README.md"): "# no"})
        _source(client, groups=["g"]).discover()
        assert [a for a in client.asked if "tree" in a] == []

    def test_an_excluded_project_is_never_read(self) -> None:
        client = StubClient(
            [_project(7, "g/harness"), _project(8, "g/template")],
            {(7, "harness.yaml"): MANIFEST_TEXT.format(id="harness"),
             (8, "harness.yaml"): MANIFEST_TEXT.format(id="template")})
        source = _source(client, groups=["g"], exclude=["g/template"])
        assert [p.project_path for p in source.discover()] == ["g/harness"]
        assert source.skipped == 1
        assert not [a for a in client.asked if "/8/" in a]


class TestMonorepo:
    """One project, many harnesses — the shape a team gets when it keeps all its
    harnesses together instead of one project each."""

    @staticmethod
    def _client() -> StubClient:
        return StubClient([_project(9, "platform/ai-harnesses")], {
            (9, "harnesses/triage/harness.yaml"): MANIFEST_TEXT.format(id="triage"),
            (9, "harnesses/triage/README.md"): "# Triage",
            (9, "harnesses/triage/prompts/classify.md"): "classify",
            (9, "harnesses/summarise/harness.yaml"): MANIFEST_TEXT.format(id="summarise"),
            (9, "harnesses/summarise/README.md"): "# Summarise",
            (9, "docs/index.md"): "not a harness",
        })

    def _discover(self, **extra):
        client = self._client()
        source = _source(client, projects=[
            dict({"path": "platform/ai-harnesses", "nested": True}, **extra)])
        return sorted(source.discover(), key=lambda p: p.project_path), source

    def test_every_directory_with_a_manifest_becomes_a_harness(self) -> None:
        found, _ = self._discover()
        assert [p.project_path for p in found] == [
            "platform/ai-harnesses/harnesses/summarise",
            "platform/ai-harnesses/harnesses/triage",
        ]

    def test_files_are_relative_to_the_harness_not_the_project(self) -> None:
        """A nested harness and a whole-project one must produce identical
        records, or every consumer of a Project has to know the difference."""
        found, _ = self._discover()
        triage = found[1]
        assert triage.files["README.md"] == "# Triage"
        assert triage.manifest_text.strip().endswith("id: triage")
        assert "prompts/classify.md" in triage.tree
        assert not any(t.startswith("harnesses/") for t in triage.tree)

    def test_each_harness_links_to_its_own_directory(self) -> None:
        found, _ = self._discover()
        assert found[1].web_url == (
            "https://harnesses.example/platform/ai-harnesses"
            "/-/tree/main/harnesses/triage")

    def test_a_nested_harness_can_be_excluded_by_path(self) -> None:
        client = self._client()
        source = _source(client,
                         projects=[{"path": "platform/ai-harnesses", "nested": True}],
                         exclude=["*/harnesses/summarise"])
        assert [p.project_path for p in source.discover()] == [
            "platform/ai-harnesses/harnesses/triage"]

    def test_a_root_manifest_makes_the_whole_project_one_harness(self) -> None:
        assert _harness_dirs(["harness.yaml", "sub/harness.yaml"]) == [""]

    def test_a_manifest_inside_a_harness_is_not_a_second_harness(self) -> None:
        """Templates and test fixtures carry manifests; LocalSource skips them,
        and a monorepo crawl has to agree or the two sources disagree."""
        assert _harness_dirs(["a/harness.yaml", "a/tests/fixture/harness.yaml",
                              "b/harness.yaml"]) == ["a", "b"]

    def test_the_tree_is_re_rooted(self) -> None:
        assert _relative(["a/x", "a/b/y", "c/z"], "a") == ["x", "b/y"]


class TestSharedArtefacts:
    def test_a_harness_only_takes_its_own_evaluation_reports(self) -> None:
        """One monorepo pipeline produces one artefact holding every harness's
        reports. Attributing all of them to each harness would invent evidence."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("harnesses/triage/evaluations/results/a.json",
                       json.dumps({"run": {"started_at": "2026-01-01"}, "who": "triage"}))
            z.writestr("harnesses/summarise/evaluations/results/a.json",
                       json.dumps({"run": {"started_at": "2026-01-01"}, "who": "summarise"}))
        reports = _reports_from_zip(buf.getvalue(), "harnesses/triage/")
        assert [r["who"] for r in reports] == ["triage"]

    def test_a_single_harness_project_reads_the_root_of_the_artefact(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("evaluations/results/a.json", json.dumps({"who": "root"}))
        assert [r["who"] for r in _reports_from_zip(buf.getvalue())] == ["root"]


class TestShippedConfigMatchesCI:
    def test_the_pipelines_crawl_the_file_that_exists(self) -> None:
        """A renamed config file that CI still refers to by its old name fails
        only at deploy time, in the job that publishes."""
        for ci in (ROOT / ".gitlab-ci.yml", ROOT / "ci/registry-ci.yml"):
            text = ci.read_text()
            assert "groups.yaml" not in text, f"{ci.name} refers to the old filename"
        assert "sources.yaml" in (ROOT / "Makefile").read_text()
        assert yaml.safe_load((ROOT / "sources.yaml").read_text())
