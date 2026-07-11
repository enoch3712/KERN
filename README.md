# KERN

**Compile code for machine attention.**

KERN maintains a lazy, content-addressed intermediate-language mirror of a repository. Coding agents keep compact semantic context available, compile only changed or newly relevant files, and fault exact source back in before edits.

[Website](https://enoch3712.github.io/KERN/) · [Installation guide](docs/install.md) · [Architecture](docs/architecture.md) · [Changelog](CHANGELOG.md) · [Benchmark data](public/benchmark-results.json)

## Install

### Codex

```bash
codex plugin marketplace add enoch3712/KERN && codex plugin add kern@kern
```

Then start a new task. KERN can also be managed from the desktop plugin directory.

### Claude Code

```bash
claude plugin marketplace add enoch3712/KERN --scope user && claude plugin install kern@kern --scope user
```

### Cursor

```bash
git clone --depth 1 https://github.com/enoch3712/KERN.git ~/.cursor/plugins/local/kern
```

Restart Cursor or run `Developer: Reload Window`.

See [docs/install.md](docs/install.md) for project-local installs, updates, model selection, and host-specific caveats.

## What it does

```text
source repository
      ↓ hash + invalidate
language-aware baseline
      ↓ economical compiler model
KERN IL mirror
      ↓ task-selected page fault
runtime model context
      ↓ exact-source gate
verified edit
```

- Hashes the repository and rejects stale IL.
- Creates deterministic baseline IL immediately.
- Enriches only files entering the semantic working set.
- Renders cold IL as compact lossless-WebP pages.
- Mirrors source paths under `.kern/`.
- Requires the current exact source and matching hash before writes.

KERN is not a replacement for source code, parsing, retrieval, or tests. The IL and image layers are derived, untrusted context representations.

## Status

KERN is an experimental open-source prototype. The included pilot measured a 3,704-line Python file across raw source, semantic IL, and dense visual pages. The observed representation estimate was approximately `36,674 → 5,795 → 2,877` tokens; full agent-loop cost is larger and workload-dependent.

## Repository map

```text
.agents/plugins/       Codex marketplace
.codex-plugin/         Codex plugin manifest
.claude-plugin/        Claude Code marketplace + manifest
.cursor-plugin/        Cursor plugin manifest
skills/kern/           Canonical portable skill
agents/                Host-specific compiler subagents
templates/codex/       Optional Codex compiler-agent template
app/                   Website and /docs route
docs/                  Markdown documentation
```

## Development

```bash
npm install
npm run dev
npm run build
python3 skills/kern/scripts/kern_cache.py --repo . scan
```

See [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

## License and marks

Code and documentation are licensed under [Apache-2.0](LICENSE). Product and language marks belong to their respective owners. Compatibility references do not imply endorsement.
