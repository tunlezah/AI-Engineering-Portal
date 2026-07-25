---
title: How search ranks
weight: 20
summary: The weights are public, and so is the reasoning.
---

Pure text relevance is the wrong ranking for a registry: a perfectly-matching
abandoned experiment should not outrank a slightly-less-matching certified
harness. The score is

```
score = text_relevance                        # BM25-ish, field-boosted
      × lifecycle_multiplier                  # certified 1.5 … retired 0.1
      × (0.7 + 0.3 × quality_score/100)
      × (1 + min(consumers, 10) × 0.03)       # adoption, capped
      × freshness_decay                       # 1.0 ≤180d → 0.6 floor
```

Field boosts: name ×3, tags ×2, summary ×2, domains ×1.5, body ×1.

**Why capped adoption.** Without the cap, the three most-used harnesses would
win every query and nothing new would ever be found — the rich-get-richer
failure that makes internal catalogues feel stale.

**Why freshness decays rather than filters.** A two-year-old harness that still
works should be findable; it just should not beat an equally relevant one that
is maintained.

Weights live in `search-weights.yaml` and are tuned against a labelled query set
(30 queries with expected top-3 results) that runs as a CI check. Ranking changes
get the same evidence discipline we ask of harnesses.
