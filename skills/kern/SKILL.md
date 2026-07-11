---
name: kern
description: Maintain a lazy, content-addressed KERN IL mirror of a software repository and render cold semantic pages as compact images. Use when an agent must understand a large codebase with less context, refresh changed files, prepare code maps, answer questions from compressed code, or fault exact source back in before editing.
---

# KERN

Treat `.kern/` as a derived, untrusted cache. Repository source remains authoritative.

Resolve `<skill-root>` from this `SKILL.md`; run bundled scripts from there. Use the repository root for `<repo>`.

## Start every code task

1. Scan before reading cached IL:

   ```bash
   python3 <skill-root>/scripts/kern_cache.py --repo <repo> scan
   ```

2. Treat `stale`, `missing`, and `deleted` entries as unusable. Never serve IL whose `source_sha256` differs from the current manifest hash.
3. Select the smallest semantic working set: target files plus direct callers, callees, types, configuration, and tests.

For an explicit repository refresh, use lazy sync or eager deterministic refresh:

```bash
python3 <skill-root>/scripts/kern_cache.py --repo <repo> sync
python3 <skill-root>/scripts/kern_cache.py --repo <repo> sync --eager
```

Eager sync does not spend model tokens. Model enrichment remains JIT.

## JIT a requested file

```bash
python3 <skill-root>/scripts/kern_cache.py --repo <repo> ensure path/to/file
```

`ensure` immediately creates a deterministic baseline IL when the cache is absent or stale. It returns cache paths, the current source hash, and whether model enrichment is needed.

`ensure` accepts `--tier L1|L2|L3` (default from config `default_tier`, `L2`).
L1 = signatures + calls + effects + raises; L2 = + control-flow skeleton;
L3 = + expressions and dataflow. Files below the `min_ir_tokens` floor get a
`mode=source-cheaper` stub — read exact source instead.

When enrichment is needed and a fast compiler subagent is available:

1. Prepare a unique job and retain its JSON output:

   ```bash
   python3 <skill-root>/scripts/kern_cache.py --repo <repo> prepare path/to/file
   ```

2. Give the compiler only the listed source, baseline IL, expected hash, staging path, and [compiler-worker.md](references/compiler-worker.md). For independent files, batch at most three workers. Never run two workers on one file.
3. Commit only after the compiler finishes:

   ```bash
   python3 <skill-root>/scripts/kern_cache.py --repo <repo> commit path/to/file \
     --ir-file <staging-ir> --source-sha <expected-sha>
   ```

   A hash mismatch is a page-fault race. Discard the staging result and repeat `prepare`; never force the commit.
4. Render the committed IL:

   ```bash
   python3 <skill-root>/scripts/kern_cache.py --repo <repo> render path/to/file
   ```

If no compiler subagent is configured, use the deterministic baseline and state that semantic enrichment is pending. See [model-routing.md](references/model-routing.md) for host-specific model configuration.

## Load context by temperature

- **Cold:** keep exact manifest entries and dense lossless-WebP IL pages.
- **Warm:** load textual `.kern-il.txt` for selected symbols or files.
- **Hot:** fault exact current source and pin it through the edit/test cycle.

Prefer the `dense` image profile: 10 px, four columns, lossless WebP. Do not use `ultra` for exact work. If image input is unavailable, use textual IL.

## Fault exact source before edits

Before editing any symbol read from IL or an image, verify its source-map handle:

    python3 <skill-root>/scripts/kern_cache.py --repo <repo> verify path/to/file \
      --symbol <qualified-name> --hash <slice-hash> [--span L<a>-L<b>]

`ok` — proceed. `moved` — same bytes at a new span; use the returned span.
`stale` — the symbol changed; the IL page is invalid, fault exact source.
Lines tagged `!FAULT(reason)` (regex, math, concurrency, elided-literal) may not
support a claim or an edit without an exact-source fault, regardless of verify.

```bash
python3 <skill-root>/scripts/kern_cache.py --repo <repo> fault path/to/file \
  --start <line> --end <line> --expect-sha <manifest-sha>
```

Fault the complete function or class when the edit depends on control flow. Also fault exact source for identifiers, literals, regexes, formulas, security, concurrency, exception matching, macros, generated code, or any IL ambiguity. Never edit from an image or IL alone.

## Refresh after changes

After each successful edit or generated-code step:

1. Run `scan` immediately.
2. Run `ensure` for changed files still in the active working set.
3. Leave other changed files stale; JIT them on first access.
4. Re-enrich and render before reusing a changed file as cold or warm context.
5. Re-fault current source before another edit.

This is KERN's lazy policy: repository-wide hashing, eager deterministic refresh for active changes, and demand-driven model/image work.

## Inspect and recover

```bash
python3 <skill-root>/scripts/kern_cache.py --repo <repo> status
python3 <skill-root>/scripts/kern_cache.py --repo <repo> paths path/to/file
```

If parsing fails, use the generic baseline, record the limitation, and fault raw source. If Pillow is unavailable, retain textual IL and report the renderer dependency rather than installing packages without permission.

The cache mirrors repository paths under:

```text
.kern/
  manifest.json
  config.json
  ir/<source-path>.kern-il.txt
  images/<source-path>/page-*.webp
  jobs/<source-path>.job.json
  staging/...
```

The cache may expose proprietary structure. Its internal `.gitignore` excludes payloads by default. Redact likely credentials and never copy secrets into IL.
