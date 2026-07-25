# Service Ticket Triage

> Classifies, routes and summarises inbound service tickets with confidence-gated automation.

## Overview

Roughly 41,000 tickets a month arrive at the service desk, and the first ten
minutes of each one is the same work: read it, work out what it is, decide who
should have it, and write a summary the assignee can act on. This harness does
that work and hands over anything it is not confident about.

The design choice that matters: **the model classifies, code routes**. A
misclassification produces a wrong suggestion, never a ticket in a queue the
requester is not entitled to reach.

## Quick Start

```shell exec
harnessctl install
harnessctl run --input ticket_id=INC0042311 --config config/default.yaml --out ./out
harnessctl explain ./out/routing.json
```

## Configuration

| Key | Default | Notes |
|---|---|---|
| `model` | `claude-haiku-4-5` | Sonnet raises category F1 by 2.1pp at 6× cost |
| `confidence_threshold` | `0.72` | Below this the ticket goes to a human triager |
| `retrieval.top_k` | `6` | Similar-incident context |
| `auto_assign` | `false` | Writing the routing decision back requires an explicit opt-in per queue |

## Evaluation

Category F1 0.884, routing accuracy 0.912, confidence calibration (ECE) 0.041,
p95 latency 7.4 s, £0.018 per ticket. Full reports in `evaluations/`.

## Limitations

English tickets only; screenshot-only tickets cannot be triaged; catalogue
changes require re-evaluation; urgency inference is advisory.

## Support

`#ai-itsm`, business hours UK. Escalation `@d.fischer`.
