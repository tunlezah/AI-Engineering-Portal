# Changelog

## [3.2.0] — 2026-07-21
### Added
- Confidence calibration reporting (ECE) in the golden suite.
### Changed
- Default model moved to `claude-haiku-4-5`; category F1 within 2.1pp of Sonnet at a sixth of the cost.

## [3.1.0] — 2026-05-30
### Added
- Known-error article retrieval.

## [3.0.0] — 2026-03-14
### Changed — BREAKING
- `queue` output replaced by the `routing` object (queue, confidence, rationale).
