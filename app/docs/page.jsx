import styles from "./docs.module.css";

export const metadata = {
  title: "Documentation — KERN",
  description:
    "Install KERN for Codex, Claude Code, or Cursor and learn how its content-addressed intermediate-language cache works.",
};

const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";

const withBasePath = (path) => `${basePath}${path}`;

const installs = [
  {
    id: "codex",
    name: "Codex",
    logo: "/logos/openai.svg",
    logoClass: styles.lightLogo,
    badge: "Marketplace",
    command:
      "codex plugin marketplace add enoch3712/KERN && codex plugin add kern@kern",
    detail: (
      <>
        Start a new task after installation. You can also manage KERN from the
        desktop <strong>Plugins</strong> directory. Invoke it with <code>$kern</code>.
      </>
    ),
    update: "codex plugin marketplace upgrade kern",
    model: (
      <>
        Copy <code>templates/codex/kern-compiler.toml</code> into{" "}
        <code>~/.codex/agents/</code> to pin an economical compiler model, or
        leave the model unset for dynamic routing.
      </>
    ),
  },
  {
    id: "claude-code",
    name: "Claude Code",
    logo: "/logos/claude.svg",
    badge: "One command",
    command:
      "claude plugin marketplace add enoch3712/KERN --scope user && claude plugin install kern@kern --scope user",
    detail: (
      <>
        Run <code>/reload-plugins</code> if the plugin is not discovered
        immediately. Use <code>--scope project</code> to share the configuration
        through the repository.
      </>
    ),
    update:
      "claude plugin marketplace update kern && claude plugin update kern@kern",
    model: (
      <>
        Edit <code>agents/claude/kern-compiler.md</code>. Its compiler defaults to
        the <code>sonnet</code> alias and remains independent of the parent runtime
        model.
      </>
    ),
  },
  {
    id: "cursor",
    name: "Cursor",
    logo: "/logos/cursor.svg",
    badge: "Local plugin",
    command:
      "git clone --depth 1 https://github.com/enoch3712/KERN.git ~/.cursor/plugins/local/kern",
    detail: (
      <>
        Restart Cursor or run <strong>Developer: Reload Window</strong>. For a
        project-local skill, copy <code>skills/kern</code> into{" "}
        <code>.cursor/skills/kern</code>.
      </>
    ),
    update: "git -C ~/.cursor/plugins/local/kern pull --ff-only",
    model: (
      <>
        Edit <code>agents/cursor/kern-compiler.md</code>. It uses{" "}
        <code>model: fast</code> by default; pin any compiler model exposed by
        your workspace when cost or latency must be predictable.
      </>
    ),
  },
];

const lifecycle = [
  ["01", "Scan", "Walk the repository and read its lightweight page table."],
  ["02", "Hash", "Address every entry by the exact bytes of its source file."],
  ["03", "Compile", "Create deterministic baseline IL; enrich only the working set."],
  ["04", "Render", "Optionally pack cold IL into compact visual pages."],
  ["05", "Page in", "Load only the semantic context required by the active task."],
  ["06", "Fault truth", "Retrieve exact current source before any edit is made."],
  ["07", "Invalidate", "A write changes the hash and expires prior IL and pages."],
];

const invariants = [
  "Source is authoritative; KERN IL is derived and untrusted.",
  "Cache validity follows source bytes and codec version—not a model name.",
  "Missing or stale entries receive a deterministic baseline before enrichment.",
  "Exact current source is always faulted before an edit.",
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

export default function DocsPage() {
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
          <a href="#routing">Model routing</a>
          <a href="#architecture">Architecture</a>
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

      <div className={styles.shell}>
        <aside className={styles.sidebar} aria-label="On this page">
          <p>Start here</p>
          <a href="#install">Install KERN</a>
          <a href="#codex">Codex</a>
          <a href="#claude-code">Claude Code</a>
          <a href="#cursor">Cursor</a>
          <p>Concepts</p>
          <a href="#routing">Model routing</a>
          <a href="#architecture">Architecture</a>
          <a href="#verify">Verify</a>
          <div className={styles.sidebarRule} />
          <a href={withBasePath("/")}>← Landing page</a>
          <a href="https://github.com/enoch3712/KERN/tree/main/docs">Source docs ↗</a>
        </aside>

        <article className={styles.content}>
          <section className={styles.intro} aria-labelledby="docs-title">
            <div className={styles.kicker}>KERN / DOCUMENTATION / 01</div>
            <h1 id="docs-title">Compile the repository.<br />Page in the truth.</h1>
            <p>
              KERN gives coding agents a compact, content-addressed semantic mirror
              of a repository. One canonical skill runs across Codex, Claude Code,
              and Cursor; each host may choose its own economical compiler and
              frontier runtime model.
            </p>
            <div className={styles.introActions}>
              <a className={styles.primaryButton} href="#install">
                Install KERN <Arrow />
              </a>
              <a
                className={styles.secondaryButton}
                href="https://github.com/enoch3712/KERN"
                target="_blank"
                rel="noreferrer"
              >
                View repository
              </a>
            </div>
            <div className={styles.requirements}>
              <span>Requirements</span>
              <code>Git</code>
              <code>Python 3.10+</code>
              <code>Pillow optional</code>
              <span>Local-first</span>
            </div>
          </section>

          <section className={styles.section} id="install" aria-labelledby="install-title">
            <div className={styles.sectionHeading}>
              <span className={styles.step}>01</span>
              <div>
                <p className={styles.eyebrow}>Install</p>
                <h2 id="install-title">Choose your environment.</h2>
                <p>
                  The workflow is identical across hosts. Installation and compiler
                  model configuration are the only host-specific pieces.
                </p>
              </div>
            </div>

            <div className={styles.installList}>
              {installs.map((item) => (
                <section className={styles.installCard} id={item.id} key={item.id}>
                  <div className={styles.installHead}>
                    <span className={`${styles.productLogo} ${item.logoClass || ""}`}>
                      <img src={withBasePath(item.logo)} alt="" />
                    </span>
                    <div>
                      <span className={styles.installLabel}>Environment</span>
                      <h3>{item.name}</h3>
                    </div>
                    <span className={styles.badge}>{item.badge}</span>
                  </div>

                  <div className={styles.commandBlock}>
                    <div className={styles.commandTop}>
                      <span>Quick install</span>
                      <span aria-hidden="true">$</span>
                    </div>
                    <pre tabIndex="0"><code>{item.command}</code></pre>
                  </div>

                  <p className={styles.installDetail}>{item.detail}</p>
                  <div className={styles.installMeta}>
                    <div>
                      <span>Compiler model</span>
                      <p>{item.model}</p>
                    </div>
                    <div>
                      <span>Update</span>
                      <code>{item.update}</code>
                    </div>
                  </div>
                </section>
              ))}
            </div>

            <aside className={styles.note}>
              <strong>Before installing third-party code</strong>
              <p>
                Review the repository and keep your host&apos;s normal sandbox and
                approval controls enabled. KERN runs local scripts against the
                repository you place in scope.
              </p>
            </aside>
          </section>

          <section className={styles.section} id="routing" aria-labelledby="routing-title">
            <div className={styles.sectionHeading}>
              <span className={styles.step}>02</span>
              <div>
                <p className={styles.eyebrow}>Model routing</p>
                <h2 id="routing-title">Separate compression from reasoning.</h2>
                <p>
                  The model that compiles high-volume context does not need to be
                  the model that solves the task. KERN makes that boundary explicit.
                </p>
              </div>
            </div>

            <div className={styles.routingDiagram} aria-label="KERN model routing flow">
              <div className={styles.routeNode}>
                <span>Input</span>
                <strong>Changed source</strong>
                <small>exact repository bytes</small>
              </div>
              <span className={styles.routeArrow} aria-hidden="true">→</span>
              <div className={`${styles.routeNode} ${styles.compilerNode}`}>
                <span>Compile</span>
                <strong>Economical model</strong>
                <small>parse · normalize · compress</small>
              </div>
              <span className={styles.routeArrow} aria-hidden="true">→</span>
              <div className={`${styles.routeNode} ${styles.kernNode}`}>
                <span>Cache</span>
                <strong>KERN IL</strong>
                <small>shared semantic layer</small>
              </div>
              <span className={styles.routeArrow} aria-hidden="true">→</span>
              <div className={`${styles.routeNode} ${styles.runtimeNode}`}>
                <span>Reason</span>
                <strong>Frontier runtime</strong>
                <small>design · debug · implement</small>
              </div>
            </div>

            <div className={styles.routingCopy}>
              <p>
                Start with the fastest model that reliably preserves the semantics
                of the current language. Escalate metaprogramming, concurrency,
                generated code, and security-sensitive logic.
              </p>
              <p>
                The cache is validated by <strong>source hash + codec version</strong>.
                Changing the compiler model may trigger enrichment, but it cannot
                make stale IL valid.
              </p>
            </div>
          </section>

          <section className={styles.section} id="architecture" aria-labelledby="architecture-title">
            <div className={styles.sectionHeading}>
              <span className={styles.step}>03</span>
              <div>
                <p className={styles.eyebrow}>Architecture</p>
                <h2 id="architecture-title">A virtual-memory lifecycle for code.</h2>
                <p>
                  Keep the page table resident. Compile, render, and load detailed
                  representations only when the active task touches them.
                </p>
              </div>
            </div>

            <ol className={styles.lifecycle}>
              {lifecycle.map(([number, title, body]) => (
                <li key={number}>
                  <span>{number}</span>
                  <div>
                    <strong>{title}</strong>
                    <p>{body}</p>
                  </div>
                </li>
              ))}
            </ol>

            <div className={styles.architectureGrid}>
              <div className={styles.invariants}>
                <span className={styles.panelLabel}>Non-negotiable invariants</span>
                <ul>
                  {invariants.map((item) => <li key={item}>{item}</li>)}
                </ul>
              </div>
              <div className={styles.cachePanel}>
                <span className={styles.panelLabel}>Derived cache layout</span>
                <pre tabIndex="0"><code>{`.kern/
  config.json
  manifest.json
  ir/<source-path>.kern-il.txt
  images/<source-path>/page-*.webp
  jobs/<source-path>.job.json
  staging/`}</code></pre>
                <p>Derived state belongs in <code>.gitignore</code>.</p>
              </div>
            </div>
          </section>

          <section className={`${styles.section} ${styles.verify}`} id="verify" aria-labelledby="verify-title">
            <div className={styles.sectionHeading}>
              <span className={styles.step}>04</span>
              <div>
                <p className={styles.eyebrow}>Verify</p>
                <h2 id="verify-title">Run the deterministic baseline.</h2>
                <p>
                  Replace <code>/path/to/kern</code> and the example source path,
                  then run these commands from a test repository.
                </p>
              </div>
            </div>
            <div className={styles.commandBlock}>
              <div className={styles.commandTop}>
                <span>Terminal</span>
                <span>baseline / local</span>
              </div>
              <pre tabIndex="0"><code>{`python3 /path/to/kern/skills/kern/scripts/kern_cache.py --repo . scan
python3 /path/to/kern/skills/kern/scripts/kern_cache.py --repo . ensure path/to/file.py
python3 /path/to/kern/skills/kern/scripts/kern_cache.py --repo . status`}</code></pre>
            </div>
            <p className={styles.verifyResult}>
              <span aria-hidden="true">✓</span> The first run creates <code>.kern/</code>.
              Do not commit that directory.
            </p>
          </section>

          <footer className={styles.footer}>
            <div>
              <Mark />
              <p><strong>KERN</strong> — compile code for machine attention.</p>
            </div>
            <nav aria-label="Footer navigation">
              <a href={withBasePath("/")}>Home</a>
              <a href="https://github.com/enoch3712/KERN">GitHub</a>
              <a href="https://github.com/enoch3712/KERN/blob/main/docs/install.md">Install source</a>
              <a href="https://github.com/enoch3712/KERN/blob/main/docs/architecture.md">Architecture source</a>
            </nav>
          </footer>
        </article>
      </div>
    </main>
  );
}
