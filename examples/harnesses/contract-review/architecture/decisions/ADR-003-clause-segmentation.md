# ADR-003: Clause-aware segmentation instead of fixed-size chunking

- **Status:** Accepted
- **Date:** 2026-02-11
- **Deciders:** @a.okafor, @s.lindqvist, Legal Ops (S. Bahri)
- **Supersedes:** ADR-001 (fixed 800-token chunks with 15% overlap)

## Context

v1 chunked contracts into fixed 800-token windows. Two failure modes dominated
error analysis on the golden suite (n=180):

1. **Split obligations.** Liability caps and their carve-outs routinely straddle
   a chunk boundary. The model assessed the cap without the carve-out and
   produced confidently wrong `low` severity findings — the worst possible error
   for this use case.
2. **Unciteable evidence.** Chunk offsets do not map to anything a lawyer can
   check. Reviewers could not verify findings without re-reading the contract,
   which removed most of the time saving.

## Decision

Segment on clause boundaries using the `document-segmentation` harness with the
`legal-clause` profile: numbering patterns, heading heuristics and paragraph
anchors from the parser, with a hard maximum of 900 tokens per clause and
explicit continuation linking for clauses that exceed it.

Each segment carries the page and paragraph anchor emitted by `document-parser`,
which becomes the citation shown to the reviewer.

## Consequences

**Positive**

- Clause classification F1 rose 0.84 → 0.91 on the golden suite.
- Findings became verifiable: every one is a click-through to a page and
  paragraph. Reviewer acceptance rose from 61% to 88% in the pilot.
- Retrieval quality improved because the query is a semantically complete clause.

**Negative**

- Segmentation is now a dependency on another harness, adding a version edge and
  a coupling we must manage (`document-segmentation ^4.0.0`).
- Badly formatted third-party paper (no numbering, inconsistent headings) falls
  back to paragraph segmentation, where quality is ~0.79 F1. Tracked as `#401`.
- Segmentation adds ~4 s p50 to the run.

**Rejected alternatives**

- *Semantic chunking with embedding-similarity boundaries:* better than fixed
  windows, but boundaries still did not coincide with legal units, and the
  boundaries were not explainable to a lawyer.
- *Whole-document long context:* simplest option and briefly tempting at 200k
  context. Rejected because per-clause coverage reporting becomes impossible,
  cost per document rose 6×, and grounding checks lost their anchor targets.
