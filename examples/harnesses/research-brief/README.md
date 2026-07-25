# Research Brief

> **Experimental.** No evaluation suite. Do not build a skill on this yet.

## Overview

Sweeps several internal corpora and produces a sourced brief on a topic. The
interesting part is the rejected-sources list: it shows what was consulted and
discarded, which is what makes a brief reviewable rather than merely readable.

## Quick Start

```shell
harnessctl run --input topic="Adoption of the semantic layer" --input depth=standard
```

## Limitations

No evaluations; experimental plugin dependency; highly variable cost; no
cross-corpus deduplication.

## Support

None. `#knowledge-ai` for questions.
