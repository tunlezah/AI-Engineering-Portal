# Changelog

All notable changes to this harness. Format: [Keep a Changelog]; versioning:
[SemVer applied to the harness contract](https://registry.pages.acme.internal/docs/versioning/).

The **contract** is: `spec.interfaces`, `config/schema.json`, declared guardrail
behaviour, and required plugin ranges. A change that forces a consumer to alter
their integration or re-baseline their expectations is a MAJOR change.

## [Unreleased]

### Added
- German clause segmentation behind `experimental.de_segmentation` (`#412`).

## [2.3.1] — 2026-07-19

### Fixed
- Evidence quotations no longer include trailing footnote markers, which caused
  exact-match grounding to reject otherwise valid findings (`gc-0131`).
- Timeout raised for documents over 150 pages; `gc-0179` now passes.

### Evaluation
- Golden pass rate 0.954 → 0.962; grounding 0.991 → 0.994. No regressions.

## [2.3.0] — 2026-06-24

### Added
- `UNMATCHED` handling: clauses with no playbook position are reported with
  `severity: review-required` instead of being forced onto the nearest position.
  This surfaced 14 genuine playbook gaps in the first fortnight.
- Coverage report output (`coverage_report`), listing assessed, unmatched and
  skipped clauses.

### Changed
- System prompt 2.2.0 → 2.3.0. **Behavioural change**: findings that previously
  received a low-confidence position now appear as `review-required`. Consumers
  filtering on severity should expect a new value. Non-breaking per the contract
  (the enum was already documented) but re-baseline your dashboards.

## [2.2.0] — 2026-05-02

### Added
- `severity_profile: conservative` for tier-1 counterparties.
- Adversarial evaluation suite (prompt injection, advice leakage, redaction bypass).

### Security
- `pii-redactor` moved from optional to **required**. Runs without redaction are
  no longer possible.

## [2.0.0] — 2026-02-18

### Changed — BREAKING
- Output `findings[].location` (character offsets) replaced by
  `findings[].anchor` (page/paragraph). Offsets were not verifiable by reviewers.
  **Migration:** [docs/migration-1-to-2.md](docs/migration-1-to-2.md).
- Clause-aware segmentation replaces fixed-size chunking
  ([ADR-003](architecture/decisions/ADR-003-clause-segmentation.md)).
- Minimum `document-parser` raised to `^3.0.0` for anchor support.

### Removed
- `raw_chunks` output. No consumer used it; confirmed via the registry's
  consumer graph before removal.

## [1.4.2] — 2025-12-09

Final 1.x release. **1.x is LTS until 2026-08-18** (security and correctness
fixes only); `release/1.x` branch. See the deprecation notice on the registry page.

[Keep a Changelog]: https://gitlab.acme.internal/ai-platform/docs/keep-a-changelog
