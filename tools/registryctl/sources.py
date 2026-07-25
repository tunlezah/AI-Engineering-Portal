"""Sources of harness projects.

Two implementations of one interface:

  GitLabSource  — production. Walks GitLab groups over the REST API (crawl.py).
  LocalSource   — development, CI fixtures, and offline demos. Reads harness
                  projects from a directory tree.

Both yield the same `Project` record, so the indexer never knows or cares which
one produced it. That is what makes the whole pipeline testable without a GitLab
instance, and it is why the crawl/index split exists at all.

A LocalSource project may carry `.registry-fixture.yaml`, which stands in for the
facts the GitLab API would otherwise supply (pipeline status, releases, activity
dates, issue statistics). Fixture files are development scaffolding; a real
project never has one, and the indexer treats their absence as "unknown", not
"failing".
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
from typing import Any, Iterable

import yaml

MANIFEST = "harness.yaml"

# Files the registry reads. Everything else stays in the harness repo and is
# linked to — the registry indexes, it does not mirror.
FETCH_FILES = [
    MANIFEST,
    "README.md",
    "CHANGELOG.md",
    "CODEOWNERS",
    "harness.lock",
    "docs/quickstart.md",
    "architecture/overview.mmd",
    "architecture/sequence.mmd",
    "config/schema.json",
]

# Globs fetched in addition to FETCH_FILES. Workflow definitions are read
# because the guardrail-wiring check compares them against the manifest — a
# check that silently passes when the file was never fetched is worse than no
# check at all.
FETCH_GLOBS = ["workflows/*.yaml", "workflows/*.yml", "guardrails/*.yaml"]
MAX_GLOB_FILES = 12

# Directories whose presence is itself evidence (see the quality rubric).
PROBE_DIRS = ["prompts", "workflows", "tests", "evaluations", "datasets",
              "guardrails", "docs", "examples", "conversations",
              "architecture/decisions"]


@dataclasses.dataclass
class Project:
    """One candidate harness project, normalised across sources."""

    project_path: str
    web_url: str
    commit: str
    default_branch: str = "main"
    visibility: str = "internal"
    archived: bool = False
    last_activity_at: str = ""
    files: dict[str, str] = dataclasses.field(default_factory=dict)
    tree: list[str] = dataclasses.field(default_factory=list)
    releases: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    pipeline: dict[str, Any] | None = None
    validation_ok: bool = False
    signed_tag: bool = False
    sbom_published: bool = False
    secret_scan_clean: bool = True
    open_issue_stats: dict[str, Any] = dataclasses.field(default_factory=dict)
    median_issue_response_days: float | None = None
    forked_from: str | None = None
    evaluations: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    source_error: str | None = None

    @property
    def manifest_text(self) -> str:
        return self.files[MANIFEST]

    def has(self, path: str) -> bool:
        return path in self.files or any(t.startswith(path) for t in self.tree)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        return cls(**d)


class LocalSource:
    """Discovers harnesses in a directory tree: any dir containing harness.yaml."""

    def __init__(self, root: pathlib.Path, namespace: str = "ai-platform/harnesses"):
        self.root = pathlib.Path(root)
        self.namespace = namespace

    def discover(self) -> Iterable[Project]:
        for manifest in sorted(self.root.glob("**/harness.yaml")):
            # Skip manifests nested inside another harness (e.g. test fixtures).
            if any(p.joinpath(MANIFEST).exists() for p in manifest.parents[1:]
                   if self.root in p.parents or p == self.root):
                continue
            yield self._read(manifest.parent)

    def _read(self, d: pathlib.Path) -> Project:
        rel = d.relative_to(self.root).as_posix()
        fx = self._fixture(d)
        files: dict[str, str] = {}
        for f in FETCH_FILES:
            p = d / f
            if p.is_file():
                files[f] = p.read_text(encoding="utf-8", errors="replace")

        for pattern in FETCH_GLOBS:
            for p in sorted(d.glob(pattern))[:MAX_GLOB_FILES]:
                files[p.relative_to(d).as_posix()] = p.read_text(
                    encoding="utf-8", errors="replace")

        tree = sorted(
            p.relative_to(d).as_posix()
            for p in d.rglob("*")
            if p.is_file() and ".git" not in p.parts
        )

        return Project(
            project_path=fx.get("project_path", f"{self.namespace}/{rel}"),
            web_url=fx.get("web_url", f"https://gitlab.acme.internal/{self.namespace}/{rel}"),
            commit=fx.get("commit", "0" * 40),
            default_branch=fx.get("default_branch", "main"),
            visibility=fx.get("visibility", "internal"),
            archived=fx.get("archived", False),
            last_activity_at=fx.get("last_activity_at", ""),
            files=files,
            tree=tree,
            releases=fx.get("releases", []),
            pipeline=fx.get("pipeline"),
            validation_ok=fx.get("validation_ok", True),
            signed_tag=fx.get("signed_tag", False),
            sbom_published=fx.get("sbom_published", False),
            secret_scan_clean=fx.get("secret_scan_clean", True),
            open_issue_stats=fx.get("open_issue_stats", {}),
            median_issue_response_days=fx.get("median_issue_response_days"),
            forked_from=fx.get("forked_from"),
            evaluations=self._evaluations(d),
        )

    @staticmethod
    def _fixture(d: pathlib.Path) -> dict:
        p = d / ".registry-fixture.yaml"
        return yaml.safe_load(p.read_text()) if p.is_file() else {}

    @staticmethod
    def _evaluations(d: pathlib.Path) -> list[dict]:
        out = []
        for p in sorted((d / "evaluations" / "results").glob("*.json")):
            try:
                out.append(json.loads(p.read_text()))
            except json.JSONDecodeError:
                continue  # a malformed report degrades one card, never the build
        return sorted(out, key=lambda e: e.get("run", {}).get("started_at", ""))


class SnapshotSource:
    """Reads a previously written crawl directory (crawl → index across jobs)."""

    def __init__(self, crawl_dir: pathlib.Path):
        self.dir = pathlib.Path(crawl_dir)

    def discover(self) -> Iterable[Project]:
        for f in sorted(self.dir.glob("*.json")):
            if f.name.startswith("_"):
                continue
            yield Project.from_dict(json.loads(f.read_text()))


def read_local_project(directory: pathlib.Path,
                       namespace: str = "ai-platform/harnesses") -> Project:
    """Read a single harness directory — used by `registryctl validate`."""
    directory = pathlib.Path(directory)
    return LocalSource(directory.parent, namespace)._read(directory)


def write_crawl(projects: Iterable[Project], out: pathlib.Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in projects:
        (out / f"{p.project_path.replace('/', '__')}.json").write_text(
            json.dumps(p.to_dict(), indent=2, sort_keys=True)
        )
        n += 1
    (out / "_crawl-summary.json").write_text(json.dumps({"harnesses": n}, indent=2))
    return n
