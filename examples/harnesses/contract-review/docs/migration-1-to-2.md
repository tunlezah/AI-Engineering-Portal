# Migrating from 1.x to 2.x

**1.x LTS ends 2026-08-18.** After that date `release/1.x` receives no fixes and
the registry marks 1.x `retired`.

## Breaking changes

### 1. `location` → `anchor`

```diff
- "location": { "start": 14832, "end": 14980 }
+ "anchor": "p12/¶3"
```

Character offsets were unstable across parser versions and unverifiable by
reviewers. Anchors are page and paragraph references that a lawyer can check.

**Migration:** if you stored offsets, they cannot be converted; re-run affected
documents. If you rendered offsets in a UI, render `anchor` instead — it is
already human-readable. `harnessctl migrate findings-v1-to-v2 <file>` rewrites
stored findings on a best-effort basis and marks unconvertible entries.

### 2. `raw_chunks` output removed

No consumer used it (verified against the registry consumer graph before
removal). If you depended on it, open an issue — do not pin to 1.x.

### 3. `document-parser` minimum raised to `^3.0.0`

Anchors require parser 3.x. Update your lockfile: `harnessctl lock`.

## Behavioural changes that are not breaking but will move your numbers

- Clause-aware segmentation changes which clauses are found. Expect ~8% more
  findings on the same document, mostly `info` and `low`.
- `UNMATCHED` (added in 2.3.0) introduces `severity: review-required`. Dashboards
  that assume four severities will silently drop these.
- Grounding is now blocking: findings that cannot be evidenced are dropped rather
  than emitted. Your finding count may fall on poorly-parsed documents; check
  `coverage_report.dropped`.

## Recommended sequence

1. Pin `^1.4.0` and confirm your pipeline is green.
2. Move to a 2.x branch in a non-production surface. Run both versions over 50
   representative documents with `harnessctl compare`.
3. Review the diff report with your lawyers — the point is to agree the new
   findings are better, not merely different.
4. Update dashboards for `review-required`.
5. Cut over, then remove the 1.x pin.

Support during migration: `#ai-legal-platform`, or tag `@a.okafor` on your issue
with the label `migration-1-to-2`.
