---
title: Publish a harness
weight: 10
summary: Ten minutes from nothing to an indexed harness.
---

## 1. Scaffold

```shell
harnessctl new my-harness --pattern rag
```

Or *New project → From template → harness-template* in GitLab. Either gives you
the standard layout, a working prompt, a starter evaluation suite for your
pattern's metrics, and a pipeline that is green on the first run.

## 2. Fill in the manifest

`harness.yaml` is the registry contract. The two fields people skip are the two
that matter most to your future readers:

- **`spec.limitations`** — mandatory. A harness with no stated limitations has
  not been evaluated honestly.
- **`spec.models.unsupported`** — what you tried that did not work, and why.
  This is how the organisation stops repeating failed experiments.

## 3. Validate locally

```shell
harnessctl validate .
```

Schema, taxonomy terms, plugin ranges, owner group, guardrail wiring, CODEOWNERS
consistency, links. The same code runs in CI and in the indexer, so a green
local run means a green pipeline.

## 4. Push

That is the whole registration process. **A project is a harness because it has
`harness.yaml` at its root** — there is no form, no allowlist, and nothing to
fall out of date. Your harness appears within about a minute of the pipeline
finishing, and within the hour regardless.

## 5. Earn your tier

Publishing is free. *Claims* cost evidence:

| To reach | You need |
|---|---|
| Prototype | README sections, green pipeline |
| Team Ready | tests, a golden suite of ≥50 cases, an executable quick start, a changelog, quality ≥60 |
| Org Ready | adversarial suite, coverage ≥0.8, SBOM, pinned dependencies, architecture diagram, quality ≥75, pass rate ≥0.90 |
| Certified | signed release, reproducible evaluations, human review, a governance record, quality ≥90, pass rate ≥0.95 |

The indexer downgrades a declaration the evidence does not support — and never
upgrades one. If your card says something lower than your manifest, the harness
page tells you exactly which signal is missing.

## What not to do

- Do not copy prompts out of another harness. Depend on it, or fork it with a
  new id and a stated reason. Copies inherit today's behaviour and none of
  tomorrow's fixes.
- Do not put secrets, endpoints or connection strings in the repository. The
  pipeline blocks on this, and rotating a leaked credential is your afternoon.
- Do not write your own quality score. You cannot; it is computed.
