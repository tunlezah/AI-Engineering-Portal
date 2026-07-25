---
title: Versioning
weight: 30
summary: SemVer governs the contract, not the prompt text.
---

Prompts change constantly and every change alters behaviour. If every prompt
edit were a major bump, versions would mean nothing; if none were, consumers
would get silently different behaviour. So the **contract** is defined
explicitly:

| In the contract | Not in the contract |
|---|---|
| `spec.interfaces.inputs` / `.outputs` | Prompt wording |
| `config/schema.json` | Internal step decomposition |
| Guardrail ids and enforcement levels | Logging and telemetry |
| Required plugin ranges | Latency and cost (tracked separately) |
| Output value domains | Documentation |

| Change | Bump |
|---|---|
| Remove or rename an output, narrow an input, add a required input, drop a supported model, raise a required plugin major | **MAJOR** |
| Add an optional input or output, add a model, **or any behavioural change that moves evaluation beyond `max_delta`** | **MINOR** |
| Clarification, bug fix, docs, performance — with no measurable metric movement | **PATCH** |

The middle row is decided **empirically, not editorially**: if the evaluation
moves beyond the threshold in `evaluations/thresholds.yaml`, it is at least a
minor release, whatever the diff looks like. `harnessctl check-versions`
enforces it by comparing the tagged run against the previous release.

A major release is not accepted without a migration guide, a deprecation of the
previous line with a dated end of support, and an old-versus-new evaluation
comparison over the same dataset — published on the harness page. That last one
forces the team to show, in numbers and in public, that the break was worth it.
