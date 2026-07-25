# Governance records

State that a harness team must not be able to write about itself.

| Directory | Contains | Approvals required (CODEOWNERS) |
|---|---|---|
| `approvals/` | Org-ready approvals, review dates, gate waivers | Governance board + domain steward |
| `certifications/` | Certification records and their evidence | 2× governance board + security (+ legal if regulated) |

The registry pipeline validates every claim in these files against live evidence
before the record takes effect: quality score, pass rate, evaluation freshness,
signed tag, human review. Humans review the judgement; the pipeline checks the
arithmetic.

Records are never deleted. Revocation sets `state: revoked` with a reason, so
the history of what was trusted, when, and on what evidence remains auditable.
