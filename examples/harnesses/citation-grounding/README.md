# Citation Grounding

> Verifies that every claim in a generated output is supported by quoted, retrievable evidence.

## Overview

The verification half of every RAG-shaped harness in the estate. Given claims and
the source set the generation was allowed to use, it decides — deterministically
where it can, with a model only where it must — whether each claim is actually
supported, and returns the failing span when it is not.

Composed by `contract-review` and available to any harness that emits citations.

## Quick Start

```shell exec
harnessctl install
harnessctl run --input claims=@examples/claims.json --input sources=@examples/sources.json --out ./out
```

## Configuration

| Key | Default | Notes |
|---|---|---|
| `mode` | `exact` | `exact` (string match) or `semantic` (model-judged, slower, less strict) |
| `min_span_tokens` | `6` | Shorter quotations match too easily to be evidence |

## Limitations

Exact matching rejects correct paraphrases by design; absence claims and derived
arithmetic are out of scope.

## Support

`#ai-platform`, business hours.
