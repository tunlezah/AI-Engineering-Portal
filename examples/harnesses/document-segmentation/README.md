# Document Segmentation

> Structure-aware segmentation of long documents into semantically complete units with anchors.

## Overview

A shared building block. Fixed-size chunking splits obligations, definitions and
conditions across boundaries; this harness segments on document structure and
carries page/paragraph anchors through, so downstream harnesses can cite what
they used. Used by `contract-review` and two other harnesses.

## Quick Start

```shell exec
harnessctl install
harnessctl run --input text=@examples/sample.txt --input profile=legal-clause --out ./out
```

## Configuration

| Key | Default | Notes |
|---|---|---|
| `profile` | *(required)* | `legal-clause`, `policy-section`, `technical-spec`, `generic` |
| `max_tokens` | `900` | Hard cap; longer units are split with continuation links |
| `fallback` | `paragraph` | Used when structure detection fails |

## Limitations

Unstructured documents degrade to paragraph segmentation; tables are not
segmented internally; each document family needs its own profile and evaluation.

## Support

`#ai-platform`, best effort.
