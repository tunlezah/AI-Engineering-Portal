# Code Review Assistant

> First-pass merge request review with test-backed findings and repository-aware context.

## Overview

Reviews a merge request and produces findings a human reviewer can act on. The
distinguishing rule: a finding that claims a behavioural defect must **reproduce
in the sandboxed test runner** before it is emitted. Unreproducible claims are
downgraded to observations, which is why reviewers stopped ignoring the output.

## Quick Start

```shell exec
harnessctl install
harnessctl run --input merge_request=ai-platform/harness-registry!42 --out ./out
```

## Configuration

| Key | Default | Notes |
|---|---|---|
| `model` | `claude-sonnet-5` | Opus for security-focused reviews |
| `focus` | all | `correctness`, `security`, `performance`, `tests` |
| `max_findings` | `15` | Ranked; a 40-finding review is not read |
| `cost_ceiling_gbp` | `1.50` | Hard stop, returns partial review |

## Evaluation

Precision on seeded defects 0.81, reproduction rate 0.74, false-positive rate
0.09, p95 latency 158 s. Latency is currently **waived** pending an upstream
`repo-context` fix (DX-812).

## Limitations

Four languages with reproduction; no full-integration review; advisory only;
very large MRs are sampled.

## Support

`#dx-ai`, business hours. Escalation `@k.duarte`.
