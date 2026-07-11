"use client";

import { useEffect, useRef, useState } from "react";
import styles from "./docs.module.css";

const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";
const withBasePath = (path) => `${basePath}${path}`;

const providers = [
  {
    id: "codex",
    name: "Codex",
    logo: "/logos/openai.svg",
    lightLogo: true,
    badge: "Marketplace",
    install:
      "codex plugin marketplace add enoch3712/KERN && codex plugin add kern@kern",
    summary:
      "Install the marketplace once, then use the same KERN skill from the Codex app or CLI.",
    compiler: "Dynamic by default",
    compilerDetail: "Pin a model only when cost or latency must be predictable.",
    runtime: "Current Codex task model",
    runtimeDetail: "The model already selected for architecture, debugging, and implementation.",
    defaultRouting: "Leave model commented in the compiler profile to let Codex route dynamically.",
    location: "Managed by Codex; optional override at ~/.codex/agents/kern-compiler.toml",
    configure:
      "mkdir -p ~/.codex/agents && curl -fsSL https://raw.githubusercontent.com/enoch3712/KERN/main/templates/codex/kern-compiler.toml -o ~/.codex/agents/kern-compiler.toml",
    verify: "codex plugin list",
    use: "Use $kern to scan this repository and prepare the smallest semantic working set.",
    update: "codex plugin marketplace upgrade kern",
    uninstall: "codex plugin remove kern",
  },
  {
    id: "claude",
    name: "Claude Code",
    logo: "/logos/claude.svg",
    badge: "User plugin",
    install:
      "claude plugin marketplace add enoch3712/KERN --scope user && claude plugin install kern@kern --scope user",
    summary:
      "The user-scoped plugin is available across repositories. Reload plugins if the current session was already open.",
    compiler: "Sonnet alias",
    compilerDetail: "The bundled compiler agent starts with model: sonnet.",
    runtime: "Current Claude Code model",
    runtimeDetail: "The parent session remains responsible for reasoning and edits.",
    defaultRouting: "Override the compiler agent at user or project scope without changing the plugin cache.",
    location: "Managed by Claude Code; optional override at ~/.claude/agents/kern-compiler.md",
    configure:
      "mkdir -p ~/.claude/agents && curl -fsSL https://raw.githubusercontent.com/enoch3712/KERN/main/agents/claude/kern-compiler.md -o ~/.claude/agents/kern-compiler.md",
    verify: "claude plugin list",
    use: "Use KERN to scan this repository and prepare the smallest semantic working set.",
    update: "claude plugin marketplace update kern && claude plugin update kern@kern",
    uninstall: "claude plugin uninstall kern@kern --scope user",
  },
  {
    id: "cursor",
    name: "Cursor",
    logo: "/logos/cursor.svg",
    badge: "Local plugin",
    install:
      "git clone --depth 1 https://github.com/enoch3712/KERN.git ~/.cursor/plugins/local/kern",
    summary:
      "Install locally until KERN is listed in Cursor's public marketplace, then reload the Cursor window.",
    compiler: "Fast alias",
    compilerDetail: "The bundled agent starts with model: fast; use inherit or an exact supported slug when you need a verifiable route.",
    runtime: "Current Cursor model",
    runtimeDetail: "Use any runtime model exposed by your workspace and plan.",
    defaultRouting: "Cursor routing varies by version, plan, and policy. Verify the resolved subagent model in the host before relying on cost or fidelity.",
    location: "~/.cursor/plugins/local/kern",
    configure: "$EDITOR ~/.cursor/plugins/local/kern/agents/cursor/kern-compiler.md",
    verify:
      "test -f ~/.cursor/plugins/local/kern/.cursor-plugin/plugin.json && echo \"KERN installed\"",
    use: "Use KERN to scan this repository and prepare the smallest semantic working set.",
    update: "git -C ~/.cursor/plugins/local/kern pull --ff-only",
    uninstall: "rm -rf ~/.cursor/plugins/local/kern",
  },
];

const workflow = [
  ["01", "Hash source", "Changed bytes receive a new identity; unchanged pages remain cached."],
  ["02", "Compile lazily", "Only changed or task-relevant files become compact KERN IL."],
  ["03", "Load the working set", "The runtime receives the map and the few semantic pages it needs."],
  ["04", "Fault exact truth", "Current source returns and its hash is checked before every edit."],
];

const safetyRules = [
  "Source is authoritative. KERN IL and dense pages are derived, disposable cache entries.",
  "Validity follows the source SHA-256, codec version, and compiler fingerprint—not the name of a model.",
  "Credentials and high-entropy secrets are redacted from derived representations.",
  "A write changes the source hash and invalidates every prior representation of that file.",
];

function Mark() {
  return (
    <span className={styles.mark} aria-hidden="true">
      <i />
      <i />
      <i />
    </span>
  );
}

function Arrow() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M4 10h12m0 0-5-5m5 5-5 5" />
    </svg>
  );
}

function CopyIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <rect x="7" y="7" width="9" height="9" rx="1.5" />
      <path d="M13 7V5.5A1.5 1.5 0 0 0 11.5 4h-7A1.5 1.5 0 0 0 3 5.5v7A1.5 1.5 0 0 0 4.5 14H7" />
    </svg>
  );
}

function moveProviderFocus(event, providers, activeId, onChange) {
  const ids = providers.map((item) => item.id);
  const current = ids.indexOf(activeId);
  let next = current;

  if (["ArrowRight", "ArrowDown"].includes(event.key)) next = (current + 1) % ids.length;
  else if (["ArrowLeft", "ArrowUp"].includes(event.key)) next = (current - 1 + ids.length) % ids.length;
  else if (event.key === "Home") next = 0;
  else if (event.key === "End") next = ids.length - 1;
  else return;

  event.preventDefault();
  const nextId = ids[next];
  const tablist = event.currentTarget.closest('[role="tablist"]');
  onChange(nextId);
  window.requestAnimationFrame(() => tablist?.querySelector(`[data-provider-id="${nextId}"]`)?.focus());
}

export default function DocsPage() {
  const [providerId, setProviderId] = useState("codex");
  const [copied, setCopied] = useState(false);
  const copiedTimer = useRef(null);
  const provider = providers.find((item) => item.id === providerId) || providers[0];

  useEffect(() => {
    document.title = "Documentation — KERN";

    const url = new URL(window.location.href);
    const queryProvider = url.searchParams.get("provider")?.replace("claude-code", "claude");
    const hashProvider = url.hash.replace(/^#/, "").replace("claude-code", "claude");
    const requested = queryProvider || hashProvider;

    if (providers.some((item) => item.id === requested)) {
      setProviderId(requested);

      if (!queryProvider) {
        url.searchParams.set("provider", requested);
        url.hash = "";
        window.history.replaceState({}, "", `${url.pathname}${url.search}`);
      }
    }
  }, []);

  useEffect(() => () => {
    if (copiedTimer.current) window.clearTimeout(copiedTimer.current);
  }, []);

  const selectProvider = (id) => {
    setProviderId(id);
    setCopied(false);

    const url = new URL(window.location.href);
    url.searchParams.set("provider", id);
    if (["codex", "claude", "claude-code", "cursor"].includes(url.hash.replace(/^#/, ""))) {
      url.hash = "";
    }
    const nextUrl = `${url.pathname}${url.search}${url.hash}`;
    window.history.replaceState({}, "", nextUrl);
  };

  const copyInstall = async () => {
    try {
      await navigator.clipboard.writeText(provider.install);
    } catch {
      const textarea = document.createElement("textarea");
      textarea.value = provider.install;
      textarea.setAttribute("readonly", "");
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      document.body.removeChild(textarea);
    }

    setCopied(true);
    if (copiedTimer.current) window.clearTimeout(copiedTimer.current);
    copiedTimer.current = window.setTimeout(() => setCopied(false), 1800);
  };

  return (
    <main className={styles.page}>
      <header className={styles.topbar}>
        <a className={styles.brand} href={withBasePath("/")} aria-label="KERN home">
          <Mark />
          <span>KERN</span>
          <span className={styles.slash}>/</span>
          <span className={styles.docsWord}>DOCS</span>
        </a>
        <nav aria-label="Documentation navigation">
          <a href="#install">Install</a>
          <a href="#architecture">Architecture</a>
          <a href="#evidence">Evidence</a>
        </nav>
        <a
          className={styles.githubLink}
          href="https://github.com/enoch3712/KERN"
          target="_blank"
          rel="noreferrer"
        >
          GitHub <Arrow />
        </a>
      </header>

      <nav className={styles.mobileDocNav} aria-label="Mobile documentation navigation">
        <a href="#install">Install</a>
        <a href="#architecture">Architecture</a>
        <a href="#evidence">Evidence</a>
        <a href="#safety">Safety</a>
      </nav>

      <article className={styles.content}>
        <section className={styles.intro} id="install" aria-labelledby="docs-title">
          <p className={styles.kicker}>KERN / QUICK START</p>
          <h1 id="docs-title">Install for your environment.</h1>
          <p>
            KERN is built for codebases too large to keep raw in a model window.
            Choose a provider to see only the command, routing defaults, and
            maintenance details that apply.
          </p>
          <div className={styles.requirements} aria-label="Requirements">
            <span>Requires</span>
            <code>Git</code>
            <code>Python 3.10+</code>
            <span>Pillow optional</span>
          </div>
        </section>

        <section className={styles.providerSection} aria-label="Provider installation">
          <div className={styles.providerTabs} role="tablist" aria-label="Choose an environment">
            {providers.map((item) => (
              <button
                className={item.id === providerId ? styles.activeProvider : ""}
                id={`provider-tab-${item.id}`}
                key={item.id}
                type="button"
                role="tab"
                data-provider-id={item.id}
                aria-selected={item.id === providerId}
                aria-controls="provider-panel"
                tabIndex={item.id === providerId ? 0 : -1}
                onClick={() => selectProvider(item.id)}
                onKeyDown={(event) => moveProviderFocus(event, providers, providerId, selectProvider)}
              >
                <span className={`${styles.providerLogo} ${item.lightLogo ? styles.lightLogo : ""}`}>
                  <img src={withBasePath(item.logo)} alt="" />
                </span>
                <span>
                  <strong>{item.name}</strong>
                  <small>{item.badge}</small>
                </span>
              </button>
            ))}
          </div>

          <div
            className={styles.providerPanel}
            id="provider-panel"
            role="tabpanel"
            aria-labelledby={`provider-tab-${provider.id}`}
          >
            <div className={styles.providerHeading}>
              <div>
                <p className={styles.eyebrow}>QUICK INSTALL</p>
                <h2>{provider.name}</h2>
              </div>
              <span className={styles.badge}>{provider.badge}</span>
            </div>
            <p className={styles.providerSummary}>{provider.summary}</p>

            <div className={styles.installCommand}>
              <div className={styles.commandTop}>
                <span>Terminal</span>
                <span>$</span>
              </div>
              <div className={styles.commandRow}>
                <code>{provider.install}</code>
                <button type="button" onClick={copyInstall} aria-label={`Copy ${provider.name} install command`}>
                  <CopyIcon />
                  <span aria-live="polite">{copied ? "Copied" : "Copy"}</span>
                </button>
              </div>
            </div>

            <div className={styles.modelRoute} aria-label={`${provider.name} KERN model routing`}>
              <div>
                <span>Compiler</span>
                <strong>{provider.compiler}</strong>
                <p>{provider.compilerDetail}</p>
              </div>
              <Arrow />
              <div className={styles.kernNode}>
                <span>Shared representation</span>
                <strong>KERN IL</strong>
                <p>Compact · cached · source-addressed</p>
              </div>
              <Arrow />
              <div>
                <span>Runtime</span>
                <strong>{provider.runtime}</strong>
                <p>{provider.runtimeDetail}</p>
              </div>
            </div>
            <p className={styles.routingDefault}>
              <strong>Default routing.</strong> {provider.defaultRouting}
            </p>

            <dl className={styles.providerDetails}>
              <div className={styles.wideDetail}>
                <dt>Install location</dt>
                <dd><code>{provider.location}</code></dd>
              </div>
              <div className={styles.wideDetail}>
                <dt>Configure compiler</dt>
                <dd><code>{provider.configure}</code></dd>
              </div>
              <div>
                <dt>Verify</dt>
                <dd><code>{provider.verify}</code></dd>
              </div>
              <div>
                <dt>First request</dt>
                <dd>{provider.use}</dd>
              </div>
              <div>
                <dt>Update</dt>
                <dd><code>{provider.update}</code></dd>
              </div>
              <div>
                <dt>Uninstall</dt>
                <dd><code>{provider.uninstall}</code></dd>
              </div>
            </dl>
          </div>

          <noscript>
            <div className={styles.noScriptProviders}>
              <p>JavaScript is off, so every provider is shown below.</p>
              {providers.map((item) => (
                <section key={item.id} id={item.id === "claude" ? "claude-code" : item.id}>
                  <h2>{item.name}</h2>
                  <code>{item.install}</code>
                  <p><strong>Verify:</strong> <code>{item.verify}</code></p>
                </section>
              ))}
            </div>
          </noscript>
        </section>

        <section className={styles.commonSection} id="architecture" aria-labelledby="architecture-title">
          <div className={styles.sectionHeading}>
            <p className={styles.eyebrow}>COMMON TO EVERY PROVIDER</p>
            <h2 id="architecture-title">Compile the map. Page in the truth.</h2>
            <p>
              Provider configuration changes the models, not the protocol. Every host
              follows the same content-addressed lifecycle.
            </p>
          </div>
          <ol className={styles.workflow}>
            {workflow.map(([number, title, body]) => (
              <li key={number}>
                <span>{number}</span>
                <div>
                  <strong>{title}</strong>
                  <p>{body}</p>
                </div>
              </li>
            ))}
          </ol>
        </section>

        <section className={styles.commonSection} id="evidence" aria-labelledby="evidence-title">
          <div className={styles.sectionHeading}>
            <p className={styles.eyebrow}>MEASURED PILOT / HONEST LIMITS</p>
            <h2 id="evidence-title">Smaller representation, measured separately from the loop.</h2>
            <p>
              One 3,704-line Python pilot produced a 12.75× smaller selected
              representation. That number does not include system instructions,
              tools, reasoning, or repeated turns.
            </p>
          </div>
          <div className={styles.benchmarkCard}>
            <div><span>Raw source</span><strong>36,674</strong><small>text tokens est.</small></div>
            <b aria-hidden="true">→</b>
            <div><span>KERN IL</span><strong>5,795</strong><small>text tokens est.</small></div>
            <b aria-hidden="true">→</b>
            <div><span>Dense pages</span><strong>2,877</strong><small>image tokens est.</small></div>
            <aside>
              <strong>End-to-end receipt</strong>
              <p>18,107 uncached input tokens across four turns; 73,403 cumulative. Ultra drifted, while Safe cost more than IL text.</p>
              <nav aria-label="Benchmark resources">
                <a href="https://github.com/enoch3712/KERN/tree/main/benchmarks" target="_blank" rel="noreferrer">Methodology <Arrow /></a>
                <a href="https://github.com/enoch3712/KERN/blob/main/benchmarks/results/python-pilot-v1.json" target="_blank" rel="noreferrer">Raw record <Arrow /></a>
              </nav>
            </aside>
          </div>
        </section>

        <section className={styles.commonSection} id="safety" aria-labelledby="safety-title">
          <div className={styles.sectionHeading}>
            <p className={styles.eyebrow}>SOURCE-VERIFIED BY DESIGN</p>
            <h2 id="safety-title">Compression is never authority.</h2>
            <p>
              KERN reduces the representation used for discovery and reasoning while
              keeping a verified route back to exact code.
            </p>
          </div>
          <div className={styles.safetyGrid}>
            <ul>
              {safetyRules.map((rule) => <li key={rule}>{rule}</li>)}
            </ul>
            <div className={styles.cachePanel}>
              <span>DERIVED CACHE</span>
              <pre tabIndex="0"><code>{`.kern/
  config.json
  manifest.json
  ir/<source-path>.kern-il.txt
  images/<source-path>/page-*.webp
  jobs/<source-path>.job.json`}</code></pre>
              <p>Add <code>.kern/</code> to <code>.gitignore</code>. Delete it at any time; source remains untouched.</p>
            </div>
          </div>
        </section>

        <aside className={styles.securityNote}>
          <div>
            <strong>Review before installing</strong>
            <p>KERN runs local scripts against repositories you place in scope.</p>
          </div>
          <p>Keep your host&apos;s normal sandbox, approval, and source-control protections enabled.</p>
          <a href="https://github.com/enoch3712/KERN/tree/main/skills/kern" target="_blank" rel="noreferrer">
            Inspect the skill <Arrow />
          </a>
        </aside>

        <footer className={styles.footer}>
          <div>
            <Mark />
            <p><strong>KERN</strong> — compile code for machine attention.</p>
          </div>
          <nav aria-label="Footer navigation">
            <a href={withBasePath("/")}>Home</a>
            <a href="https://github.com/enoch3712/KERN">GitHub</a>
            <a href="https://github.com/enoch3712/KERN/blob/main/docs/install.md">Install source</a>
            <a href="https://github.com/enoch3712/KERN/blob/main/LICENSE">Apache-2.0</a>
          </nav>
        </footer>
      </article>
    </main>
  );
}
