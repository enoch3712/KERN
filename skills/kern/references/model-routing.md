# Model routing

KERN separates the high-volume compiler from the model that solves the task. The skill never hard-codes a provider model: configure a host-specific `kern-compiler` subagent and keep the deterministic baseline as the fallback.

## Selection rule

- Use a fast, economical model for routine semantic lowering.
- Escalate unusually subtle concurrency, metaprogramming, generated code, or language semantics to a stronger compiler model.
- Keep the frontier runtime focused on architecture, debugging, and implementation.
- Record the actual host/model in committed IL metadata when the host exposes it.

Cache validity depends on source hash plus codec version. Changing compiler models may justify re-enrichment, but never makes stale source valid.

## Codex

Install the plugin, then optionally download [`templates/codex/kern-compiler.toml`](../../../templates/codex/kern-compiler.toml) to `~/.codex/agents/kern-compiler.toml` from the KERN repository. Set `model` and `model_reasoning_effort` to a model available in your workspace. Omit `model` to let Codex route dynamically.

The agent should be read-heavy, workspace-write only for the supplied staging path, and instructed never to edit source.

## Claude Code

The plugin ships [`agents/claude/kern-compiler.md`](../../../agents/claude/kern-compiler.md). Its `model` frontmatter defaults to `sonnet`. Copy that file to `~/.claude/agents/kern-compiler.md` (or the project's `.claude/agents/`) before changing it; the user/project definition overrides the versioned plugin copy.

Claude Code resolves the compiler agent separately from the parent runtime model, so the task-solving model can remain stronger than the compiler.

## Cursor

The plugin ships [`agents/cursor/kern-compiler.md`](../../../agents/cursor/kern-compiler.md). Its `model` defaults to `fast` for economical compilation. Pin an explicit model identifier exposed by your Cursor workspace when you want predictable cost or latency.

Model availability may be constrained by plan, Max Mode, or team policy. Verify the selected model in run metadata when exact routing matters.
