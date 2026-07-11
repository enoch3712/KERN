# Architecture

KERN applies the compiler/virtual-memory pattern to coding-agent context.

## Invariants

1. Source is authoritative; KERN IL is derived and untrusted.
2. Every cache entry is content-addressed by exact source bytes.
3. Missing or stale files receive a deterministic baseline before optional model enrichment.
4. Only the semantic working set is enriched and loaded.
5. Exact current source must be faulted before an edit.
6. A source change invalidates its prior IL and rendered pages.
7. Every IL symbol carries a contextual semantic handle backed by its exact slice and module dependencies; a passing verify or fresh fault is required before that symbol is edited.

## Lifecycle

```text
scan → hash → invalidate → compile(tiered) → render → page in → verify/fault → write → invalidate
```

The repository-wide scan is cheap. Model work and image rendering are lazy. This is analogous to keeping a page table resident while paging detailed code representations into the active context only when a task touches them.

## Cache layout

```text
.kern/
  config.json
  manifest.json
  ir/<source-path>.kern-il.txt
  images/<source-path>/page-*.webp
  jobs/<source-path>.job.json
  staging/...
```

See [`skills/kern/SKILL.md`](../skills/kern/SKILL.md) for the operational workflow and [`skills/kern/references/compiler-worker.md`](../skills/kern/references/compiler-worker.md) for the enrichment contract.
