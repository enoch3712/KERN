# Install KERN

KERN ships one canonical skill with native wrappers for Codex, Claude Code, and Cursor. The workflow is shared; each host chooses its own economical compiler model and frontier runtime model.

## Requirements

- Git
- Python 3.10 or newer
- Optional: Pillow for dense WebP rendering (`python3 -m pip install Pillow`)

KERN runs local scripts against repositories. Review third-party plugin code before installing it and use normal host sandbox/approval controls.

## Codex

Add the GitHub marketplace:

```bash
codex plugin marketplace add enoch3712/KERN && codex plugin add kern@kern
```

Then:

1. Restart or refresh the ChatGPT desktop app if KERN does not appear immediately.
2. Start a new task so the installed skill is discovered.
3. Ask: `Use $kern to scan this repository and prepare the smallest semantic working set.`

You can also add the marketplace and install KERN separately from the desktop **Plugins** directory.

Update the marketplace snapshot with:

```bash
codex plugin marketplace upgrade kern
```

### Optional Codex compiler model

The plugin works with deterministic baseline IL even without a subagent. For separate model routing, copy the provided template:

```bash
mkdir -p ~/.codex/agents
curl -fsSL https://raw.githubusercontent.com/enoch3712/KERN/main/templates/codex/kern-compiler.toml \
  -o ~/.codex/agents/kern-compiler.toml
```

Edit `model` and `model_reasoning_effort` in that file to use an economical model available in your workspace. Leave either field commented to inherit it from the parent session; the other custom-agent fields remain independent.

Official references: [Codex custom subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents), [Codex skills](https://developers.openai.com/codex/concepts/customization#skills), and [Codex plugins](https://developers.openai.com/codex/plugins/build).

## Claude Code

Register the marketplace and install the plugin in one shell command:

```bash
claude plugin marketplace add enoch3712/KERN --scope user && claude plugin install kern@kern --scope user
```

Equivalent commands inside Claude Code:

```text
/plugin marketplace add enoch3712/KERN
/plugin install kern@kern
/reload-plugins
```

Scopes:

- `user`: available across your repositories.
- `project`: shared through `.claude/settings.json`.
- `local`: private to the current checkout through `.claude/settings.local.json`.

The plugin includes `agents/claude/kern-compiler.md`, configured with `model: sonnet`. To customize it without editing Claude's versioned plugin cache, create a user-level override:

```bash
mkdir -p ~/.claude/agents
curl -fsSL https://raw.githubusercontent.com/enoch3712/KERN/main/agents/claude/kern-compiler.md \
  -o ~/.claude/agents/kern-compiler.md
```

Change the override's alias or use a full allowed model ID to control the compiler independently from the parent runtime model. User- and project-level agent definitions take precedence over the plugin agent with the same name.

Update with:

```bash
claude plugin marketplace update kern && claude plugin update kern@kern
```

Official references: [discover plugins](https://code.claude.com/docs/en/discover-plugins), [plugin marketplaces](https://code.claude.com/docs/en/plugin-marketplaces), and [plugin reference](https://code.claude.com/docs/en/plugins-reference).

## Cursor

Until KERN is accepted into Cursor's public marketplace, install it as a local plugin:

```bash
git clone --depth 1 https://github.com/enoch3712/KERN.git ~/.cursor/plugins/local/kern
```

Restart Cursor or run `Developer: Reload Window`.

Update with:

```bash
git -C ~/.cursor/plugins/local/kern pull --ff-only
```

The plugin includes `agents/cursor/kern-compiler.md`. It defaults to `model: fast` for economical compilation and permits writes only through the host's normal approval controls. Set `model: inherit` to follow the parent, or use an exact model identifier exposed by your workspace when you need a verifiable route. Availability and fallback behavior depend on client version, plan, and team policy, so confirm the resolved subagent model in Cursor before relying on a cost or fidelity claim.

Cursor also discovers standalone skills from `.agents/skills/`, `.cursor/skills/`, `~/.agents/skills/`, and `~/.cursor/skills/`. For a project-local install, copy `skills/kern` into `.cursor/skills/kern`.

When KERN is listed in the public marketplace, installation becomes `/add-plugin` followed by selecting KERN.

Official references: [Cursor plugins](https://cursor.com/docs/plugins), [plugin reference](https://cursor.com/docs/reference/plugins), [subagent model configuration](https://cursor.com/docs/subagents#model-configuration), and [Agent Skills](https://cursor.com/docs/skills).

## Model routing

The compiler model is replaceable. Use the cheapest model that reliably preserves the language semantics in the current file; escalate subtle metaprogramming, concurrency, generated code, or security-sensitive logic.

```text
changed source
      ↓
economical compiler model
      ↓
KERN IL cache
      ↓
frontier runtime model
```

KERN cache validity depends on the source hash and codec version, not a model name. Switching models can trigger re-enrichment, but never makes stale IL valid.

## Verify an installation

From a test repository, ask the host agent to use KERN or run the baseline directly:

```bash
python3 /path/to/kern/skills/kern/scripts/kern_cache.py --repo . scan
python3 /path/to/kern/skills/kern/scripts/kern_cache.py --repo . ensure path/to/file.py
python3 /path/to/kern/skills/kern/scripts/kern_cache.py --repo . status
```

The first run creates `.kern/`. That directory is derived and should not be committed.
