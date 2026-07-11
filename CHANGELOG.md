# Changelog

All notable changes to KERN are documented here.

## 0.2.0 — 2026-07-11

- Implement codec `kern-il/0.2` with deterministic tiered compiler (L1/L2/L3).
- Add computed side effects and exception propagation through the IL.
- Introduce `verify` CLI verb for source-map validation before edits.
- Make stale verification fail shell/JSON gates and reject unsafe cache, manifest,
  log, and renderer paths before filesystem mutation.
- Implement size floor (`min_ir_tokens=600`) with `mode=source-cheaper` stubs.
- Pin the tested optional JavaScript/TypeScript parser set and exercise it in PR CI.
- Add per-tier token benchmarks with independent signature, call, effect, raise,
  unknown-call, tier-marker, and source-handle fidelity checks.

## 0.1.1 — 2026-07-11

- Enforce codec-version invalidation and reject stale rendered pages.
- Verify cached IR digests and exact compiler-worker headers before reuse or commit.
- Remove private pilot artifacts and identifying benchmark metadata from the public history.
- Add one-command Codex installation and portable compiler-agent overrides.
- Refactor the README and website around measured compression evidence, a concrete compiler preview, and provider-first installation docs.
- Move machine-readable pilot data under `benchmarks/` and publish the full-loop limitations beside the representation result.

## 0.1.0 — 2026-07-11

- Publish the portable KERN skill and deterministic cache runtime.
- Add Codex, Claude Code, and Cursor plugin packaging.
- Add lazy hash invalidation, JIT semantic-IL enrichment, dense-page rendering, and exact-source write gates.
- Publish the landing page, installation guide, architecture notes, and measured pilot data.
