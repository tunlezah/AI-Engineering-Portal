# AI Harness Registry — design and blueprint

An architecture decision record plus implementation blueprint for an **AI Harness
Registry**: the engineering layer that sits between the Prompt Library / Plugin
Marketplace (components) and the Skill Marketplace (business capabilities), and
lets AI teams share complete, evaluated *implementations* rather than isolated
prompts.

Fully offline: GitLab, GitLab CI, GitLab Pages. No SaaS, no cloud APIs, no
internet egress at build or run time.

## Run it

The registry is implemented, not just specified. It builds offline from the
fixture harnesses in `examples/harnesses/` — no GitLab instance required.

```shell
make all        # validate -> crawl -> index -> emit -> build -> verify
make serve      # browse it at http://localhost:1313
make test       # 77 tests: scoring, lifecycle gates, validation, pipeline
```

Requires Python 3.11 (`pyyaml`, `jsonschema`) and Hugo extended ≥ 0.148.
Against a real GitLab instance, swap the source: `make crawl-gitlab`
(`REGISTRY_READ_TOKEN` + `groups.yaml`).

What a build produces: 9 harnesses indexed, 8 valid, 2 auto-downgraded for
over-claimed maturity, 1 invalid manifest rendered as an error card, 172 pages,
~90 ms Hugo build, zero external asset references.

## Start here

**[`docs/ai-harness-registry-design.md`](docs/ai-harness-registry-design.md)** — the
full design, 23 sections, from high-level architecture through metadata,
indexing, search, governance, versioning, CI/CD, security and a critical review
of the design's own weaknesses.

## What's in this repository

Everything the document specifies exists here as a real artefact, not a sketch.

| Path | What it is |
|---|---|
| `docs/ai-harness-registry-design.md` | The design document |
| `schema/harness.schema.json` | Harness manifest schema (JSON Schema 2020-12) |
| `schema/evaluation-report.schema.json` | Machine-readable evaluation report schema |
| `schema/quality-rubric.yaml` | Quality scoring dimensions, signals and lifecycle gates |
| `schema/taxonomy.yaml` | Business/technical taxonomy and reference architectures |
| `ci/harness-ci.yml` | Shared GitLab CI template included by every harness project |
| `ci/registry-ci.yml` | Registry pipeline: crawl → index → verify → build → deploy |
| `tools/registryctl/` | **The implementation**: sources, validation, observation, scoring, indexing, content emission, verification, CLI |
| `tools/dev/` | Fixture generators (evaluation histories, blueprint pages) |
| `site/` | The Hugo site: templates, hand-written CSS, vendored JS, faceted search |
| `tests/` | Scoring, lifecycle gating, validation rules, end-to-end pipeline |
| `examples/harnesses/` | Nine worked harnesses, including a certified one, a deliberately invalid one, and two that get auto-downgraded |
| `governance/` | Approval and certification records — held outside the harness repos on purpose |
| `vendor/` | Sibling registry exports, platform catalogues, vendored browser assets |

## The nine decisions

1. **Git is the database** — generated, committed snapshot; no server, no DB.
2. **A project is a harness iff `harness.yaml` exists at its root** — discovery, not registration.
3. **Manifests declare; the registry observes** — scores and lifecycle are computed, never self-awarded.
4. **Crawl for truth, push for latency** — ~40 s to live, hourly reconciliation is authoritative.
5. **Hugo + Pagefind + a generated facet catalog** — vendorable, fast at thousands of pages.
6. **Governance state lives in the registry repo** — a team cannot certify itself.
7. **SemVer governs the contract**, not prompt text — with an empirical test for behavioural change.
8. **Evaluation is a build artefact** — schema-valid, gated, trended, attributable.
9. **Everything vendored** — enforced by a blocking "no external assets" CI gate.

Rationale, trade-offs and rejected alternatives for each are in the document.

## How the pieces fit

```
examples/harnesses/**/harness.yaml     a project is a harness because this exists
        │  registryctl crawl           (LocalSource here, GitLabSource in production)
        ▼
build/crawl/*.json                     normalised projects
        │  registryctl index           validate → observe → score → gate → resolve graph
        ▼
build/snapshot/                        cards, graph, catalog.json, registry.lock.yaml, index report
        │  registryctl emit-content    content adapters (the replaceable layer)
        ▼
site/content + site/data + static/     Hugo input
        │  hugo
        ▼
site/public/                           the registry — static, offline, ~4 MB
        │  registryctl verify-site     links, external assets, budgets, accessibility
        ▼
GitLab Pages
```

The snapshot is the interface. Everything above it is the durable asset —
metadata model, rubric, evaluation contract, governance separation. Everything
below it is presentation, and could be replaced without touching any of that.
