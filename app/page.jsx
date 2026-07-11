"use client";

import { useEffect, useRef, useState } from "react";

const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "";
const asset = (path) => `${BASE_PATH}${path}`;

const environments = [
  {
    id: "claude",
    name: "Claude Code",
    logo: "/logos/claude.svg",
    compiler: "Sonnet 5",
    runtime: "Fable 5",
    command: "claude plugin marketplace add enoch3712/KERN --scope user && claude plugin install kern@kern --scope user",
    installNote: "Native marketplace plugin · user scope",
  },
  {
    id: "codex",
    name: "Codex",
    logo: "/logos/openai.svg",
    compiler: "Luna",
    runtime: "GPT-5.6 Sol",
    command: "codex plugin marketplace add enoch3712/KERN && codex plugin add kern@kern",
    installNote: "Add the marketplace and install KERN in one command",
    light: true,
  },
  {
    id: "cursor",
    name: "Cursor",
    logo: "/logos/cursor.svg",
    runtimeLogo: "/logos/grok.svg",
    compiler: "Composer 2.5",
    runtime: "Grok 4.5",
    command: "git clone --depth 1 https://github.com/enoch3712/KERN.git ~/.cursor/plugins/local/kern",
    installNote: "Local plugin · reload the Cursor window",
  },
];

const memorySteps = [
  {
    title: "Watch changes",
    body: "Hash the repository. A changed file gets a new identity; unchanged pages stay cached.",
  },
  {
    title: "Compile lazily",
    body: "Only changed or newly relevant files pass through the economical compiler model.",
  },
  {
    title: "Store cold",
    body: "Compact KERN IL pages settle into a mirror that follows the source tree.",
  },
  {
    title: "Fault context in",
    body: "A task selects the few semantic pages the runtime actually needs.",
  },
  {
    title: "Verify before write",
    body: "Exact source returns, its hash is checked, and the old page is invalidated after the edit.",
  },
];

const languages = [
  ["Python", "/logos/python.svg"],
  ["TypeScript", "/logos/typescript.svg"],
  ["JavaScript", "/logos/javascript.svg"],
  ["Java", "/logos/openjdk.svg", true],
  ["Go", "/logos/go.svg"],
  ["Rust", "/logos/rust.svg", true],
  ["C# / .NET", "/logos/dotnet.svg"],
  ["C++", "/logos/cplusplus.svg"],
  ["Swift", "/logos/swift.svg"],
  ["Kotlin", "/logos/kotlin.svg"],
];

function Arrow({ down = false }) {
  return (
    <svg className={down ? "arrow arrow-down" : "arrow"} viewBox="0 0 20 20" aria-hidden="true">
      <path d={down ? "M10 3v12m0 0 5-5m-5 5-5-5" : "M4 10h12m0 0-5-5m5 5-5 5"} />
    </svg>
  );
}

function Brand() {
  return (
    <span className="brand-lockup">
      <span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>
      <span>KERN<span>/IL</span></span>
    </span>
  );
}

function CodeIcon() {
  return (
    <svg className="compiler-icon" viewBox="0 0 88 52" role="img" aria-label="Source code compiled into machine instructions">
      <path d="m18 14-9 12 9 12M32 14l9 12-9 12" />
      <path className="compiler-arrow" d="M47 26h13m0 0-4-4m4 4-4 4" />
      <rect x="66" y="12" width="15" height="28" rx="3" />
      <path d="M70 18h7M70 23h7M70 28h7M70 33h7" />
    </svg>
  );
}

function SectionHead({ eyebrow, title, body }) {
  return (
    <div className="section-head reveal">
      <p className="eyebrow">{eyebrow}</p>
      <h2>{title}</h2>
      {body ? <p>{body}</p> : null}
    </div>
  );
}

export default function Home() {
  const [activeEnv, setActiveEnv] = useState("codex");
  const [copied, setCopied] = useState("");
  const [memoryStep, setMemoryStep] = useState(0);
  const [memoryVisible, setMemoryVisible] = useState(false);
  const memoryRef = useRef(null);
  const active = environments.find((environment) => environment.id === activeEnv);

  useEffect(() => {
    const elements = document.querySelectorAll(".reveal");
    const observer = new IntersectionObserver(
      (entries) => entries.forEach((entry) => entry.isIntersecting && entry.target.classList.add("visible")),
      { threshold: 0.16 },
    );
    elements.forEach((element) => observer.observe(element));
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!memoryRef.current) return undefined;
    const observer = new IntersectionObserver(
      ([entry]) => setMemoryVisible(entry.isIntersecting),
      { threshold: 0.35 },
    );
    observer.observe(memoryRef.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!memoryVisible) return undefined;
    const timer = window.setInterval(() => setMemoryStep((step) => (step + 1) % memorySteps.length), 2800);
    return () => window.clearInterval(timer);
  }, [memoryVisible]);

  const copyInstall = async (environment) => {
    await navigator.clipboard.writeText(environment.command);
    setCopied(environment.id);
    window.setTimeout(() => setCopied(""), 1800);
  };

  return (
    <main>
      <header className="site-nav">
        <a href="#top" aria-label="KERN home"><Brand /></a>
        <nav aria-label="Primary navigation">
          <a href="#routing">How it works</a>
          <a href="#runtime">Runtime</a>
          <a href="#proof">Proof</a>
          <a href="#languages">Languages</a>
        </nav>
        <div className="nav-actions">
          <a href={`${BASE_PATH}/docs/`}>Docs</a>
          <a className="github-link" href="https://github.com/enoch3712/KERN" target="_blank" rel="noreferrer">GitHub <Arrow /></a>
        </div>
      </header>

      <section className="hero" id="top">
        <div className="hero-copy reveal visible">
          <p className="eyebrow">OPEN-SOURCE CONTEXT COMPILER</p>
          <h1>Compile code for<br /><em>machine attention.</em></h1>
          <p className="hero-lede">KERN turns repositories into compact, verified intermediate language, so coding agents spend context on reasoning—and return to exact source before they write.</p>
          <div className="hero-actions">
            <a className="button button-primary" href="#install">Install KERN <Arrow /></a>
            <a className="button button-ghost" href="https://github.com/enoch3712/KERN" target="_blank" rel="noreferrer">View on GitHub</a>
          </div>
          <div className="hero-trust">
            <span><i />Lazy + content-addressed</span>
            <span><i />Exact-source write gate</span>
            <span><i />Apache-2.0</span>
          </div>
        </div>

        <div className="install-terminal reveal visible" id="install">
          <div className="terminal-head"><span>KERN / QUICK INSTALL</span><strong>v0.1.1</strong></div>
          <div className="install-tabs" role="tablist" aria-label="Installation environment">
            {environments.map((environment) => (
              <button
                type="button"
                role="tab"
                aria-selected={activeEnv === environment.id}
                className={activeEnv === environment.id ? "active" : ""}
                onClick={() => setActiveEnv(environment.id)}
                key={environment.id}
              >
                <span className={environment.light ? "logo-tile light" : "logo-tile"}><img src={asset(environment.logo)} alt="" /></span>
                {environment.name}
              </button>
            ))}
          </div>
          <div className="command-panel" role="tabpanel">
            <div className="command-line"><span>$</span><code>{active.command}</code></div>
            <button className="copy-button" type="button" onClick={() => copyInstall(active)} aria-label={`Copy ${active.name} install command`}>
              {copied === active.id ? "Copied" : "Copy"}
            </button>
          </div>
          <div className="terminal-foot"><span>{active.installNote}</span><a href={`${BASE_PATH}/docs/#${active.id}`}>Full setup <Arrow /></a></div>
        </div>
      </section>

      <section className="routing section-pad" id="routing">
        <SectionHead
          eyebrow="SEPARATE THE MODELS"
          title="Compile with the fast model. Reason with the best one."
          body="Choose an environment. KERN compiles changed code with its economical IR model, caches compact KERN IL, and gives the runtime only the context required for the task."
        />

        <div className="environment-switch reveal" role="tablist" aria-label="Model environment">
          {environments.map((environment) => (
            <button
              type="button"
              role="tab"
              aria-selected={activeEnv === environment.id}
              className={activeEnv === environment.id ? "active" : ""}
              onClick={() => setActiveEnv(environment.id)}
              key={environment.id}
            >
              <span className={environment.light ? "logo-tile light" : "logo-tile"}><img src={asset(environment.logo)} alt="" /></span>
              <span><strong>{environment.name}</strong><small>{environment.compiler} → {environment.runtime}</small></span>
            </button>
          ))}
        </div>

        <div className={`routing-visual route-${active.id}`} key={`route-${active.id}`}>
          <div className="source-packet">
            <span className="visual-label">CHANGED SOURCE</span>
            <div className="code-lines" aria-hidden="true">{[92, 65, 84, 48, 76, 58].map((width, index) => <i style={{ width: `${width}%` }} key={index} />)}</div>
            <small>syntax · imports · repetition</small>
          </div>
          <div className="moving-arrow"><Arrow /><i /></div>
          <div className="model-node compiler-node">
            <span className={active.compiler === "Luna" ? "model-logo luna-logo" : "model-logo"}>{active.compiler === "Luna" ? <b>L</b> : <img src={asset(active.logo)} alt="" />}</span>
            <span className="visual-label">IR COMPILER MODEL</span>
            <strong>{active.compiler}</strong>
            <small>parse · normalize · compress</small>
          </div>
          <div className="moving-arrow packet-arrow"><Arrow /><i /></div>
          <div className="kern-node">
            <Brand />
            <strong>KERN IL</strong>
            <small>compact · cached · task-selected</small>
          </div>
          <div className="moving-arrow runtime-arrow"><Arrow /><i /></div>
          <div className="model-node runtime-node">
            <span className={active.light ? "model-logo light" : "model-logo"}><img src={asset(active.runtimeLogo || active.logo)} alt="" /></span>
            <span className="visual-label">RUNTIME MODEL</span>
            <strong>{active.runtime}</strong>
            <small>reason · design · implement</small>
          </div>
        </div>
        <p className="ir-glossary reveal"><strong>IR</strong> means Intermediate Representation: a machine-oriented form between source and consumption. <strong>KERN IL</strong> is the stable intermediate language shared by compiler and runtime models.</p>
      </section>

      <section className="runtime section-pad" id="runtime" ref={memoryRef}>
        <SectionHead
          eyebrow="REPOSITORY VIRTUAL MEMORY"
          title="Keep the map hot. Page detail in only when needed."
          body="KERN keeps a cheap index resident, compiles on demand, and treats exact source like a protected page fault before mutation."
        />

        <div className="memory-explainer reveal">
          <div className="memory-steps" role="tablist" aria-label="Repository paging lifecycle">
            {memorySteps.map((step, index) => (
              <button
                type="button"
                role="tab"
                aria-selected={memoryStep === index}
                className={memoryStep === index ? "active" : ""}
                onClick={() => setMemoryStep(index)}
                key={step.title}
              >
                <span>{String(index + 1).padStart(2, "0")}</span>
                <div><strong>{step.title}</strong><p>{step.body}</p></div>
              </button>
            ))}
          </div>

          <div className={`memory-stage memory-state-${memoryStep}`} role="tabpanel" aria-live="polite">
            <div className="memory-region source-region">
              <div className="region-head"><span>SOURCE REPOSITORY</span><small>authoritative</small></div>
              <div className="file-tree">
                <div><i />src/agent.ts <span>a84f</span></div>
                <div className="changed"><i />src/cache.py <span>9c21</span></div>
                <div><i />tests/cache.py <span>41bd</span></div>
              </div>
              <div className="exact-source"><span>EXACT SOURCE</span><code>if current_sha != expected_sha:</code><code>    reject_stale_commit()</code></div>
            </div>

            <div className="memory-transfer first-transfer"><Arrow /><span className="il-packet">IL</span></div>

            <div className="memory-region mirror-region">
              <div className="region-head"><span>KERN MIRROR</span><small>derived cache</small></div>
              <div className="mirror-tree">
                <div><span>manifest.json</span><i /></div>
                <div><span>ir/src/agent.ts</span><i /></div>
                <div className="compiled"><span>ir/src/cache.py</span><i /></div>
                <div><span>images/src/cache.py</span><i /></div>
              </div>
              <div className="compiler-pulse"><span>JIT COMPILE</span><b>{active.compiler}</b></div>
            </div>

            <div className="memory-transfer second-transfer"><Arrow /><span className="page-packet">PAGE</span></div>

            <div className="memory-region context-region">
              <div className="region-head"><span>MODEL CONTEXT</span><small>working set</small></div>
              <div className="context-slots">
                <div>repo map</div>
                <div className="faulted">cache.py · KERN IL</div>
                <div className="source-slot">cache.py · exact source</div>
              </div>
              <div className="write-gate"><span>WRITE GATE</span><strong>HASH MATCH</strong></div>
            </div>
          </div>
          <p className="memory-current"><span>{String(memoryStep + 1).padStart(2, "0")}</span><strong>{memorySteps[memoryStep].title}</strong>{memorySteps[memoryStep].body}</p>
        </div>
      </section>

      <section className="history section-pad" id="history">
        <SectionHead
          eyebrow="ANOTHER ABSTRACTION LAYER"
          title="Software has solved this pattern before."
          body="Every major abstraction separated how humans express software from how machines consume it. KERN applies the same idea to model attention."
        />
        <div className="history-line reveal">
          <article>
            <span className="history-era">01</span>
            <div className="history-symbol punch-card" aria-hidden="true">{Array.from({ length: 20 }).map((_, index) => <i key={index} />)}</div>
            <h3>Punch cards</h3><p>Programs matched the machine format.</p>
          </article>
          <Arrow />
          <article>
            <span className="history-era">02</span>
            <div className="history-symbol"><CodeIcon /></div>
            <h3>Compilers</h3><p>Source became independent from hardware instructions.</p>
          </article>
          <Arrow />
          <article>
            <span className="history-era">03</span>
            <div className="history-symbol vm-symbol"><span className="light"><img src={asset("/logos/openjdk.svg")} alt="OpenJDK" /></span><span><img src={asset("/logos/dotnet.svg")} alt=".NET" /></span></div>
            <h3>VMs + IL</h3><p>Bytecode became independent from execution platforms.</p>
          </article>
          <Arrow />
          <article className="history-kern">
            <span className="history-era">04</span>
            <div className="history-symbol"><Brand /></div>
            <h3>KERN IL</h3><p>Source syntax becomes independent from model attention.</p>
          </article>
        </div>
        <p className="history-caption reveal">Same pattern, new consumer: the runtime is now a coding model.</p>
      </section>

      <section className="proof section-pad" id="proof">
        <SectionHead
          eyebrow="MEASURED PILOT"
          title="Compress until semantics are dense—not vague."
          body="One path, one selected density, and exact source retained as authority."
        />
        <div className="compression-flow reveal">
          <div className="compression-stage raw-stage">
            <span className="visual-label">RAW SOURCE</span>
            <strong>36,674</strong><small>estimated text tokens</small>
            <div className="raw-snippet" aria-hidden="true">
              <i /><i /><i /><i /><i /><i /><i /><i /><i />
            </div>
          </div>
          <div className="compression-arrow"><Arrow /><span>semantic compile</span></div>
          <div className="compression-stage il-stage">
            <span className="visual-label">KERN IL</span>
            <strong>5,795</strong><small>estimated text tokens</small>
            <div className="semantic-snippet" aria-hidden="true"><i>F</i><i>IF</i><i>CALL</i><i>ERR</i><i>RET</i></div>
          </div>
          <div className="compression-arrow"><Arrow /><span>dense render</span></div>
          <div className="compression-stage dense-stage">
            <span className="visual-label">DENSE PAGE</span>
            <strong>2,877</strong><small>estimated image tokens</small>
            <div className="dense-page" aria-hidden="true">{Array.from({ length: 44 }).map((_, index) => <i key={index} />)}</div>
          </div>
        </div>
        <div className="proof-result reveal">
          <div><strong>~12.75×</strong><span>representation compression</span></div>
          <p><b>DENSE · 10 PX · SOURCE-VERIFIED</b> Measured on a 3,704-line Python pilot. Full agent-loop cost is larger and workload-dependent.</p>
          <a href={asset("/benchmark-results.json")} target="_blank">View benchmark data <Arrow /></a>
        </div>
      </section>

      <section className="languages section-pad" id="languages">
        <div className="language-heading reveal"><p className="eyebrow">LANGUAGE FRONTENDS</p><h2>One IL across the languages you already use.</h2></div>
        <div className="language-strip reveal">
          {languages.map(([name, logo, light]) => (
            <div className={light ? "language-logo light" : "language-logo"} data-label={name} aria-label={name} key={name}>
              <img src={asset(logo)} alt="" />
            </div>
          ))}
        </div>
      </section>

      <section className="open-source-band">
        <div><p className="eyebrow">OPEN SOURCE · APACHE-2.0</p><h2>Inspect the protocol. Improve the compiler.</h2></div>
        <div className="band-actions">
          <a className="button button-primary" href="#install">Install</a>
          <a className="button button-ghost" href={`${BASE_PATH}/docs/`}>Docs</a>
          <a className="button button-ghost" href="https://github.com/enoch3712/KERN" target="_blank" rel="noreferrer">GitHub</a>
        </div>
      </section>

      <footer>
        <Brand />
        <p>Compile code for machine attention.</p>
        <div><a href={`${BASE_PATH}/docs/`}>Docs</a><a href="https://github.com/enoch3712/KERN">GitHub</a><a href="https://github.com/enoch3712/KERN/blob/main/LICENSE">License</a></div>
        <small>Product and language marks belong to their respective owners. Compatibility references do not imply endorsement.</small>
      </footer>
    </main>
  );
}
