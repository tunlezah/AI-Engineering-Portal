---
title: Using the registry
weight: 1
summary: What this site is, how to search it, and how to read a harness page before you depend on one.
---

## Read this first

Everything in this registry was written and evaluated by another team, for
their problem. The registry's job is to show you **evidence** — what a harness
claims, what the platform observed, and where the two disagree. It is not a
recommendation, and an entry appearing here is not an approval to use it.

Before you adopt a harness:

1. Read its page end to end, including the **Limitations** section. A harness
   with no stated limitations has not been evaluated honestly, and the registry
   scores it accordingly.
2. Check its **lifecycle tier** and what the tier actually means (below). A
   tier is computed from evidence, not awarded on request.
3. Check its **evaluation history**, not just its latest pass rate. One green
   run says less than a stable trend.
4. Confirm its **data classification** and **risk level** match what you intend
   to put through it. A harness evaluated on public documents is not cleared
   for customer PII because it happens to work.
5. Read its **CHANGELOG** and version. The contract is the interface, and it
   changes.

You remain accountable for what you ship. The banner at the top of every page
says the same thing; dismiss it once you have read it and it stays dismissed on
this browser.

## Finding a harness

**[Browse](/browse/)** is the fast path. It runs entirely in your browser
against a single generated catalogue file — there is no search server, so there
is nothing to be down, and every query is a shareable URL. Paste one into a
merge request and the reviewer sees exactly what you saw.

### The query language

Bare words match names, summaries, domains and tags. Everything else is a
`field:value` filter.

| You want | Type this |
|---|---|
| Text search | `clause extraction` |
| An exact phrase | `"clause extraction"` |
| Only certified harnesses | `lifecycle:certified` |
| A team's harnesses | `team:legal-eng` |
| One architecture | `pattern:rag` |
| Needs a specific plugin | `plugin:pii-redactor` |
| Runs on a model | `model:claude-sonnet-5` |
| Quality above 85 | `quality:>85` |
| Evaluation pass rate above 90% | `pass:>0.9` |
| Actually adopted by something | `consumers:>0` |
| Touched in the last 90 days | `updated:<90d` |
| Exclude anything deprecated | `-lifecycle:deprecated` |

Filters combine, and repeating a field widens rather than narrows it —
`lifecycle:certified lifecycle:org-ready` returns both tiers. The facet
checkboxes on the left do the same thing; counts update against the current
result set, so a facet never offers a filter that would return nothing.

Ranking is deliberately not pure text relevance — an abandoned experiment that
matches perfectly should not outrank a certified harness that matches nearly as
well. The exact weights are published in
[How search ranks](/docs/search-ranking/).

## Reading a harness page

### Lifecycle

The tier on the card is the **effective** one — what the evidence supports —
which is not always what the manifest declared. Where they differ the page says
so and lists the reasons. A team cannot promote its own harness by editing a
field.

| Tier | What it means |
|---|---|
| **Certified** | Passed governance review; a certification record exists in the registry repository, not in the harness repository |
| **Org Ready** | Evidenced, evaluated and supported for use outside the owning team |
| **Team Ready** | Works, tested and evaluated, but supported only by its own team |
| **Prototype** | Real but incomplete — expect gaps in tests, docs or evaluation |
| **Experimental** | An exploration. Read the code before depending on any of it |
| **Deprecated** | Still indexed, on its way out. The page names the replacement |
| **Retired** | Kept for the historical record. Do not build on it |
| **Invalid manifest** | The manifest failed validation. Shown deliberately — hiding it would make the estate look healthier than it is |

### Quality score

A 0–100 score computed from observed signals — tests, evaluation coverage,
documentation, freshness, adoption, governance — never self-reported. The
breakdown chart shows where the points came from and, more usefully, where they
did not. A score is capped by missing fundamentals: a harness whose manifest
does not validate cannot score at all.

Treat the score as a prompt to look closer, not a verdict. A 90 on a harness
built for a different data classification than yours is irrelevant to you.

### Evaluations

Pass rates come from evaluation reports produced by the harness's own pipeline
and collected as build artefacts, against a schema. The page shows the trend,
not just the latest number, and attributes movement where it can — a drop
caused by a model build change is not the harness's fault, and the registry
says so rather than penalising the team.

No evaluation at all is itself information. It is shown as "no evaluation",
never as a passing score.

### Relationships

The diagram on each page is generated from the index, so it cannot go stale. It
shows one hop: the skills built on this harness, the plugins it requires, the
harnesses it depends on, and the team that owns it. "Consumers" counts what
would break if the harness changed — the most honest measure of how load-bearing
it is.

## Choosing a colour theme

The control in the top right cycles **follow system → light → dark**. It
defaults to following your operating system setting, and your choice is
remembered on this browser. Nothing is sent anywhere; the preference lives in
`localStorage`.

## Working offline

There is no server behind this site. It is static HTML generated from the
registry snapshot, and every asset — stylesheet, script, font, diagram — is
served from the same origin. No CDN, no analytics, no external fonts, no calls
home. A build that references anything off-site fails its pipeline rather than
shipping.

In practice that means the site works on an air-gapped network, and you can
save a page and still read it. Links out to GitLab repositories are ordinary
links: they need the network if you click them, but nothing fetches them to
render the page.

## When something looks wrong

The **[Registry health](/admin/)** page lists every manifest that failed
validation, every harness whose declared lifecycle was downgraded, unresolved
plugin references and overdue reviews. If a harness looks wrong here, that page
usually explains why — and if it does not, the fix belongs in the harness's own
repository, since the registry only ever reports what it observed.
