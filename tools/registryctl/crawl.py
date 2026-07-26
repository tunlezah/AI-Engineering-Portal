"""Discovery: find harnesses by walking GitLab groups and projects.

The harnesses are not in this repository. They live wherever the organisation
puts them — other groups, other projects, in the general case a different GitLab
instance with a different credential — and `sources.yaml` (SourceConfig) is the
only thing that knows where. Nothing here reads the registry's own source.

Design rules enforced here:

  * The crawler is the source of truth. Push notifications only decide *when*
    we run, never *what* we believe.
  * Every read is conditional (ETag), so an hourly full crawl over thousands of
    projects costs a few thousand 304s rather than a few thousand blob reads.
  * A project is a harness if, and only if, `harness.yaml` exists at the root of
    its default branch. There is no registration list to fall out of date. The
    one deliberate extension is a project listed with `nested: true`, where the
    same rule is applied to every directory in the tree — that is how a single
    shared "harnesses" project holding many of them is supported.
  * Whether the latest default-branch pipeline ran the shared validation job
    successfully is recorded per project and feeds scoring. It is deliberately
    *not* a publication gate: a harness that fails validation is still indexed
    and rendered as an error card, because hiding broken harnesses would make
    the estate look healthier than it is. Treat `validation_ok` as evidence,
    not as a security control — it is satisfied by a job of the right name in
    the producer's own pipeline, which the producer controls.
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
import pathlib
import posixpath
import sys
import threading
from typing import Any, Iterator

from .sources import (FETCH_FILES, FETCH_GLOBS, MAX_GLOB_FILES, MANIFEST, Project,
                      ProjectRef, SourceConfig)

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
    """Reads harnesses out of whichever GitLab the source configuration names.

    `config` decides the instance, the credential, the groups, the individually
    named projects and the exclusions; `base_url` and `token` override the
    resolved values for a one-off run. `client` is for tests — inject a stub and
    the whole discovery path runs without a GitLab.
    """

    def __init__(self, config: SourceConfig, *, base_url: str | None = None,
                 token: str | None = None, etag_cache: pathlib.Path | None = None,
                 concurrency: int | None = None, client: Client | None = None):
        self.config = config
        self.concurrency = concurrency or config.concurrency
        self.errors = 0
        self.scanned = 0
        self.skipped = 0
        # Harnesses inside a monorepo are excluded on the worker thread that
        # read the project, so the counter they bump needs a lock.
        self._lock = threading.Lock()
        self.client = client or Client(
            config.resolve_base_url(base_url),
            token or config.resolve_token(),
            etag_cache,
        )

    # -- discovery ---------------------------------------------------------
    def discover(self) -> list[Project]:
        targets = self._targets()
        self.scanned = len(targets)
        out: list[Project] = []
        with futures.ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            jobs = {pool.submit(self._read, p, ref) for p, ref in targets}
            for fut in futures.as_completed(jobs):
                try:
                    out += fut.result()
                except Exception as exc:            # one project must not fail the crawl
                    self.errors += 1
                    print(f"crawl error: {exc}", file=sys.stderr)
        self.client.save_cache()
        return out

    def discover_one(self, project_path: str) -> list[Project]:
        """Incremental path: re-read a single project after its pipeline fired.

        The project may be a monorepo, in which case one pipeline covers several
        harnesses and re-reading it re-reads all of them.
        """
        self.scanned = 1
        if self.config.excluded(project_path):
            self.skipped = 1
            return []
        p = self.client.get(f"/projects/{self.client.quote(project_path)}")
        return self._read(p, self._named.get(project_path, ProjectRef(project_path)))

    def healthy(self) -> bool:
        return not self.scanned or (self.errors / self.scanned) <= MAX_ERROR_RATE

    # -- internals ---------------------------------------------------------
    @property
    def _named(self) -> dict[str, ProjectRef]:
        return {r.path: r for r in self.config.projects}

    def _targets(self) -> list[tuple[dict, ProjectRef]]:
        """Every project to read, paired with how it was configured.

        Group membership and the explicit project list are merged on project id,
        so naming a project that also happens to sit in a crawled group reads it
        once — with the explicit entry's settings, because that is the one
        somebody wrote down on purpose.
        """
        seen: dict[int, tuple[dict, ProjectRef]] = {}
        for group in self.config.groups:
            gid = self.client.quote(group)
            for p in self.client.paged(f"/groups/{gid}/projects", include_subgroups="true",
                                       archived="false", order_by="last_activity_at"):
                seen[p["id"]] = (p, ProjectRef(p["path_with_namespace"]))

        for ref in self.config.projects:
            p = self.client.get(f"/projects/{self.client.quote(ref.path)}")
            seen[p["id"]] = (p, ref)

        kept = []
        for p, ref in seen.values():
            if self.config.excluded(p["path_with_namespace"]):
                self.skipped += 1
                continue
            kept.append((p, ref))
        return kept

    def _file(self, pid: int, ref: str, path: str) -> str | None:
        try:
            blob = self.client.get(
                f"/projects/{pid}/repository/files/{self.client.quote(path)}", ref=ref)
        except Exception as e:  # 404 is the common, expected case
            if getattr(getattr(e, "response", None), "status_code", None) == 404:
                return None
            raise
        return base64.b64decode(blob["content"]).decode("utf-8", "replace")

    def _read(self, p: dict, cfg: ProjectRef) -> list[Project]:
        """Read one GitLab project into zero, one or many harnesses.

        Many only in the `nested: true` case: a monorepo's pipeline, releases and
        issue statistics are project-wide, so every harness in it shares them.
        That is honest — those signals really are shared — but it is why a
        monorepo scores as one engineering unit and separate projects do not.
        """
        pid = p["id"]
        ref = cfg.ref or p.get("default_branch")
        if not ref:
            return []

        manifest: str | None = None
        if cfg.nested:
            tree = self._tree(pid, ref)
            subdirs = _harness_dirs(tree)
        else:
            # The cheap path, and the one every group-discovered project takes:
            # one probe for the root manifest, and nothing else if it is absent.
            # Most projects in a crawled group are not harnesses, and a recursive
            # tree listing for each of them to learn that would dominate the run.
            if (manifest := self._file(pid, ref, MANIFEST)) is None:
                return []
            tree = self._tree(pid, ref)
            subdirs = [""]
        if not subdirs:
            return []

        pipelines = self.client.get(f"/projects/{pid}/pipelines", ref=ref, per_page=1)
        pipeline = pipelines[0] if pipelines else None
        validation_ok = False
        jobs: list[dict] = []
        if pipeline:
            jobs = list(self.client.paged(f"/projects/{pid}/pipelines/{pipeline['id']}/jobs"))
            validation_ok = any(
                j["name"] == REQUIRED_JOB and j["status"] == "success" for j in jobs)

        releases = list(self.client.paged(f"/projects/{pid}/releases"))
        shared = dict(
            default_branch=ref,
            visibility=p["visibility"],
            archived=p.get("archived", False),
            last_activity_at=p["last_activity_at"],
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
            commit=self.client.get(f"/projects/{pid}/repository/commits/{ref}")["id"],
        )

        out: list[Project] = []
        for subdir in subdirs:
            path = posixpath.join(p["path_with_namespace"], subdir) if subdir \
                else p["path_with_namespace"]
            if self.config.excluded(path):
                with self._lock:
                    self.skipped += 1
                continue
            local = _relative(tree, subdir)
            out.append(Project(
                project_path=path,
                web_url=f"{p['web_url']}/-/tree/{ref}/{subdir}" if subdir else p["web_url"],
                files=self._files(pid, ref, subdir, local,
                                  seed={MANIFEST: manifest} if manifest else None),
                tree=local,
                evaluations=self._evaluations(pid, jobs, subdir),
                **shared,
            ))
        return out

    def _tree(self, pid: int, ref: str) -> list[str]:
        return [t["path"] for t in self.client.paged(
            f"/projects/{pid}/repository/tree", ref=ref, recursive="true")
            if t["type"] == "blob"]

    def _files(self, pid: int, ref: str, subdir: str, local: list[str],
               seed: dict[str, str] | None = None) -> dict[str, str]:
        """Fetch the readable files for one harness.

        Keys are relative to the harness directory, so a harness in a monorepo
        and one that owns its whole project produce identical records and the
        indexer cannot tell them apart. `local` is the tree already relative to
        the harness, so a file that does not exist is never requested.
        """
        files: dict[str, str] = dict(seed or {})
        wanted = [f for f in FETCH_FILES if f in local and f not in files]
        for pattern in FETCH_GLOBS:
            wanted += [t for t in local if fnmatch.fnmatch(t, pattern)][:MAX_GLOB_FILES]

        for f in wanted:
            full = posixpath.join(subdir, f) if subdir else f
            if (content := self._file(pid, ref, full)) is not None:
                files[f] = content
        return files

    def _tag_signed(self, pid: int, releases: list[dict]) -> bool:
        if not releases:
            return False
        try:
            tag = self.client.get(
                f"/projects/{pid}/repository/tags/{self.client.quote(releases[0]['tag_name'])}")
        except Exception:
            return False
        return bool(tag.get("signature") or (tag.get("commit") or {}).get("signature"))

    def _evaluations(self, pid: int, jobs: list[dict], subdir: str = "") -> list[dict]:
        """Evaluation reports travel as job artefacts from `evaluate:*` jobs.

        In a monorepo one pipeline produces one artefact holding every harness's
        reports, so each harness takes only the reports under its own directory.
        Attributing the whole artefact to all of them would invent evaluation
        evidence, which is the one thing this registry must never do.
        """
        prefix = f"{subdir}/" if subdir else ""
        out: list[dict] = []
        for j in jobs:
            if not j["name"].startswith("evaluate:") or j["status"] != "success":
                continue
            try:
                r = self.client.s.get(
                    f"{self.client.api}/projects/{pid}/jobs/{j['id']}/artifacts", timeout=60)
                r.raise_for_status()
                out += _reports_from_zip(r.content, prefix)
            except Exception as exc:
                print(f"artefact fetch failed for job {j['id']}: {exc}", file=sys.stderr)
        return sorted(out, key=lambda e: e.get("run", {}).get("started_at", ""))


def _relative(tree: list[str], subdir: str) -> list[str]:
    """The subset of a project tree that belongs to one harness, re-rooted."""
    if not subdir:
        return tree
    prefix = f"{subdir}/"
    return [t[len(prefix):] for t in tree if t.startswith(prefix)]


def _harness_dirs(tree: list[str]) -> list[str]:
    """Every directory in a monorepo that is a harness: it holds a harness.yaml.

    A manifest *inside* another harness is that harness's own fixture or
    template, not a second harness — the same rule LocalSource applies to a
    directory tree, so the two sources discover the same thing.
    """
    dirs = sorted({posixpath.dirname(t) for t in tree
                   if posixpath.basename(t) == MANIFEST})
    if "" in dirs:
        return [""]     # a manifest at the root: the project itself is the harness
    return [d for d in dirs if not any(d.startswith(f"{o}/") for o in dirs if o != d)]


# A job artefact is produced by a pipeline in someone else's project, so its
# *decompressed* size is attacker-chosen even when the download is small. These
# caps keep a zip bomb from taking the crawler down with it.
MAX_REPORT_BYTES = 4 * 1024 * 1024
MAX_ARTEFACT_BYTES = 64 * 1024 * 1024


def _reports_from_zip(blob: bytes, prefix: str = "") -> list[dict]:
    import io
    import zipfile

    out: list[dict] = []
    budget = MAX_ARTEFACT_BYTES
    results_dir = f"{prefix}evaluations/results/"
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for info in z.infolist():
            name = info.filename
            if not (name.startswith(results_dir) and name.endswith(".json")):
                continue
            if info.file_size > MAX_REPORT_BYTES:
                print(f"skipping oversized evaluation report {name} "
                      f"({info.file_size} bytes)", file=sys.stderr)
                continue
            budget -= info.file_size
            if budget < 0:
                print("evaluation artefact exceeds the decompression budget; "
                      "ignoring the rest", file=sys.stderr)
                break
            try:
                out.append(json.loads(z.read(name)))
            except json.JSONDecodeError:
                continue
    return out
