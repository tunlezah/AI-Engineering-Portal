"""Verification: never publish a broken registry.

Two gates:

  verify_snapshot() — the data is internally consistent, and this run is not a
                      catastrophe (a bad token or a renamed group must not
                      silently empty the registry).
  verify_site()     — the built output is offline-safe, linkable and within
                      budget. The external-asset check is what makes the
                      "no CDN" rule real rather than aspirational.
"""

from __future__ import annotations

import json
import pathlib
import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse


@dataclass
class Result:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def report(self) -> str:
        lines = [f"  {k}: {v}" for k, v in self.stats.items()]
        lines += [f"  WARN  {w}" for w in self.warnings]
        lines += [f"  ERROR {e}" for e in self.errors]
        return "\n".join(lines)


def verify_snapshot(snapshot: pathlib.Path, *, base: pathlib.Path | None = None,
                    max_removed_pct: float = 10.0) -> Result:
    snapshot = pathlib.Path(snapshot)
    r = Result()
    cards = [json.loads(p.read_text()) for p in sorted((snapshot / "harnesses").glob("*.json"))]
    graph = json.loads((snapshot / "graph" / "edges.json").read_text())
    catalog = json.loads((snapshot / "catalog.json").read_text())
    report = json.loads((snapshot / "reports" / "index-report.json").read_text())

    ids = [c["spec"]["metadata"]["id"] for c in cards]
    r.stats["harnesses"] = len(cards)
    r.stats["valid"] = report["totals"]["valid"]
    r.stats["catalog_bytes"] = (snapshot / "catalog.json").stat().st_size

    # identity
    if len(set(ids)) != len(ids):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        r.errors.append(f"duplicate harness ids: {dupes}")
    if len(catalog["harnesses"]) != len(cards):
        r.errors.append(
            f"catalog has {len(catalog['harnesses'])} records for {len(cards)} cards")

    # referential integrity
    known = set(ids)
    for c in cards:
        hid = c["spec"]["metadata"]["id"]
        for dep in c["spec"].get("spec", {}).get("dependencies", []):
            if dep["harness"] not in known:
                r.warnings.append(f"{hid} depends on unknown harness {dep['harness']}")
        for p in c["status"]["plugin_resolution"]:
            if p["status"] != "ok":
                r.warnings.append(f"{hid} plugin {p['id']} {p['range']} does not resolve")

    if graph["cycles"]:
        r.errors.append(f"dependency cycles: {graph['cycles']}")

    node_keys = {n["key"] for n in graph["nodes"]}
    for e in graph["edges"]:
        for side in ("from", "to"):
            if e[side] not in node_keys:
                r.errors.append(f"graph edge references unknown node {e[side]}")
                break

    # mass-change guard
    if base and (base_dir := pathlib.Path(base) / "harnesses").is_dir():
        before = {p.stem for p in base_dir.glob("*.json")}
        removed = before - set(ids)
        pct = (len(removed) / len(before) * 100) if before else 0.0
        r.stats["removed_pct"] = round(pct, 1)
        if pct > max_removed_pct:
            r.errors.append(
                f"{len(removed)} of {len(before)} harnesses disappeared ({pct:.1f}%): "
                f"refusing to publish. Likely an expired token or a renamed group. "
                f"Examples: {sorted(removed)[:5]}")
        elif removed:
            r.warnings.append(f"harnesses removed since last snapshot: {sorted(removed)}")

    return r


# Attribute values are matched quoted *and* unquoted: Hugo's --minify strips
# quotes from values that contain no whitespace, so a quotes-only pattern sees
# nothing at all on a production build and the offline gate passes vacuously.
TAG_RE = re.compile(
    r"<(script|link|img|source|iframe|embed|video|audio|object|track|a|area)\b([^>]*)>", re.I)
ATTR_RE = re.compile(
    r"""\b(src|href|poster|data|data-catalog)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'<>`]+))""", re.I)
CSS_URL_RE = re.compile(r"""url\(\s*(?:"([^"]*)"|'([^']*)'|([^)"']*))\)""", re.I)
LOCAL_SCHEMES = ("mailto:", "tel:", "data:", "#", "javascript:", "blob:", "about:")
# Links into GitLab are legitimate: the registry indexes repositories it cannot
# host. They are not fetched at page load, so they do not break offline use.
ALLOWED_EXTERNAL_HOSTS = ("gitlab.acme.internal", "registry.pages.acme.internal")
INLINE_HANDLER_RE = re.compile(r"<[a-z][^>]*?\s(on[a-z]{3,20})\s*=", re.I)
JS_URL_RE = re.compile(r"""(?:src|href)\s*=\s*["']?\s*javascript:""", re.I)

# Whether the browser fetches the URL to render the page is a property of the
# element and attribute, not of the file extension: <a href="…/schema.json"> is
# a link a reader may click, while <link href="…/x.css"> is a hard dependency
# that breaks the page offline. Only the latter may not point off-site.
FETCHED = {
    ("script", "src"), ("link", "href"), ("img", "src"), ("source", "src"),
    ("iframe", "src"), ("embed", "src"), ("video", "src"), ("video", "poster"),
    ("audio", "src"), ("object", "data"), ("track", "src"),
    ("div", "data-catalog"),
}


def _targets(html: str):
    """Yield (url, is_fetched) for every URL the page references.

    is_fetched marks the subset the browser loads on its own; those are the
    ones that make a page fail offline.
    """
    for m in TAG_RE.finditer(html):
        tag = m.group(1).lower()
        for a in ATTR_RE.finditer(m.group(2)):
            attr = a.group(1).lower()
            value = next(g for g in (a.group(2), a.group(3), a.group(4)) if g is not None)
            yield value.strip(), (tag, attr) in FETCHED
    # The browse page fetches its catalogue by XHR from a data attribute.
    for a in re.finditer(r"""\bdata-catalog\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'<>`]+))""", html):
        value = next(g for g in a.groups() if g is not None)
        yield value.strip(), True
    for m in CSS_URL_RE.finditer(html):
        value = next((g for g in m.groups() if g is not None), "")
        yield value.strip().strip("\"'"), True


def _detect_base_path(pages: list[pathlib.Path], public: pathlib.Path) -> str:
    """Infer the URL prefix of a site built for a GitLab Pages *project* subpath.

    A project site is served from https://<group>.gitlab.io/<project>/, so Hugo
    writes every root-absolute URL as /<project>/…, which does not correspond to
    anything in the output tree. A first path segment that is not a real
    top-level entry of public/ is that prefix.
    """
    top = {p.name for p in public.iterdir()}
    counts: dict[str, int] = {}
    for page in pages[:25]:
        html = page.read_text(encoding="utf-8", errors="replace")
        for target, _ in _targets(html):
            if not target.startswith("/") or target.startswith("//"):
                continue
            seg = target.lstrip("/").split("/")[0]
            if seg and seg not in top:
                counts[seg] = counts.get(seg, 0) + 1
    if not counts:
        return ""
    seg, n = max(counts.items(), key=lambda kv: kv[1])
    return seg if n >= 3 else ""


def verify_site(public: pathlib.Path, *, max_page_kb: int = 400,
                max_catalog_mb: float = 6.0, allow_hosts: tuple[str, ...] = ALLOWED_EXTERNAL_HOSTS,
                base_path: str | None = None) -> Result:
    public = pathlib.Path(public)
    r = Result()
    pages = sorted(public.rglob("*.html"))
    r.stats["pages"] = len(pages)
    if not pages:
        r.errors.append("no pages were generated")
        return r

    prefix = (base_path if base_path is not None else _detect_base_path(pages, public)).strip("/")
    if prefix:
        r.stats["base_path"] = f"/{prefix}/"

    known_paths = {p.relative_to(public).as_posix() for p in public.rglob("*") if p.is_file()}
    heavy: list[str] = []
    broken: list[str] = []

    for page in pages:
        rel = page.relative_to(public).as_posix()
        html = page.read_text(encoding="utf-8", errors="replace")
        size_kb = len(html.encode()) / 1024
        if size_kb > max_page_kb:
            heavy.append(f"{rel} ({size_kb:.0f} KB)")

        # Injected-markup gate. README and CHANGELOG text is rendered through
        # Goldmark with unsafe = false, so repository HTML arrives escaped and
        # neither of these can come from content. If one appears, the escaping
        # has been turned off somewhere and the build must not ship.
        for m in INLINE_HANDLER_RE.finditer(html):
            r.errors.append(f"inline event handler in {rel}: {m.group(1)}=…")
        if JS_URL_RE.search(html):
            r.errors.append(f"javascript: URL in {rel}")

        for target, is_fetched in _targets(html):
            if not target or target.startswith(LOCAL_SCHEMES):
                continue
            parsed = urlparse(target)
            if parsed.scheme in ("http", "https") or target.startswith("//"):
                host = parsed.netloc or target.lstrip("/").split("/")[0]
                if is_fetched:
                    r.errors.append(f"external asset reference in {rel}: {target}")
                elif not any(host == h or host.endswith("." + h) for h in allow_hosts):
                    r.warnings.append(f"external link in {rel}: {target}")
                continue
            if parsed.scheme:
                # Any other scheme (ftp:, ws:, file:) is not offline-safe.
                r.errors.append(f"non-local scheme in {rel}: {target}")
                continue
            # internal link: resolve it
            if not _resolves(rel, target, known_paths, prefix):
                broken.append(f"{rel} -> {target}")

    if heavy:
        r.warnings.append(f"{len(heavy)} page(s) over {max_page_kb} KB: {heavy[:5]}")
    if broken:
        r.errors.append(f"{len(broken)} broken internal link(s): {broken[:8]}")

    catalog = public / "catalog.json"
    if not catalog.is_file():
        r.errors.append("catalog.json missing from the built site")
    else:
        mb = catalog.stat().st_size / 1_048_576
        r.stats["catalog_mb"] = round(mb, 2)
        if mb > max_catalog_mb:
            r.errors.append(f"catalog.json is {mb:.1f} MB, over the {max_catalog_mb} MB budget")
        data = json.loads(catalog.read_text())
        r.stats["catalog_records"] = data["count"]
        oversized = [h["i"] for h in data["harnesses"] if len(json.dumps(h)) > 2048]
        if oversized:
            r.warnings.append(f"catalog records over 2 KB: {oversized[:5]}")

    r.stats["total_kb"] = round(
        sum(p.stat().st_size for p in public.rglob("*") if p.is_file()) / 1024)
    return r


def _resolves(page_rel: str, target: str, known: set[str], base_path: str = "") -> bool:
    path = unquote(target.split("#")[0].split("?")[0])
    if not path:
        return True
    if path.startswith("/"):
        candidate = path.lstrip("/")
        if base_path:
            # Root-absolute URLs on a subpath deployment carry the project
            # prefix, which is not part of the output tree.
            if candidate == base_path:
                candidate = ""
            elif candidate.startswith(base_path + "/"):
                candidate = candidate[len(base_path) + 1:]
        if not candidate:
            candidate = "index.html"
    else:
        base = page_rel.rsplit("/", 1)[0] if "/" in page_rel else ""
        candidate = f"{base}/{path}" if base else path
    candidate = _normalise(candidate)
    if candidate in known:
        return True
    for suffix in ("index.html", "/index.html"):
        if _normalise(candidate.rstrip("/") + ("" if suffix.startswith("/") else "/") + suffix.lstrip("/")) in known:
            return True
    return False


def _normalise(path: str) -> str:
    parts: list[str] = []
    for seg in path.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
            continue
        parts.append(seg)
    return "/".join(parts)


def check_accessibility(public: pathlib.Path, sample: int = 40) -> Result:
    """Static accessibility checks we can make without a browser.

    Not a substitute for axe-core in the real pipeline; it catches the
    regressions that actually happen in a template-generated site — missing alt
    text, unlabelled controls, no page language, skipped heading levels.
    """
    public = pathlib.Path(public)
    r = Result()
    pages = sorted(public.rglob("*.html"))[:sample]
    r.stats["sampled"] = len(pages)
    for page in pages:
        rel = page.relative_to(public).as_posix()
        html = page.read_text(encoding="utf-8", errors="replace")
        if not re.search(r"<html[^>]+lang=", html):
            r.errors.append(f"{rel}: <html> has no lang attribute")
        for img in re.findall(r"<img\b[^>]*>", html, re.I):
            if not re.search(r"\balt\s*=", img, re.I):
                r.errors.append(f"{rel}: <img> without alt text")
                break
        for ctrl in re.findall(r"<(?:input|select|textarea)\b[^>]*>", html, re.I):
            if not re.search(r"aria-label|aria-labelledby|\bid\s*=", ctrl, re.I):
                r.errors.append(f"{rel}: form control without a label")
                break
        if re.search(r"<svg\b(?![^>]*(?:aria-hidden|role=))", html, re.I):
            r.warnings.append(f"{rel}: <svg> without role or aria-hidden")
        levels = [int(m) for m in re.findall(r"<h([1-6])\b", html, re.I)]
        for a, b in zip(levels, levels[1:]):
            if b > a + 1:
                r.warnings.append(f"{rel}: heading level jumps h{a} -> h{b}")
                break
        if "skip-link" not in html:
            r.warnings.append(f"{rel}: no skip link")
        # Unquoted form included: --minify drops the quotes, and a quoted-only
        # pattern silently found nothing on exactly the builds that ship.
        ids = [next(g for g in m.groups() if g is not None)
               for m in re.finditer(r"""\sid\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'<>`]+))""", html)]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            # Duplicate ids break in-page navigation and confuse screen readers.
            # They appear when template section ids collide with auto-generated
            # heading ids from rendered Markdown.
            r.errors.append(f"{rel}: duplicate element id(s) {sorted(dupes)[:4]}")
    return r
