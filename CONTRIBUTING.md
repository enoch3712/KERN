# Contributing

Thanks for helping improve KERN.

## Before opening a pull request

1. Keep source authoritative and treat `.kern/` as disposable output.
2. Preserve hash checks and the exact-source write gate.
3. Do not add benchmark answer keys, credentials, or proprietary source artifacts.
4. Keep host-specific model identifiers out of the canonical skill workflow.
5. Add or update tests when changing cache behavior or rendering.

Run:

```bash
npm ci
npm run build
python3 -m py_compile skills/kern/scripts/kern_cache.py skills/kern/scripts/render_ir.py
python3 skills/kern/scripts/kern_cache.py --repo . scan
```

Use focused commits and explain behavioral, compatibility, or benchmark implications in the pull request.

## Repository layout

```text
.agents/plugins/       Codex marketplace metadata
.codex-plugin/         Codex plugin manifest
.claude-plugin/        Claude Code marketplace and manifest
.cursor-plugin/        Cursor plugin manifest
skills/kern/           Canonical portable skill and cache runtime
agents/                Host-specific compiler workers
templates/codex/       Optional Codex compiler-agent template
benchmarks/             Human methodology and machine-readable results
app/                    Website and documentation route
docs/                   Markdown installation and architecture guides
```

## Benchmark contributions

Benchmarks must separate representation estimates from complete agent-loop usage. Document the source shape, redaction policy, tokenizer or image-accounting method, model and codec versions, fidelity checks, limitations, and any reproduction gap. Keep machine-readable results under `benchmarks/results/` and link them from a human-readable methodology page.

Do not commit private source, answer keys, credentials, or identifying metadata. A result that cannot be reproduced byte-for-byte because its source is private must say so explicitly.
