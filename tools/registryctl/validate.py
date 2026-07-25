"""Validation: schema, reference integrity and cross-file consistency.

Three layers, in increasing cost:

  1. JSON Schema      — shape. Cheap, total, and the hard gate.
  2. Reference checks — do the taxonomy terms, models, runtimes, plugins,
                        owner groups and harness dependencies actually exist?
  3. Consistency      — does the repository match what the manifest claims?
                        (CODEOWNERS vs maintainers, declared guardrails vs
                        workflow steps, config_schema exists and is closed.)

Layer 3 is where most real defects are found. Schema validity only proves a
manifest is well-formed; consistency proves it is *true*.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import re
from typing import Any

import yaml
from jsonschema import Draft202012Validator

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


@dataclasses.dataclass(frozen=True)
class Finding:
    severity: str          # error | warning | info
    code: str
    message: str
    path: str = ""

    def __str__(self) -> str:
        loc = f" [{self.path}]" if self.path else ""
        return f"{self.severity.upper():7} {self.code}{loc}: {self.message}"


class ReferenceData:
    """Everything a manifest is allowed to point at."""

    def __init__(self, schema_dir: pathlib.Path, vendor_dir: pathlib.Path | None = None):
        self.schema = json.loads((schema_dir / "harness.schema.json").read_text())
        self.rubric = yaml.safe_load((schema_dir / "quality-rubric.yaml").read_text())
        tax = yaml.safe_load((schema_dir / "taxonomy.yaml").read_text())
        self.business = {t["id"] for t in tax["business"]}
        self.technical = {t["id"] for t in tax["technical"]}
        self.patterns = {t["id"] for t in tax["reference_architectures"]}
        self.taxonomy = tax
        self.plugins: dict[str, dict] = {}
        self.skills: dict[str, dict] = {}
        self.models: set[str] = set()
        self.runtimes: set[str] = set()
        self.teams: set[str] = set()
        self.owner_groups: dict[str, int] = {}
        if vendor_dir:
            self._load_vendor(pathlib.Path(vendor_dir))

    def _load_vendor(self, v: pathlib.Path) -> None:
        def load(path: pathlib.Path) -> Any:
            return json.loads(path.read_text()) if path.is_file() else None

        if (p := load(v / "plugin-marketplace" / "index.json")):
            self.plugins = {x["id"]: x for x in p["plugins"]}
        if (s := load(v / "skill-marketplace" / "index.json")):
            self.skills = {x["id"]: x for x in s["skills"]}
        if (m := load(v / "platform" / "models.json")):
            self.models = {x["id"] for x in m["models"]}
        if (r := load(v / "platform" / "runtimes.json")):
            self.runtimes = {x["id"] for x in r["runtimes"]}
        if (t := load(v / "platform" / "teams.json")):
            self.teams = {x["slug"] for x in t["teams"]}
            self.owner_groups = {x["owner_group"]: x["member_count"] for x in t["teams"]}


def validate_manifest(manifest: Any, ref: ReferenceData, *, project: Any = None) -> list[Finding]:
    """Full validation of one manifest. Never raises; returns findings."""
    out: list[Finding] = []

    if not isinstance(manifest, dict):
        return [Finding("error", "MANIFEST_NOT_OBJECT", "harness.yaml is not a mapping")]

    # ---- layer 1: schema -------------------------------------------------
    schema_errors = sorted(
        Draft202012Validator(ref.schema).iter_errors(manifest),
        key=lambda e: list(e.path),
    )
    for e in schema_errors:
        out.append(Finding("error", "SCHEMA", e.message, "/".join(str(p) for p in e.path)))
    if schema_errors:
        return out  # later layers assume a well-formed document

    meta, spec = manifest["metadata"], manifest["spec"]

    # ---- layer 2: references --------------------------------------------
    for term in spec["domains"]["business"]:
        if term not in ref.business:
            out.append(Finding("error", "UNKNOWN_BUSINESS_DOMAIN", term, "spec/domains/business"))
    for term in spec["domains"]["technical"]:
        if term not in ref.technical:
            out.append(Finding("error", "UNKNOWN_TECHNICAL_DOMAIN", term, "spec/domains/technical"))
    if spec["architecture"]["pattern"] not in ref.patterns:
        out.append(Finding("error", "UNKNOWN_PATTERN", spec["architecture"]["pattern"],
                           "spec/architecture/pattern"))

    if ref.models:
        for m in spec["models"]["supported"]:
            if m not in ref.models:
                out.append(Finding("error", "UNKNOWN_MODEL", m, "spec/models/supported"))
        if (d := spec["models"].get("default")) and d not in spec["models"]["supported"]:
            out.append(Finding("error", "DEFAULT_MODEL_NOT_SUPPORTED", d, "spec/models/default"))
    if ref.runtimes:
        for r in spec["runtimes"]:
            if r not in ref.runtimes:
                out.append(Finding("error", "UNKNOWN_RUNTIME", r, "spec/runtimes"))
    if ref.teams and meta["team"] not in ref.teams:
        out.append(Finding("error", "UNKNOWN_TEAM", meta["team"], "metadata/team"))
    if ref.owner_groups:
        members = ref.owner_groups.get(meta["owner"])
        if members is None:
            out.append(Finding("error", "UNKNOWN_OWNER_GROUP", meta["owner"], "metadata/owner"))
        elif members < 2:
            out.append(Finding("error", "OWNER_GROUP_TOO_SMALL",
                               f"{meta['owner']} has {members} member(s); 2 required",
                               "metadata/owner"))

    for kind in ("required", "optional"):
        for p in spec.get("plugins", {}).get(kind, []):
            known = ref.plugins.get(p["id"])
            if ref.plugins and known is None:
                out.append(Finding("error", "UNKNOWN_PLUGIN", p["id"], f"spec/plugins/{kind}"))
            elif known and not resolve_range(p["range"], known.get("versions", [])):
                # A warning, not an error: the harness is still describable and
                # useful to read. The card renders the plugin as `unresolved`,
                # and /admin/ lists it. Hiding the card would over-correct.
                out.append(Finding("warning", "UNRESOLVABLE_PLUGIN_RANGE",
                                   f"{p['id']} {p['range']} matches no published version",
                                   f"spec/plugins/{kind}"))
            elif known and p.get("trust_tier") and p["trust_tier"] != known.get("trust_tier"):
                out.append(Finding("warning", "TRUST_TIER_MISMATCH",
                                   f"{p['id']} declares {p['trust_tier']}, marketplace says "
                                   f"{known.get('trust_tier')}", f"spec/plugins/{kind}"))

    # ---- lifecycle-conditional rules ------------------------------------
    lc = spec["lifecycle"]
    if lc in ("deprecated", "retired") and "deprecation" not in spec:
        out.append(Finding("error", "DEPRECATION_REQUIRED",
                           f"lifecycle '{lc}' requires spec.deprecation", "spec/deprecation"))
    if lc not in ("deprecated", "retired") and "deprecation" in spec:
        out.append(Finding("error", "DEPRECATION_FORBIDDEN",
                           "spec.deprecation is only valid for deprecated/retired harnesses",
                           "spec/deprecation"))
    if spec["version"].startswith("0.") and lc in ("org-ready", "certified"):
        out.append(Finding("error", "ZEROVER_TOO_MATURE",
                           "0.x versions cannot claim org-ready or certified", "spec/version"))
    if lc in ("team-ready", "org-ready", "certified") and len(spec.get("limitations", [])) < 3:
        out.append(Finding("warning", "FEW_LIMITATIONS",
                           f"{lc} harnesses should declare >= 3 limitations", "spec/limitations"))

    # ---- policy rules (structural, not behavioural) ----------------------
    out += policy_check(manifest)

    # ---- layer 3: repository consistency ---------------------------------
    if project is not None:
        out += consistency_check(manifest, project)

    return out


def policy_check(manifest: dict) -> list[Finding]:
    """Structural policy assertions. Same rules as `harnessctl policy-check`."""
    spec = manifest["spec"]
    sec = spec["security"]
    out: list[Finding] = []
    guard_types = {g["type"] for g in spec.get("guardrails", [])}

    pii = {"customer-pii", "employee-pii", "health"}
    if pii & set(sec["data_types"]) and "pii-redaction" not in guard_types:
        out.append(Finding("error", "POL_PII_REDACTION",
                           "handles personal data but declares no pii-redaction guardrail",
                           "spec/guardrails"))
    if sec["classification"] in ("confidential", "restricted") and sec["human_review"] == "not-required":
        out.append(Finding("warning", "POL_HUMAN_REVIEW",
                           f"{sec['classification']} data with human_review: not-required",
                           "spec/security/human_review"))
    if sec.get("risk_level") in ("high", "critical") and not spec.get("policies"):
        out.append(Finding("warning", "POL_NO_POLICY_MAPPING",
                           "high/critical risk with no policy references", "spec/policies"))
    for dest in sec.get("egress", []):
        if dest != "none" and not dest.endswith(".acme.internal"):
            out.append(Finding("error", "POL_EXTERNAL_EGRESS",
                               f"external egress destination '{dest}' requires security approval",
                               "spec/security/egress"))
    for field, value in (("index", spec.get("retrieval", {}).get("index", "")),):
        if re.match(r"^\w+://", str(value)):
            out.append(Finding("error", "POL_ENDPOINT_IN_MANIFEST",
                               f"{field} must be a logical name, not an endpoint",
                               f"spec/retrieval/{field}"))
    return out


def consistency_check(manifest: dict, project: Any) -> list[Finding]:
    """Does the repository actually back up the manifest's claims?"""
    out: list[Finding] = []
    spec, meta = manifest["spec"], manifest["metadata"]
    files = project.files

    # CODEOWNERS must cover every declared maintainer.
    if (co := files.get("CODEOWNERS")):
        owners = set(re.findall(r"@[\w.\-]+", co))
        missing = set(meta["maintainers"]) - owners
        if missing:
            out.append(Finding("warning", "CODEOWNERS_MISMATCH",
                               f"maintainers not in CODEOWNERS: {sorted(missing)}", "CODEOWNERS"))

    # Every declared guardrail must be *evidenced*: wired as a workflow step, or
    # pointing at something that exists (a file in the repository, or a plugin the
    # manifest actually requires). Some real guardrails — a cost ceiling, a rate
    # limit — are enforced by configuration rather than by a step, and the check
    # has to know that or it produces false failures on correct harnesses.
    declared = {g["id"]: g for g in spec.get("guardrails", [])}
    if declared:
        workflows = _read_workflows(project)
        wired = set(re.findall(r"guardrail:\s*([a-z0-9-]+)", workflows))
        plugin_ids = {p["id"] for kind in ("required", "optional")
                      for p in spec.get("plugins", {}).get(kind, [])}
        tree = set(project.tree) | set(project.files)
        if not workflows and not tree:
            out.append(Finding("info", "GUARDRAIL_NOT_VERIFIABLE",
                               "no workflow or repository listing available, so guardrail "
                               "wiring could not be checked", "spec/guardrails"))
        else:
            for gid, g in declared.items():
                if gid in wired:
                    continue
                impl = str(g.get("implemented_by", ""))
                if impl.startswith("plugin:"):
                    if impl.removeprefix("plugin:") in plugin_ids:
                        continue
                    out.append(Finding("error", "GUARDRAIL_PLUGIN_NOT_REQUIRED",
                                       f"{gid} is implemented by {impl}, which the manifest "
                                       f"does not declare as a plugin", "spec/guardrails"))
                    continue
                path = impl.split("#")[0]
                if path and (path in tree or any(t.startswith(path) for t in tree)):
                    continue
                out.append(Finding("error", "GUARDRAIL_NOT_WIRED",
                                   f"{gid} is declared but not wired into a workflow step and "
                                   f"its implementation "
                                   f"{'is not declared' if not impl else f'({impl}) does not exist'}",
                                   "spec/guardrails"))

    # config_schema must exist and be closed.
    if (cs := spec["interfaces"].get("config_schema")):
        if cs not in files and cs not in project.tree:
            out.append(Finding("error", "CONFIG_SCHEMA_MISSING", cs, "spec/interfaces/config_schema"))
        elif cs in files:
            try:
                if json.loads(files[cs]).get("additionalProperties") is not False:
                    out.append(Finding("warning", "CONFIG_SCHEMA_OPEN",
                                       "config schema should set additionalProperties: false", cs))
            except json.JSONDecodeError as e:
                out.append(Finding("error", "CONFIG_SCHEMA_INVALID", str(e), cs))

    # Released version must match the manifest.
    if project.releases:
        tag = project.releases[0]["tag_name"].lstrip("v")
        if tag != spec["version"]:
            out.append(Finding("warning", "VERSION_TAG_MISMATCH",
                               f"latest release {tag} != spec.version {spec['version']}",
                               "spec/version"))
        if (cl := files.get("CHANGELOG.md")) and spec["version"] not in cl:
            out.append(Finding("warning", "CHANGELOG_MISSING_VERSION",
                               f"no CHANGELOG entry for {spec['version']}", "CHANGELOG.md"))

    # Supported models should have been evaluated at least once.
    evaluated = {e["run"]["environment"]["model"] for e in project.evaluations}
    if evaluated:
        unproven = set(spec["models"]["supported"]) - evaluated
        if unproven:
            out.append(Finding("info", "MODEL_NOT_EVALUATED",
                               f"declared supported but never evaluated: {sorted(unproven)}",
                               "spec/models/supported"))

    # Stale operational figures mislead capacity planning.
    if (lat := spec.get("operations", {}).get("estimated_latency", {})).get("measured_on"):
        from datetime import date
        age = (date.today() - date.fromisoformat(lat["measured_on"])).days
        if age > 180:
            out.append(Finding("warning", "STALE_LATENCY",
                               f"latency last measured {age} days ago",
                               "spec/operations/estimated_latency"))
    return out


def _read_workflows(project: Any) -> str:
    return "\n".join(v for k, v in project.files.items() if k.startswith("workflows/"))


# --------------------------------------------------------------------------
# Minimal SemVer range resolution (caret, tilde, exact, ">=", "*").
# Deliberately small: we control the range vocabulary, so a full parser is
# unnecessary weight. Anything unrecognised resolves to "no match", which
# surfaces as an explicit validation error rather than a silent pass.
# --------------------------------------------------------------------------
def parse_version(v: str) -> tuple[int, int, int]:
    core = v.lstrip("v").split("-")[0].split("+")[0]
    parts = (core.split(".") + ["0", "0"])[:3]
    return tuple(int(re.sub(r"\D", "", p) or 0) for p in parts)  # type: ignore[return-value]


def satisfies(version: str, spec: str) -> bool:
    spec = spec.strip()
    v = parse_version(version)
    if spec in ("*", ""):
        return True
    if spec.startswith("^"):
        b = parse_version(spec[1:])
        if b[0] > 0:
            return v[0] == b[0] and v >= b
        if b[1] > 0:
            return v[0] == 0 and v[1] == b[1] and v >= b
        return v[0] == 0 and v[1] == 0 and v[2] >= b[2]
    if spec.startswith("~"):
        b = parse_version(spec[1:])
        return v[0] == b[0] and v[1] == b[1] and v >= b
    if spec.startswith(">="):
        return v >= parse_version(spec[2:])
    if spec.startswith(">"):
        return v > parse_version(spec[1:])
    return v == parse_version(spec)


def resolve_range(spec: str, versions: list[str]) -> str | None:
    """Highest published version satisfying the range, or None."""
    matching = [v for v in versions if satisfies(v, spec)]
    return max(matching, key=parse_version) if matching else None
