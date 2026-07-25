# AI Harness Registry — design and blueprint

An architecture decision record plus implementation blueprint for an **AI Harness
Registry**: the engineering layer that sits between the Prompt Library / Plugin
Marketplace (components) and the Skill Marketplace (business capabilities), and
lets AI teams share complete, evaluated *implementations* rather than isolated
prompts.

Fully offline: GitLab, GitLab CI, GitLab Pages. No SaaS, no cloud APIs, no
internet egress at build or run time.

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
| `tools/registryctl/crawl.py` | Reference crawler over GitLab groups (ETag-conditional) |
| `tools/registryctl/index.py` | Reference indexer: scoring, lifecycle gates, graph, catalog |
| `examples/harnesses/contract-review/` | A complete worked harness — manifest, prompts, workflow, guardrails, evaluations, datasets, tests, ADRs, docs |

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
