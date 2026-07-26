# AI Harness Registry

The engineering layer between the Prompt Library / Plugin Marketplace
(components) and the Skill Marketplace (business capabilities): a registry that
lets AI teams share complete, evaluated **implementations** rather than isolated
prompts.

It is a **static site with no backend**. No database, no application server, no
search service, no runtime API. A pipeline reads harness manifests, computes a
snapshot, renders HTML, and GitLab Pages serves the result. Everything is
vendored, and a blocking build gate fails the pipeline if any page references an
asset it cannot serve itself — so the published site works on an air-gapped
network.

```shell
make all        # validate → crawl → index → emit → build → verify
make serve      # browse it at http://localhost:1313
make test       # 150 tests: scoring, lifecycle gates, validation, pipeline,
                #            sources, security
```

Requires Python 3.11 (`pyyaml`, `jsonschema`) and Hugo **extended** ≥ 0.148.

A build produces: 9 harnesses indexed, 8 valid, 2 auto-downgraded for
over-claimed maturity, 1 invalid manifest rendered as an error card, 137 pages,
~4 MB, ~130 ms Hugo build, zero external asset references.

## Deploying to GitLab Pages

[`.gitlab-ci.yml`](.gitlab-ci.yml) is the pipeline that builds and publishes this
repository. It runs the same steps as `make all` and ends in a `pages` job, so a
green pipeline on the default branch is a deployed site. It needs no GitLab API,
no database and no network access beyond pulling its two container images.

The one thing that is easy to get wrong: a GitLab Pages **project** site is
served from `https://<group>.gitlab.io/<project>/`, not from the domain root. If
Hugo builds with the default `baseURL`, every stylesheet, script and link on the
deployed site resolves one level too high and the site arrives unstyled. The
pipeline therefore builds with `--baseURL "$CI_PAGES_URL"`, and `verify-site`
resolves links against that same prefix. To reproduce a subpath build locally:

```shell
make site BASEURL=https://group.gitlab.example/harness-registry/
python3 -m tools.registryctl verify-site --public site/public
```

`verify-site` auto-detects the prefix; pass `--base-path` to be explicit.

### Air-gapped installs

The only things the pipeline pulls from outside the repository are two container
images and the Python dependencies. Override four variables in
*Settings → CI/CD → Variables* and it has no egress at all:

| Variable | Purpose |
|---|---|
| `PYTHON_IMAGE` | An image with Python 3.11+ |
| `HUGO_IMAGE` | An image with Hugo extended ≥ 0.148 |
| `PIP_INDEX_URL` | Your internal PyPI mirror |
| `PIP_ARGS` | e.g. `--no-index --find-links vendor/wheels` |

`ci/registry-ci.yml` is the *production* pipeline for an organisation that has
real harness projects to crawl — incremental triggers, an hourly reconciliation
crawl, and a committed snapshot branch. Against a real instance, swap the source
with `make crawl-gitlab` (see below).

## Where the harnesses come from

**The harnesses do not live in this repository, and they do not have to live on
the GitLab that builds it.** `sources.yaml` is the entire coupling between the
registry and the estate it indexes, so teams keep their harnesses wherever they
already work and this project stays just the thing that reads them.

```yaml
gitlab:
  base_url: https://gitlab.other-instance.example   # a different GitLab entirely
  token_env: HARNESS_INSTANCE_TOKEN                 # with its own read token
groups:
  - platform/harnesses                              # walked recursively
projects:
  - path: some-team/ai-harnesses                    # one project, many harnesses
    nested: true
  - other-org/contract-review                       # one project, one harness
exclude:
  - "*/templates/*"
```

```shell
make crawl-gitlab                        # uses sources.yaml
make crawl-gitlab SOURCES=ours.yaml      # or any other file
registryctl crawl --sources sources.yaml --gitlab-url https://gitlab.example
```

| Setting | What it decides | Default |
|---|---|---|
| `gitlab.base_url` | Which instance holds the harnesses | `--gitlab-url`, then this, then `$REGISTRY_GITLAB_URL`, then `$CI_SERVER_URL` |
| `gitlab.token_env` | Which variable holds a token with `read_api` **on that instance** | `REGISTRY_READ_TOKEN` |
| `groups` | Namespaces walked recursively; one project per harness | — |
| `projects` | Projects named outright, `nested: true` for one project holding many | — |
| `exclude` | Globs over `group/project`, and `group/project/subdir` when nested | — |

Two things are worth knowing before you point it at a second instance. There is
no fallback if the URL or the token cannot be resolved: crawling the wrong
instance, or anonymously, yields an empty registry that publishes successfully,
which is the worst outcome available — so the crawl fails with a message naming
the missing setting. And `$CI_JOB_TOKEN` is scoped to the pipeline's own
instance; a separate one needs a real token in the variable `token_env` names.

In a **monorepo** (`nested: true`), pipeline status, releases and issue
statistics are project-wide, so every harness in it shares them and scores as one
engineering unit. Evaluation reports are the exception — each harness takes only
the reports under its own directory, because attributing a shared artefact to all
of them would manufacture evidence. Otherwise a nested harness and one that owns
its whole project produce identical records: paths are relative to the harness,
and nothing downstream can tell them apart.

For a runner with no route to the instance, `--harnesses-root DIR` applies the
same configuration to a local checkout, and `make index HARNESSES=/src/harnesses`
crawls any directory tree the same way it crawls the fixtures here.

## Linking out to the rest of the platform

The header carries an **Ecosystem** menu — Plugin Marketplace, Skills
Marketplace, Prompt Library, AI Gallery — repeated in the footer and on the home
page. They are separate products with their own URLs, so they are configuration:
`[[menus.ecosystem]]` in [`site/hugo.toml`](site/hugo.toml).

```shell
cp site/ecosystem.example.toml site/ecosystem.toml   # git-ignored
make site CONFIG=hugo.toml,ecosystem.toml
```

Blank an entry's `url` and it disappears everywhere — an organisation without an
AI Gallery gets no link rather than a dead one. Adding a fifth system is another
block in the config; no template changes. Hugo replaces arrays when merging
configs rather than appending, so an override file lists every link you want.

The links point out of the site and are never fetched to render a page, so they
do not affect the offline guarantee. `verify-site` reports a link to an unlisted
host as a warning; `--allow-host` (repeatable) is how a deployment declares its
own first-party domains.

## What offline actually means here

"No CDN" is enforced, not aspirational. `registryctl verify-site` fails the build
on:

- any **fetched** subresource pointing off-site — `<script src>`, `<link href>`,
  `<img src>`, `<video poster>`, CSS `url(...)`;
- any non-`http(s)` scheme in a URL;
- any broken internal link;
- any inline event handler or `javascript:` URL;
- pages over the weight budget, and a static accessibility sample.

Links *out* to GitLab repositories are allowed and warned about rather than
blocked: the registry indexes repositories it cannot host, and nothing fetches
them to render a page.

Verified in a real browser: loading the home page, browse, docs and a harness
page issues **zero off-origin requests**.

## Using the site

The web interface documents itself. **[Using the registry](site/content/docs/using-the-registry.md)**
(`/docs/using-the-registry/` when deployed) covers the search query language,
what the lifecycle tiers and quality scores mean, how to read an evaluation
trend, and what to check before depending on someone else's harness.

**Disclaimer.** Every page carries a banner telling readers to read and
understand a harness — its documentation, its stated limitations, its evaluation
history — before using it, and that scores are observations rather than
guarantees. It is dismissible, and the dismissal persists per browser in
`localStorage`. It is shown by default and hidden only once dismissed, so every
failure mode (no JavaScript, no storage, private window) leaves the disclaimer
visible.

**Themes.** The control in the header cycles **follow system → light → dark**.
The default follows the OS via `prefers-color-scheme`; an explicit choice
persists in `localStorage` and is applied by an inline script before first paint,
so there is no flash of the wrong theme. No preference leaves the browser.

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
        │  hugo --baseURL $CI_PAGES_URL
        ▼
site/public/                           the registry — static, offline, ~4 MB
        │  registryctl verify-site     links, external assets, injected markup, budgets, a11y
        ▼
GitLab Pages
```

The snapshot is the interface. Everything above it is the durable asset —
metadata model, rubric, evaluation contract, governance separation. Everything
below it is presentation, and could be replaced without touching any of that.

## The nine decisions

1. **Git is the database** — generated, committed snapshot; no server, no DB.
2. **A project is a harness iff `harness.yaml` exists at its root** — discovery, not registration.
3. **Manifests declare; the registry observes** — scores and lifecycle are computed, never self-awarded.
4. **Crawl for truth, push for latency** — ~40 s to live, hourly reconciliation is authoritative.
5. **Hugo + a generated facet catalog** — vendorable, fast at thousands of pages.
6. **Governance state lives in the registry repo** — a team cannot certify itself.
7. **SemVer governs the contract**, not prompt text — with an empirical test for behavioural change.
8. **Evaluation is a build artefact** — schema-valid, gated, trended, attributable.
9. **Everything vendored** — enforced by a blocking "no external assets" CI gate.

Rationale, trade-offs and rejected alternatives for each are in
**[`docs/ai-harness-registry-design.md`](docs/ai-harness-registry-design.md)** —
the full design, 23 sections, including a critical review of the design's own
weaknesses.

## Security posture

Any team can create a project containing a `harness.yaml`, so manifests,
READMEs, CHANGELOGs and evaluation artefacts are **untrusted input**. The
registry treats them that way:

- **Harness ids are validated before they touch the filesystem.** An id is a
  filename and a URL segment; anything that is not a plain slug is rejected
  rather than sanitised, and every write goes through a guard that refuses to
  leave its directory.
- **Repository Markdown is rendered with `goldmark.unsafe = false`,** so HTML in
  a README is escaped rather than executed. Generated diagrams and charts come
  from templates, not from Markdown, and never needed the raw-HTML escape hatch.
- **The browser trusts nothing from the catalogue** — every value interpolated
  into the DOM is escaped, including ids.
- **Crawled artefacts have decompression limits,** so a zip bomb in someone's
  pipeline artefact cannot take the crawler down.
- **The build gates the result**: external assets, inline event handlers and
  `javascript:` URLs all fail the pipeline.

`tests/test_security.py` holds a regression test for each of these; every one
corresponds to a real defect found in this codebase, not a hypothetical.

Note that `validation_ok` — whether a producer's pipeline ran the shared
validation job — is **evidence that feeds scoring, not a publication gate**. A
harness that fails validation is still indexed and shown as an error card, on
purpose: hiding broken harnesses would make the estate look healthier than it
is.

## What's in this repository

| Path | What it is |
|---|---|
| `.gitlab-ci.yml` | The pipeline that builds and publishes this repository to GitLab Pages |
| `sources.yaml` | **Where the harnesses are**: instance, credential, groups, projects, exclusions |
| `docs/ai-harness-registry-design.md` | The design document |
| `schema/harness.schema.json` | Harness manifest schema (JSON Schema 2020-12) |
| `schema/evaluation-report.schema.json` | Machine-readable evaluation report schema |
| `schema/quality-rubric.yaml` | Quality scoring dimensions, signals and lifecycle gates |
| `schema/taxonomy.yaml` | Business/technical taxonomy and reference architectures |
| `ci/harness-ci.yml` | Shared GitLab CI template included by every harness project |
| `ci/registry-ci.yml` | Production registry pipeline: crawl → index → verify → build → deploy |
| `tools/registryctl/` | **The implementation**: sources, validation, observation, scoring, indexing, content emission, verification, CLI |
| `tools/dev/` | Fixture generators (evaluation histories, blueprint pages) |
| `site/` | The Hugo site: templates, hand-written CSS, vendored JS, faceted search, theming, disclaimer, configurable ecosystem links |
| `tests/` | Scoring, lifecycle gating, validation rules, end-to-end pipeline, source configuration and discovery, security regressions |
| `examples/harnesses/` | Nine worked harnesses, including a certified one, a deliberately invalid one, and two that get auto-downgraded |
| `governance/` | Approval and certification records — held outside the harness repos on purpose |
| `vendor/` | Sibling registry exports, platform catalogues, vendored browser assets |
