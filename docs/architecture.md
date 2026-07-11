# Architecture

KERN applies the compiler/virtual-memory pattern to coding-agent context.

## Invariants

1. Source is authoritative; KERN IL is derived and untrusted.
2. Every cache entry is content-addressed by exact source bytes.
3. Missing or stale files receive a deterministic baseline before optional model enrichment.
4. Only the semantic working set is enriched and loaded.
5. Exact current source must be faulted before an edit.
6. A source change invalidates its prior IL and rendered pages.

## Lifecycle

```text
scan → hash → invalidate → baseline → enrich → render → page in → exact fault → write → invalidate
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
