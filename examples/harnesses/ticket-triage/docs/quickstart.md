# Quick Start

> Executed by the `harness:smoke` CI job on every pipeline. If this drifts from
> reality, the pipeline goes red.

```shell exec
harnessctl install --lockfile harness.lock
harnessctl doctor
```

Run the bundled example:

```shell exec
harnessctl run --config config/default.yaml --input-file examples/sample.json --out ./out
harnessctl explain ./out --format table
```

See [configuration](configuration.md) for tuning, and the registry page for
evaluation results.
