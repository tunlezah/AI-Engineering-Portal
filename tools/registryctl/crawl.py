"""Discovery: find harnesses by walking GitLab groups.

Design rules enforced here:

  * The crawler is the source of truth. Push notifications only decide *when*
    we run, never *what* we believe.
  * Every read is conditional (ETag), so an hourly full crawl over thousands of
    projects costs a few thousand 304s rather than a few thousand blob reads.
  * A project is a harness if, and only if, `harness.yaml` exists at the root of
    its default branch. There is no registration list to fall out of date.
  * A harness is publishable only if its latest default-branch pipeline ran the
    shared validation job successfully. Producers cannot opt out of the gate by
    deleting jobs from their own .gitlab-ci.yml.
  * One broken project degrades one card. A crawl that errors on more than 2% of
    projects refuses to publish at all — a bad token must not silently empty the
    registry.

Requires `requests`; GitLab is on the enterprise network, so no internet access
is involved. For development and tests, use LocalSource in sources.py instead:
both yield the same Project record.
"""

from __future__ import annotations

import base64
import concurrent.futures as futures
import fnmatch
import json
import os
import pathlib
import sys
from typing import Any, Iterator

from .sources import FETCH_FILES, FETCH_GLOBS, MAX_GLOB_FILES, MANIFEST, Project

REQUIRED_JOB = "harness:validate"
MAX_ERROR_RATE = 0.02


class Client:
    """Thin GitLab REST client with an on-disk ETag cache."""

    def __init__(self, base_url: str, token: str, etag_cache: pathlib.Path | None = None):
        import requests  # imported lazily so LocalSource users need no dependency

        self.api = f"{base_url.rstrip('/')}/api/v4"
        self.s = requests.Session()
        self.s.headers["PRIVATE-TOKEN"] = token
        self.cache_path = etag_cache
        self.etags: dict[str, list[str]] = (
            json.loads(etag_cache.read_text()) if etag_cache and etag_cache.exists() else {}
        )
        self._requests = requests

    def get(self, path: str, **params: Any) -> Any:
        url = f"{self.api}{path}"
        key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        headers = {}
        if cached := self.etags.get(key):
            headers["If-None-Match"] = cached[0]
        r = self.s.get(url, params=params, headers=headers, timeout=30)
        if r.status_code == 304 and (cached := self.etags.get(key)):
            return json.loads(cached[1])
        r.raise_for_status()
        body = r.json()
        if etag := r.headers.get("ETag"):
            self.etags[key] = [etag, json.dumps(body)]
        return body

    def paged(self, path: str, **params: Any) -> Iterator[dict]:
        page = 1
        while True:
            batch = self.get(path, per_page=100, page=page, **params)
            if not batch:
                return
            yield from batch
            if len(batch) < 100:
                return
            page += 1

    def quote(self, s: str) -> str:
        return self._requests.utils.quote(s, safe="")

    def save_cache(self) -> None:
        if self.cache_path:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self.etags))


class GitLabSource:
    def __init__(self, groups: list[str], *, base_url: str | None = None,
                 token: str | None = None, etag_cache: pathlib.Path | None = None,
                 concurrency: int = 16):
        self.groups = groups
        self.concurrency = concurrency
        self.errors = 0
        self.scanned = 0
        self.client = Client(
            base_url or os.environ.get("CI_SERVER_URL", "https://gitlab.acme.internal"),
            token or os.environ["REGISTRY_READ_TOKEN"],
            etag_cache,
        )

    # -- discovery ---------------------------------------------------------
    def discover(self) -> list[Project]:
        projects = self._projects()
        self.scanned = len(projects)
        out: list[Project] = []
        with futures.ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            for fut in futures.as_completed({pool.submit(self._read, p) for p in projects}):
                try:
                    if (res := fut.result()) is not None:
                        out.append(res)
                except Exception as exc:            # one project must not fail the crawl
                    self.errors += 1
                    print(f"crawl error: {exc}", file=sys.stderr)
        self.client.save_cache()
        return out

    def discover_one(self, project_path: str) -> Project | None:
        """Incremental path: re-read a single project after its pipeline fired."""
        p = self.client.get(f"/projects/{self.client.quote(project_path)}")
        self.scanned = 1
        return self._read(p)

    def healthy(self) -> bool:
        return not self.scanned or (self.errors / self.scanned) <= MAX_ERROR_RATE

    # -- internals ---------------------------------------------------------
    def _projects(self) -> list[dict]:
        seen: dict[int, dict] = {}
        for group in self.groups:
            gid = self.client.quote(group)
            for p in self.client.paged(f"/groups/{gid}/projects", include_subgroups="true",
                                       archived="false", order_by="last_activity_at"):
                seen[p["id"]] = p
        return list(seen.values())

    def _file(self, pid: int, ref: str, path: str) -> str | None:
        try:
            blob = self.client.get(
                f"/projects/{pid}/repository/files/{self.client.quote(path)}", ref=ref)
        except Exception as e:  # 404 is the common, expected case
            if getattr(getattr(e, "response", None), "status_code", None) == 404:
                return None
            raise
        return base64.b64decode(blob["content"]).decode("utf-8", "replace")

    def _read(self, p: dict) -> Project | None:
        pid, ref = p["id"], p.get("default_branch")
        if not ref:
            return None
        manifest = self._file(pid, ref, MANIFEST)
        if manifest is None:
            return None  # not a harness — the only registration mechanism there is

        files = {MANIFEST: manifest}
        for f in FETCH_FILES[1:]:
            if (content := self._file(pid, ref, f)) is not None:
                files[f] = content

        tree = [
            t["path"] for t in self.client.paged(
                f"/projects/{pid}/repository/tree", ref=ref, recursive="true")
            if t["type"] == "blob"
        ]

        for pattern in FETCH_GLOBS:
            for path in [t for t in tree if fnmatch.fnmatch(t, pattern)][:MAX_GLOB_FILES]:
                if (content := self._file(pid, ref, path)) is not None:
                    files[path] = content

        pipelines = self.client.get(f"/projects/{pid}/pipelines", ref=ref, per_page=1)
        pipeline = pipelines[0] if pipelines else None
        validation_ok = False
        evaluations: list[dict] = []
        if pipeline:
            jobs = list(self.client.paged(f"/projects/{pid}/pipelines/{pipeline['id']}/jobs"))
            validation_ok = any(
                j["name"] == REQUIRED_JOB and j["status"] == "success" for j in jobs)
            evaluations = self._evaluations(pid, jobs)

        releases = list(self.client.paged(f"/projects/{pid}/releases"))
        return Project(
            project_path=p["path_with_namespace"],
            web_url=p["web_url"],
            commit=self.client.get(f"/projects/{pid}/repository/commits/{ref}")["id"],
            default_branch=ref,
            visibility=p["visibility"],
            archived=p.get("archived", False),
            last_activity_at=p["last_activity_at"],
            files=files,
            tree=tree,
            releases=[{"tag_name": r["tag_name"], "released_at": r.get("released_at"),
                       "description": r.get("description", "")[:2000]} for r in releases],
            pipeline=pipeline,
            validation_ok=validation_ok,
            signed_tag=self._tag_signed(pid, releases),
            sbom_published=any(
                a.get("name") == "SBOM"
                for r in releases for a in r.get("assets", {}).get("links", [])),
            secret_scan_clean=validation_ok,   # gitleaks is a blocking job in the template
            open_issue_stats=self.client.get(f"/projects/{pid}/issues_statistics"),
            forked_from=(p.get("forked_from_project") or {}).get("path_with_namespace"),
            evaluations=evaluations,
        )

    def _tag_signed(self, pid: int, releases: list[dict]) -> bool:
        if not releases:
            return False
        try:
            tag = self.client.get(
                f"/projects/{pid}/repository/tags/{self.client.quote(releases[0]['tag_name'])}")
        except Exception:
            return False
        return bool(tag.get("signature") or (tag.get("commit") or {}).get("signature"))

    def _evaluations(self, pid: int, jobs: list[dict]) -> list[dict]:
        """Evaluation reports travel as job artefacts from `evaluate:*` jobs."""
        out: list[dict] = []
        for j in jobs:
            if not j["name"].startswith("evaluate:") or j["status"] != "success":
                continue
            try:
                r = self.client.s.get(
                    f"{self.client.api}/projects/{pid}/jobs/{j['id']}/artifacts", timeout=60)
                r.raise_for_status()
                out += _reports_from_zip(r.content)
            except Exception as exc:
                print(f"artefact fetch failed for job {j['id']}: {exc}", file=sys.stderr)
        return sorted(out, key=lambda e: e.get("run", {}).get("started_at", ""))


def _reports_from_zip(blob: bytes) -> list[dict]:
    import io
    import zipfile

    out = []
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for name in z.namelist():
            if name.startswith("evaluations/results/") and name.endswith(".json"):
                try:
                    out.append(json.loads(z.read(name)))
                except json.JSONDecodeError:
                    continue
    return out
