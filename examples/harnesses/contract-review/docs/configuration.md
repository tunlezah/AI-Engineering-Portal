# Configuration reference

Authoritative schema: [`config/schema.json`](../config/schema.json). CI rejects
any config in this repo, in tests, or in a consuming skill's manifest that does
not validate against it.

## Choosing a model

| | `claude-sonnet-5` (default) | `claude-opus-5` |
|---|---|---|
| Golden pass rate | 0.962 | 0.971 |
| Severity agreement | 0.842 | 0.860 |
| Cost per document | £0.42 | ~£1.01 |
| p95 latency | 168 s | 241 s |

Use Opus for tier-1 counterparties and for agreements over £1m TCV. The gain is
real but modest; the cost is 2.4×. Both are gated in the evaluation matrix, so a
regression in either fails the pipeline.

`claude-haiku-4-5` is explicitly unsupported: clause F1 drops to 0.71, below the
0.85 gate. This is recorded in `spec.models.unsupported` so nobody re-runs the
experiment.

## Severity profiles

- `balanced` — playbook severities as written. Default.
- `conservative` — promotes liability, indemnity, data-protection and termination
  divergences from `medium` to `high`. Roughly +35% high findings; use for tier-1.
- `permissive` — demotes drafting-quality findings to `info`. Intended for
  high-volume NDA triage where only material risk is actioned. Requires an owner
  sign-off recorded in the consuming skill's manifest.

## Retrieval

`retrieval.top_k` defaults to 8. Values above 12 are rejected by the schema:
measured grounding rate falls from 0.994 to 0.981 at `top_k: 16` because the
model cites weakly relevant positions. More context is not free.

`retrieval.min_score: 0.35` suppresses low-relevance positions entirely, which
is what drives honest `UNMATCHED` output rather than a forced nearest match.

## Grounding

`grounding.on_failure: drop` is mandatory at `confidential` classification and
above, and the policy check enforces it. `flag` exists for development only: it
emits ungrounded findings marked as such, which is useful when debugging a
prompt change and dangerous anywhere near a real reviewer.

## Cost ceiling

`cost_ceiling_gbp` is a hard stop, not a warning. On breach the run returns
partial findings with `coverage_report.truncated: true`. Set it above the p95
cost for your document mix — £2.00 covers a 200-page agreement on Sonnet.

## What you cannot configure

Deliberately absent, because these are guarantees rather than options:

- Disabling PII redaction.
- Disabling the grounding check.
- Disabling the no-advice filter.
- Skipping lawyer sign-off.

If your use case requires any of these, it is a different harness with a
different risk assessment, not a configuration of this one.
