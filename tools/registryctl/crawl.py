"""Discovery: find harnesses by walking GitLab groups.

Reference implementation of `registryctl crawl`. Dependencies: python 3.11 stdlib
plus `requests` (vendored in the toolkit image). No internet access required —
GitLab is on the enterprise network.

Design rules enforced here:
  * The crawler is the source of truth. Push notifications only decide *when*
    we run, never *what* we believe.
  * Every read is conditional (ETag) so an hourly full crawl over thousands of
    projects costs a few thousand 304s rather than a few thousand blob reads.
  * A project is a harness if, and only if, `harness.yaml` exists at the root of
    its default branch. There is no registration list to fall out of date.
  * A harness is publishable only if its latest default-branch pipeline ran the
    shared validation job successfully. Producers cannot opt out of the gate by
    deleting jobs from their own .gitlab-ci.yml.
"""

from __future__ import annotations

import base64
import concurrent.futures as futures
import dataclasses
import json
import os
import pathlib
import sys
from typing import Any, Iterator

import requests
import yaml

GITLAB = os.environ.get("CI_SERVER_URL", "https://gitlab.acme.internal")
TOKEN = os.environ["REGISTRY_READ_TOKEN"]  # group-level read_api token, rotated quarterly
API = f"{GITLAB}/api/v4"
REQUIRED_JOB = "harness:validate"
MANIFEST = "harness.yaml"

# Files copied verbatim into the crawl output. Everything else stays in the
# harness repo and is linked to — the registry indexes, it does not mirror.
FETCH_FILES = [
    MANIFEST,
    "README.md",
    "CHANGELOG.md",
    "harness.lock",
    "architecture/overview.mmd",
]


@dataclasses.dataclass
class Crawled:
    project_path: str
    project_id: int
    default_branch: str
    commit: str
    visibility: str
    web_url: str
    last_activity_at: str
    files: dict[str, str]
    releases: list[dict[str, Any]]
    pipeline: dict[str, Any] | None
    validation_ok: bool
    open_issue_stats: dict[str, Any]
    error: str | None = None


class Client:
    def __init__(self, etag_cache: pathlib.Path):
        self.s = requests.Session()
        self.s.headers["PRIVATE-TOKEN"] = TOKEN
        self.etags: dict[str, list[str]] = (
            json.loads(etag_cache.read_text()) if etag_cache.exists() else {}
        )
        self.cache_path = etag_cache

    def get(self, path: str, **params) -> Any:
        url = f"{API}{path}"
        headers = {}
        cached = self.etags.get(url)
        if cached:
            headers["If-None-Match"] = cached[0]
        r = self.s.get(url, params=params, headers=headers, timeout=30)
        if r.status_code == 304:
            return json.loads(cached[1])
        r.raise_for_status()
        body = r.json()
        if etag := r.headers.get("ETag"):
            self.etags[url] = [etag, json.dumps(body)]
        return body

    def paged(self, path: str, **params) -> Iterator[dict]:
        page = 1
        while True:
            batch = self.get(path, per_page=100, page=page, **params)
            if not batch:
                return
            yield from batch
            if len(batch) < 100:
                return
            page += 1

    def save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self.etags))


def discover_projects(c: Client, groups: list[str]) -> list[dict]:
    """All non-archived projects under the configured groups, deduplicated."""
    seen: dict[int, dict] = {}
    for group in groups:
        gid = requests.utils.quote(group, safe="")
        for p in c.paged(
            f"/groups/{gid}/projects",
            include_subgroups="true",
            archived="false",
            order_by="last_activity_at",
        ):
            seen[p["id"]] = p
    return list(seen.values())


def read_file(c: Client, pid: int, ref: str, path: str) -> str | None:
    enc = requests.utils.quote(path, safe="")
    try:
        blob = c.get(f"/projects/{pid}/repository/files/{enc}", ref=ref)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return None
        raise
    return base64.b64decode(blob["content"]).decode("utf-8", "replace")


def crawl_project(c: Client, p: dict) -> Crawled | None:
    pid, ref = p["id"], p.get("default_branch")
    if not ref:
        return None
    manifest = read_file(c, pid, ref, MANIFEST)
    if manifest is None:
        return None  # not a harness — the only registration mechanism there is

    commit = c.get(f"/projects/{pid}/repository/commits/{ref}")["id"]
    files = {MANIFEST: manifest}
    for f in FETCH_FILES[1:]:
        if (content := read_file(c, pid, ref, f)) is not None:
            files[f] = content

    pipelines = c.get(f"/projects/{pid}/pipelines", ref=ref, per_page=1)
    pipeline = pipelines[0] if pipelines else None
    validation_ok = False
    if pipeline:
        jobs = list(c.paged(f"/projects/{pid}/pipelines/{pipeline['id']}/jobs"))
        validation_ok = any(
            j["name"] == REQUIRED_JOB and j["status"] == "success" for j in jobs
        )
        # Evaluation reports travel as job artefacts; the indexer fetches them
        # lazily for jobs whose name starts with "evaluate:".
        pipeline["eval_jobs"] = [
            {"id": j["id"], "name": j["name"], "status": j["status"]}
            for j in jobs
            if j["name"].startswith("evaluate:")
        ]

    return Crawled(
        project_path=p["path_with_namespace"],
        project_id=pid,
        default_branch=ref,
        commit=commit,
        visibility=p["visibility"],
        web_url=p["web_url"],
        last_activity_at=p["last_activity_at"],
        files=files,
        releases=list(c.paged(f"/projects/{pid}/releases")),
        pipeline=pipeline,
        validation_ok=validation_ok,
        open_issue_stats=c.get(f"/projects/{pid}/issues_statistics").get("statistics", {}),
    )


def main() -> int:
    groups_file = pathlib.Path(sys.argv[sys.argv.index("--groups") + 1])
    out = pathlib.Path(sys.argv[sys.argv.index("--out") + 1])
    cache = pathlib.Path(os.environ.get("ETAG_CACHE", ".cache/etags.json"))
    groups = yaml.safe_load(groups_file.read_text())["groups"]

    c = Client(cache)
    projects = discover_projects(c, groups)
    out.mkdir(parents=True, exist_ok=True)

    results: list[Crawled] = []
    errors = 0
    with futures.ThreadPoolExecutor(max_workers=16) as pool:
        for fut in futures.as_completed({pool.submit(crawl_project, c, p) for p in projects}):
            try:
                if (res := fut.result()) is not None:
                    results.append(res)
            except Exception as exc:  # one broken project must not fail the crawl
                errors += 1
                print(f"crawl error: {exc}", file=sys.stderr)

    for r in results:
        (out / f"{r.project_path.replace('/', '__')}.json").write_text(
            json.dumps(dataclasses.asdict(r), indent=2)
        )
    (out / "_crawl-summary.json").write_text(
        json.dumps(
            {"scanned": len(projects), "harnesses": len(results), "errors": errors}, indent=2
        )
    )
    c.save_cache()

    # A crawl that errored on more than 2% of projects is not trustworthy enough
    # to publish from; fail loudly rather than silently shrinking the registry.
    if projects and errors / len(projects) > 0.02:
        print("crawl error rate above 2% — refusing to publish", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
