"""registryctl — the registry's command line.

    registryctl validate <dir>          validate one harness (what a contributor runs)
    registryctl crawl                   discover harnesses (local dir or GitLab groups)
    registryctl index                   crawl output -> snapshot
    registryctl emit-content            snapshot -> Hugo content and data
    registryctl verify-snapshot         integrity + mass-change guard
    registryctl verify-site             offline-safety, links, budgets, a11y
    registryctl build                   crawl + index + emit, in one step (dev loop)

Exit codes: 0 ok, 1 errors found, 2 usage error. Warnings never fail a command
unless --strict is passed — the registry must degrade, not stop, when one
harness is imperfect.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import yaml

from . import content as content_mod
from . import index as index_mod
from .index import Governance, write_snapshot
from .sources import LocalSource, SnapshotSource, read_local_project, write_crawl
from .validate import ReferenceData, validate_manifest
from .verify import check_accessibility, verify_site, verify_snapshot

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULTS = {
    "schema": ROOT / "schema",
    "vendor": ROOT / "vendor",
    "governance": ROOT / "governance",
    "harnesses": ROOT / "examples/harnesses",
    "crawl": ROOT / "build/crawl",
    "snapshot": ROOT / "build/snapshot",
    "site": ROOT / "site",
    "public": ROOT / "site/public",
}


def _ref(args: argparse.Namespace) -> ReferenceData:
    return ReferenceData(pathlib.Path(args.schema), pathlib.Path(args.vendor))


def _taxonomy(args: argparse.Namespace) -> dict:
    return yaml.safe_load((pathlib.Path(args.schema) / "taxonomy.yaml").read_text())


# --------------------------------------------------------------------------
def cmd_validate(args: argparse.Namespace) -> int:
    ref = _ref(args)
    targets = [pathlib.Path(args.path)]
    if not (targets[0] / "harness.yaml").is_file():
        targets = sorted(p.parent for p in pathlib.Path(args.path).glob("*/harness.yaml"))
    if not targets:
        print(f"no harness.yaml found under {args.path}", file=sys.stderr)
        return 2

    worst = 0
    for t in targets:
        project = read_local_project(t)  # same record shape a crawl would produce
        manifest = yaml.safe_load(project.manifest_text)
        findings = validate_manifest(manifest, ref, project=project)
        errors = [f for f in findings if f.severity == "error"]
        warns = [f for f in findings if f.severity == "warning"]
        name = manifest.get("metadata", {}).get("id", t.name) if isinstance(manifest, dict) else t.name
        status = "FAIL" if errors else ("WARN" if warns else "OK")
        print(f"{status:4} {name}")
        for f in findings:
            if f.severity == "info" and not args.verbose:
                continue
            print(f"     {f}")
        worst = max(worst, 1 if errors or (args.strict and warns) else 0)
    return worst


def cmd_crawl(args: argparse.Namespace) -> int:
    out = pathlib.Path(args.out)
    if args.groups:
        from .crawl import GitLabSource

        groups = yaml.safe_load(pathlib.Path(args.groups).read_text())["groups"]
        source = GitLabSource(groups, etag_cache=pathlib.Path(args.etag_cache)
                              if args.etag_cache else None)
        projects = ([source.discover_one(args.project)] if args.project else source.discover())
        projects = [p for p in projects if p]
        n = write_crawl(projects, out)
        print(f"crawled {n} harness(es) from {source.scanned} project(s), "
              f"{source.errors} error(s)")
        if not source.healthy():
            print("crawl error rate above 2% — refusing to publish", file=sys.stderr)
            return 1
    else:
        projects = list(LocalSource(pathlib.Path(args.harnesses)).discover())
        n = write_crawl(projects, out)
        print(f"crawled {n} harness(es) from {args.harnesses}")
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    ref = _ref(args)
    gov = Governance(pathlib.Path(args.governance))
    projects = list(SnapshotSource(pathlib.Path(args.crawl)).discover())
    if not projects:
        print(f"no crawl output in {args.crawl}", file=sys.stderr)
        return 1
    cards = index_mod.index_projects(projects, ref, gov)
    report = write_snapshot(cards, pathlib.Path(args.out), ref)
    t = report["totals"]
    print(f"indexed {t['harnesses']} harness(es): {t['valid']} valid, {t['invalid']} invalid, "
          f"{t['downgraded']} downgraded, {t['overdue_reviews']} overdue review(s)")
    for d in report["downgraded"]:
        print(f"  downgrade {d['id']}: {d['declared']} -> {d['effective']} ({d['reasons'][0]})")
    for i in report["invalid"]:
        print(f"  INVALID {i['id']}: {i['findings'][0]['code']} {i['findings'][0]['message'][:80]}")
    return 1 if (args.strict and t["invalid"]) else 0


def cmd_emit(args: argparse.Namespace) -> int:
    stats = content_mod.emit(pathlib.Path(args.snapshot), pathlib.Path(args.site), _taxonomy(args))
    print(f"emitted {stats['totals']['harnesses']} page(s) into {args.site}/content/harnesses")
    return 0


def cmd_verify_snapshot(args: argparse.Namespace) -> int:
    r = verify_snapshot(pathlib.Path(args.snapshot),
                        base=pathlib.Path(args.base) if args.base else None,
                        max_removed_pct=args.max_removed_pct)
    print("snapshot verification:")
    print(r.report())
    return 0 if r.ok and not (args.strict and r.warnings) else 1


def cmd_verify_site(args: argparse.Namespace) -> int:
    site = verify_site(pathlib.Path(args.public), max_page_kb=args.max_page_kb,
                       max_catalog_mb=args.max_catalog_mb, base_path=args.base_path)
    a11y = check_accessibility(pathlib.Path(args.public), sample=args.a11y_sample)
    print("site verification:")
    print(site.report())
    print("accessibility:")
    print(a11y.report())
    ok = site.ok and a11y.ok
    if args.strict:
        ok = ok and not site.warnings and not a11y.warnings
    return 0 if ok else 1


def cmd_build(args: argparse.Namespace) -> int:
    """The development loop: crawl -> index -> emit, from local fixtures."""
    import copy

    crawl_args = copy.copy(args)
    crawl_args.out = args.crawl
    if rc := cmd_crawl(crawl_args):
        return rc

    index_args = copy.copy(args)
    index_args.out = args.snapshot
    if rc := cmd_index(index_args):
        return rc

    return cmd_emit(args)


def cmd_report(args: argparse.Namespace) -> int:
    report = json.loads((pathlib.Path(args.snapshot) / "reports" / "index-report.json").read_text())
    print(json.dumps(report if args.full else report["totals"], indent=2))
    return 0


# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="registryctl", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--schema", default=str(DEFAULTS["schema"]))
    p.add_argument("--vendor", default=str(DEFAULTS["vendor"]))
    p.add_argument("--strict", action="store_true", help="treat warnings as failures")
    sub = p.add_subparsers(dest="command", required=True)

    v = sub.add_parser("validate", help="validate one harness directory (or a tree of them)")
    v.add_argument("path")
    v.add_argument("--verbose", action="store_true")
    v.set_defaults(func=cmd_validate)

    c = sub.add_parser("crawl", help="discover harnesses")
    c.add_argument("--harnesses", default=str(DEFAULTS["harnesses"]),
                   help="local directory tree (development / CI fixtures)")
    c.add_argument("--groups", help="groups.yaml — use the GitLab API instead of a directory")
    c.add_argument("--project", help="incremental: crawl a single project path")
    c.add_argument("--etag-cache", default=".cache/etags.json")
    c.add_argument("--out", default=str(DEFAULTS["crawl"]))
    c.set_defaults(func=cmd_crawl)

    i = sub.add_parser("index", help="crawl output -> snapshot")
    i.add_argument("--crawl", default=str(DEFAULTS["crawl"]))
    i.add_argument("--governance", default=str(DEFAULTS["governance"]))
    i.add_argument("--out", default=str(DEFAULTS["snapshot"]))
    i.set_defaults(func=cmd_index)

    e = sub.add_parser("emit-content", help="snapshot -> Hugo content and data")
    e.add_argument("--snapshot", default=str(DEFAULTS["snapshot"]))
    e.add_argument("--site", default=str(DEFAULTS["site"]))
    e.set_defaults(func=cmd_emit)

    vs = sub.add_parser("verify-snapshot", help="integrity and mass-change guard")
    vs.add_argument("--snapshot", default=str(DEFAULTS["snapshot"]))
    vs.add_argument("--base", help="previous snapshot to diff against")
    vs.add_argument("--max-removed-pct", type=float, default=10.0)
    vs.set_defaults(func=cmd_verify_snapshot)

    vt = sub.add_parser("verify-site", help="offline-safety, links, budgets, accessibility")
    vt.add_argument("--public", default=str(DEFAULTS["public"]))
    vt.add_argument("--max-page-kb", type=int, default=400)
    vt.add_argument("--max-catalog-mb", type=float, default=6.0)
    vt.add_argument("--a11y-sample", type=int, default=40)
    vt.add_argument("--base-path", default=None,
                    help="URL prefix the site is served under (GitLab Pages project "
                         "sites live at /<project>/). Auto-detected when omitted; "
                         "pass '' to force root.")
    vt.set_defaults(func=cmd_verify_site)

    b = sub.add_parser("build", help="crawl + index + emit (development loop)")
    b.add_argument("--harnesses", default=str(DEFAULTS["harnesses"]))
    b.add_argument("--groups")
    b.add_argument("--project")
    b.add_argument("--etag-cache", default=".cache/etags.json")
    b.add_argument("--out", default=str(DEFAULTS["crawl"]))
    b.add_argument("--crawl", default=str(DEFAULTS["crawl"]))
    b.add_argument("--governance", default=str(DEFAULTS["governance"]))
    b.add_argument("--snapshot", default=str(DEFAULTS["snapshot"]))
    b.add_argument("--site", default=str(DEFAULTS["site"]))
    b.set_defaults(func=cmd_build)

    r = sub.add_parser("report", help="print the index report")
    r.add_argument("--snapshot", default=str(DEFAULTS["snapshot"]))
    r.add_argument("--full", action="store_true")
    r.set_defaults(func=cmd_report)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
