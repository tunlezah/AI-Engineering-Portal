# SQL Analyst

> Answers analytical questions over the warehouse by generating, validating and explaining read-only SQL.

## Overview

Generates SQL against the semantic layer, validates it (parse, permission and
cost checks) and executes it read-only under the caller's row-level security
context. The explanation of *how the question was interpreted* matters more than
the SQL: most wrong answers here are right queries to the wrong question.

Currently a pilot. Execution accuracy is 0.78 on the pilot set — usable with
checking, not usable unattended.

## Quick Start

```shell exec
harnessctl install
harnessctl run --input question="Revenue by region last quarter" --input workspace=finance --out ./out
```

## Configuration

| Key | Default | Notes |
|---|---|---|
| `row_limit` | `5000` | Hard cap on returned rows |
| `explain_only` | `false` | Generate and explain without executing |

## Limitations

Finance and commercial marts only; no multi-step analysis; 0.78 execution
accuracy — check results before acting on them.

## Support

`#data-ai`, best effort.
