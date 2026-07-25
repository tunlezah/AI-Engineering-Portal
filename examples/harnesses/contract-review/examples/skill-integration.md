# Adopting this harness in a skill

Three skills consume `contract-review`. This shows how, and where the boundary
between skill and harness sits.

## The boundary

| Concern | Owner |
|---|---|
| Who the user is, what they see, business framing, entitlement | **Skill** |
| Clause segmentation, retrieval, prompting, grounding, guardrails | **Harness** |
| Playbook content | Legal (data, not code) |
| Model access, redaction, audit sink | Platform |

A skill that finds itself editing prompts is doing harness work and should
contribute upstream instead of forking. Fork only when the *implementation*
genuinely diverges — see the fork policy on the registry.

## Skill manifest reference

`skill://contract-triage` declares its harness dependency; the registry reads
this to build the consumer graph in the other direction.

```yaml
apiVersion: skill.marketplace.acme.internal/v1
kind: Skill
metadata:
  id: contract-triage
  name: Contract Triage
  owner: group:legal-operations
spec:
  harnesses:
    - id: contract-review
      range: "^2.3.0"
      config:
        model: claude-sonnet-5
        playbook_id: uk-standard
        severity_profile: balanced
  surfaces: [legal-portal, teams-bot]
  entitlement: role:legal-ops
```

## Calling the harness

```python
from harness_sdk import Harness

harness = Harness.resolve("contract-review", range="^2.3.0")  # registry lockfile

result = harness.run(
    inputs={
        "contract_file": upload.path,
        "playbook_id": "uk-standard",
        "counterparty_tier": matter.tier,
    },
    config={"severity_profile": "conservative"} if matter.tier == "tier-1" else {},
    context={"matter_id": matter.id, "requested_by": user.id},
)

# The run is not finished when this returns: signoff is a human step.
assert result.state == "awaiting-signoff"
portal.enqueue(result.summary, result.findings, sla_hours=24)
```

## What the skill must handle

1. **`review-required` findings.** They are not failures; they are the harness
   declining to guess. Present them as decisions for the lawyer.
2. **Partial results.** If the cost ceiling or wall-clock budget is hit, the
   harness returns partial findings with `coverage_report.truncated: true`.
   Never render a partial review as complete.
3. **Rejected findings.** Sign-off decisions belong to the skill's domain, but
   push them back: `harness.feedback(run_id, decisions)` feeds the golden-suite
   candidate queue. Every consuming skill doing this is why the suite grows.
4. **Version pinning.** Pin a range, not `latest`. The registry emails owners of
   consuming skills when a MAJOR version is published or a version is deprecated.

## Anti-patterns seen in review

- **Re-prompting on top of the output.** If the finding wording is wrong for your
  users, that is a harness issue — raise it. A second model pass over findings
  breaks the grounding guarantee, because the citations no longer match the text.
- **Bypassing sign-off** by reading `findings` before the signoff step completes.
  The audit record will show it, and the guardrail test suite asserts against it.
- **Copying the prompts into the skill.** You inherit today's behaviour and none
  of tomorrow's fixes, and you leave the evaluation suite behind.
