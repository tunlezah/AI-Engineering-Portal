# HR Policy Q&A

> **Deprecated since 2026-06-01.** Replaced by the knowledge-assistant pattern
> built on `research-brief`. Removal after 2026-12-01. See `docs/migration.md`.

## Overview

Answered employee HR policy questions with citations to policy id, section and
version. Deprecated because the underlying policy index is maintained manually
and the permission model does not distinguish manager-only policies — a
limitation that cannot be fixed inside this architecture.

## Quick Start

```shell
harnessctl run --input question="How much carry-over leave is allowed?" --input country=GB
```

## Configuration

| Key | Default |
|---|---|
| `country` | *(required)* |
| `top_k` | `5` |

## Limitations

UK/IE only; no individual employment advice; index refresh lags publication.

## Support

None. Deprecated. Questions to `#people-tech`.
