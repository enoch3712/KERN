"use client";

import { useEffect, useRef, useState } from "react";

const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "";
const asset = (path) => `${BASE_PATH}${path}`;

const environments = [
  {
    id: "codex",
    name: "Codex",
    logo: "/logos/openai.svg",
    compiler: "Luna",
    runtime: "GPT-5.6 Sol",
    command: "codex plugin marketplace add enoch3712/KERN && codex plugin add kern@kern",
    installNote: "Marketplace plugin · configure the compiler model independently",
    light: true,
  },
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
    id: "cursor",
    name: "Cursor",
    logo: "/logos/cursor.svg",
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
  { name: "Python", logo: "/logos/python.svg", support: "Structured" },
  { name: "TypeScript", logo: "/logos/typescript.svg", support: "Generic" },
  { name: "JavaScript", logo: "/logos/javascript.svg", support: "Generic" },
  { name: "Java", logo: "/logos/java.svg", support: "Generic" },
  { name: "Go", logo: "/logos/go.svg", support: "Generic" },
  { name: "Rust", logo: "/logos/rust.svg", support: "Generic", light: true },
  { name: "C# / .NET", logo: "/logos/dotnet.svg", support: "Generic" },
  { name: "C++", logo: "/logos/cplusplus.svg", support: "Generic" },
  { name: "Swift", logo: "/logos/swift.svg", support: "Generic" },
  { name: "Kotlin", logo: "/logos/kotlin.svg", support: "Generic" },
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

function SectionHead({ eyebrow, title, body }) {
  return (
    <div className="section-head">
      <p className="eyebrow">{eyebrow}</p>
      <h2>{title}</h2>
      {body ? <p>{body}</p> : null}
    </div>
  );
}

function moveTabFocus(event, ids, activeId, onChange) {
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
  window.requestAnimationFrame(() => tablist?.querySelector(`[data-tab-id="${nextId}"]`)?.focus());
}

function EnvironmentTabs({ activeId, onChange, label, panelId, idPrefix, className = "environment-switch" }) {
  const ids = environments.map((environment) => environment.id);
  return (
    <div className={className} role="tablist" aria-label={label}>
      {environments.map((environment) => (
        <button
          type="button"
          role="tab"
          id={`${idPrefix}-tab-${environment.id}`}
          data-tab-id={environment.id}
          aria-selected={activeId === environment.id}
          aria-controls={panelId}
          tabIndex={activeId === environment.id ? 0 : -1}
          className={activeId === environment.id ? "active" : ""}
          onClick={() => onChange(environment.id)}
          onKeyDown={(event) => moveTabFocus(event, ids, activeId, onChange)}
          key={environment.id}
        >
          <span className={environment.light ? "logo-tile light" : "logo-tile"}>
            <img src={asset(environment.logo)} alt="" />
          </span>
          <span><strong>{environment.name}</strong><small>{environment.compiler} → {environment.runtime}</small></span>
        </button>
      ))}
    </div>
  );
}

export default function Home() {
  const [installEnv, setInstallEnv] = useState("codex");
  const [compilerEnv, setCompilerEnv] = useState("codex");
  const [copied, setCopied] = useState("");
  const [memoryStep, setMemoryStep] = useState(0);
  const [memoryVisible, setMemoryVisible] = useState(false);
  const [memoryHovered, setMemoryHovered] = useState(false);
  const [memoryManual, setMemoryManual] = useState(false);
  const [reduceMotion, setReduceMotion] = useState(false);
  const memoryRef = useRef(null);
  const activeInstall = environments.find((environment) => environment.id === installEnv);
  const activeCompiler = environments.find((environment) => environment.id === compilerEnv);

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduceMotion(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
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
    if (!memoryVisible || memoryHovered || memoryManual || reduceMotion) return undefined;
    const timer = window.setInterval(() => setMemoryStep((step) => (step + 1) % memorySteps.length), 3800);
    return () => window.clearInterval(timer);
  }, [memoryVisible, memoryHovered, memoryManual, reduceMotion]);

  const copyInstall = async (environment) => {
    try {
      await navigator.clipboard.writeText(environment.command);
      setCopied(environment.id);
    } catch {
      setCopied("error");
    }
    window.setTimeout(() => setCopied(""), 1800);
  };

  const chooseMemoryStep = (index) => {
    setMemoryStep(index);
    setMemoryManual(true);
  };

  return (
    <main>
      <a className="skip-link" href="#main-content">Skip to content</a>
      <header className="site-nav">
        <a href="#top" aria-label="KERN home"><Brand /></a>
        <nav aria-label="Primary navigation">
          <a href="#compiler">Compiler</a>
          <a href="#runtime">Runtime</a>
          <a href="#history">Why IL</a>
          <a href="#languages">Languages</a>
        </nav>
        <div className="nav-actions">
          <a href={`${BASE_PATH}/docs/`}>Docs</a>
          <a className="github-link" href="https://github.com/enoch3712/KERN" target="_blank" rel="noreferrer">GitHub <Arrow /></a>
        </div>
      </header>

      <div id="main-content">
        <section className="hero" id="top">
          <div className="hero-copy">
            <p className="eyebrow">OPEN-SOURCE CONTEXT COMPILER FOR LARGE CODEBASES · v0.1.1</p>
            <h1><em>12.75× smaller.</em><br />Exact source when it matters.</h1>
            <p className="hero-lede">Built for large codebases, KERN compiles repositories into a compact intermediate language, caches unchanged pages, and faults exact source back in before every edit.</p>
            <p className="pilot-qualifier">Selected-representation reduction in one 3,704-line Python pilot—not total agent-loop savings.</p>
            <div className="hero-actions">
              <a className="button button-primary" href="#install">Install KERN <Arrow /></a>
              <a className="button button-ghost" href={`${BASE_PATH}/docs/`}>Read the docs</a>
            </div>
            <div className="hero-trust">
              <span><i />Local-first</span>
              <span><i />Content-addressed</span>
              <span><i />Exact-source gate</span>
              <span><i />Apache-2.0</span>
            </div>
          </div>

          <div className="hero-evidence" aria-label="Measured pilot compression result">
            <div className="evidence-head">
              <span>MEASURED PILOT / SELECTED ARTIFACT</span>
              <strong>92.2% smaller</strong>
            </div>
            <div className="compression-rail">
              <article className="rail-stage rail-raw">
                <span>RAW SOURCE</span>
                <div className="rail-page" aria-hidden="true">{Array.from({ length: 18 }).map((_, index) => <i key={index} />)}</div>
                <strong>36,674</strong>
                <small>text tokens est.</small>
              </article>
              <Arrow />
              <article className="rail-stage rail-il">
                <span>KERN IL</span>
                <div className="rail-page rail-semantic" aria-hidden="true"><i>F</i><i>IF</i><i>CALL</i><i>SIDE</i><i>QA</i></div>
                <strong>5,795</strong>
                <small>text tokens est.</small>
              </article>
              <Arrow />
              <article className="rail-stage rail-dense">
                <span>DENSE PAGE</span>
                <div className="rail-page" aria-hidden="true">{Array.from({ length: 10 }).map((_, index) => <i key={index} />)}</div>
                <strong>2,877</strong>
                <small>image tokens est.</small>
              </article>
            </div>
            <div className="loop-receipt">
              <span>END-TO-END RECEIPT</span>
              <p>Dense pilot run: <strong>18,107 uncached</strong> input tokens across four turns; <strong>73,403 cumulative</strong>. Representation size and complete agent-loop cost are different measurements.</p>
              <a href="https://github.com/enoch3712/KERN/tree/main/benchmarks" target="_blank" rel="noreferrer">Methodology <Arrow /></a>
            </div>
          </div>

          <div className="install-terminal" id="install">
            <div className="terminal-head"><span>KERN / QUICK INSTALL</span><strong>v0.1.1</strong></div>
            <div className="install-tabs" role="tablist" aria-label="Installation environment">
              {environments.map((environment) => (
                <button
                  type="button"
                  role="tab"
                  id={`install-tab-${environment.id}`}
                  data-tab-id={environment.id}
                  aria-selected={installEnv === environment.id}
                  aria-controls="install-panel"
                  tabIndex={installEnv === environment.id ? 0 : -1}
                  className={installEnv === environment.id ? "active" : ""}
                  onClick={() => setInstallEnv(environment.id)}
                  onKeyDown={(event) => moveTabFocus(event, environments.map((item) => item.id), installEnv, setInstallEnv)}
                  key={environment.id}
                >
                  <span className={environment.light ? "logo-tile light" : "logo-tile"}><img src={asset(environment.logo)} alt="" /></span>
                  {environment.name}
                </button>
              ))}
            </div>
            <div className="command-panel" id="install-panel" role="tabpanel" aria-labelledby={`install-tab-${activeInstall.id}`}>
              <div className="command-line"><span>$</span><code>{activeInstall.command}</code></div>
              <button className="copy-button" type="button" onClick={() => copyInstall(activeInstall)} aria-label={`Copy ${activeInstall.name} install command`}>
                {copied === activeInstall.id ? "Copied" : copied === "error" ? "Select text" : "Copy"}
              </button>
            </div>
            <div className="terminal-foot"><span>{activeInstall.installNote}</span><a href={`${BASE_PATH}/docs/?provider=${activeInstall.id}`}>Full setup <Arrow /></a></div>
          </div>
        </section>

        <section className="compiler section-pad" id="compiler">
          <SectionHead
            eyebrow="THE COMPILER, NOT A DIAGRAM"
            title="Watch source become task-ready context."
            body="The host changes. The KERN IL contract does not. Select a provider to see an example compiler/runtime route around the same source transformation."
          />

          <EnvironmentTabs activeId={compilerEnv} onChange={setCompilerEnv} label="Compiler environment" panelId="compiler-panel" idPrefix="compiler" />

          <div className={`compiler-workbench provider-${activeCompiler.id}`} id="compiler-panel" role="tabpanel" aria-labelledby={`compiler-tab-${activeCompiler.id}`}>
            <div className="workbench-bar">
              <Brand />
              <span>src/cache.py</span>
              <span><i /> source hash 9c21… verified</span>
            </div>

            <div className="workbench-grid">
              <section className="code-pane source-pane" aria-labelledby="source-pane-title">
                <header><span id="source-pane-title">CHANGED SOURCE</span><small>authoritative</small></header>
                <ol className="source-code">
                  <li><code><b>from</b> hashlib <b>import</b> sha256</code></li>
                  <li><code>&nbsp;</code></li>
                  <li><code><b>def</b> load_entry(path, expected_sha):</code></li>
                  <li><code>    data = path.read_bytes()</code></li>
                  <li><code>    current_sha = sha256(data).hexdigest()</code></li>
                  <li><code>    <b>if</b> current_sha != expected_sha:</code></li>
                  <li><code>        <b>raise</b> StaleSource(path)</code></li>
                  <li><code>    <b>return</b> parse(data)</code></li>
                </ol>
                <footer><span>3,704 lines</span><strong>36,674 tokens est.</strong></footer>
              </section>

              <section className="compile-pane" aria-label={`${activeCompiler.compiler} compiler status`}>
                <span className={activeCompiler.light ? "provider-orb light" : "provider-orb"}><img src={asset(activeCompiler.logo)} alt="" /></span>
                <p>IR COMPILER PROFILE</p>
                <h3>{activeCompiler.compiler}</h3>
                <small>Example route for {activeCompiler.name}</small>
                <div className="compiler-status">
                  <span><i />parse structure</span>
                  <span><i />lower semantics</span>
                  <span><i />declare omissions</span>
                  <span><i />bind source hash</span>
                </div>
                <div className="compile-meter"><i /></div>
                <strong className="compile-ratio">6.33× <small>raw → IL</small></strong>
              </section>

              <section className="code-pane il-pane" aria-labelledby="il-pane-title">
                <header><span id="il-pane-title">COMPILED KERN IL</span><small>derived · cached</small></header>
                <pre><code><b>KERN-IL/0.1</b>{`\n`}M cache source_sha256=9c21…{`\n`}F load_entry(path, expected_sha){`\n`}  READ path -&gt; data{`\n`}  CALL sha256(data) -&gt; current_sha{`\n`}  IF current_sha != expected_sha{`\n`}    RAISE StaleSource(path){`\n`}  RET parse(data){`\n`}SIDE fs:read{`\n`}QA exact-source-required{`\n`}OMIT syntax, comments, repetition</code></pre>
                <footer><span>349 lines</span><strong>5,795 tokens est.</strong></footer>
              </section>
            </div>

            <div className="runtime-route">
              <div><span>KERN IL WORKING SET</span><strong>task-selected pages</strong></div>
              <Arrow />
              <div className="runtime-profile">
                <span className={activeCompiler.light ? "provider-orb light" : "provider-orb"}><img src={asset(activeCompiler.runtimeLogo || activeCompiler.logo)} alt="" /></span>
                <p><span>RUNTIME</span><strong>{activeCompiler.runtime}</strong></p>
              </div>
              <Arrow />
              <div className="write-gate-preview"><span>BEFORE WRITE</span><strong>FAULT EXACT SOURCE</strong></div>
            </div>
          </div>
          <p className="ir-glossary"><strong>IR</strong> means Intermediate Representation: a machine-oriented form between source and consumption. <strong>KERN IL</strong> is its portable semantic language. Model names above are example routing profiles, not protocol requirements.</p>
        </section>

        <section className="runtime section-pad" id="runtime" ref={memoryRef}>
          <SectionHead
            eyebrow="REPOSITORY VIRTUAL MEMORY"
            title="Keep the map hot. Page detail in only when needed."
            body="KERN keeps a cheap index resident, compiles on demand, and treats exact source like a protected page fault before mutation."
          />

          <div
            className="memory-explainer"
            onMouseEnter={() => setMemoryHovered(true)}
            onMouseLeave={() => setMemoryHovered(false)}
            onFocusCapture={() => setMemoryHovered(true)}
            onBlurCapture={() => setMemoryHovered(false)}
          >
            <div className="memory-steps" role="tablist" aria-label="Repository paging lifecycle">
              {memorySteps.map((step, index) => (
                <button
                  type="button"
                  role="tab"
                  id={`memory-tab-${index}`}
                  data-tab-id={String(index)}
                  aria-selected={memoryStep === index}
                  aria-controls="memory-panel"
                  tabIndex={memoryStep === index ? 0 : -1}
                  className={memoryStep === index ? "active" : ""}
                  onClick={() => chooseMemoryStep(index)}
                  onKeyDown={(event) => moveTabFocus(event, memorySteps.map((_, stepIndex) => String(stepIndex)), String(memoryStep), (nextId) => chooseMemoryStep(Number(nextId)))}
                  key={step.title}
                >
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  <div><strong>{step.title}</strong><p>{step.body}</p></div>
                </button>
              ))}
            </div>

            <div className={`memory-stage memory-state-${memoryStep}`} id="memory-panel" role="tabpanel" aria-labelledby={`memory-tab-${memoryStep}`}>
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
                <div className="compiler-pulse"><span>JIT COMPILE</span><b>{activeCompiler.compiler}</b></div>
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
            title="Software has solved this shape before."
            body="Each step separated the form humans work in from the form machines consume. KERN applies that old trick to model attention."
          />
          <div className="history-line">
            <article>
              <span className="history-era">01</span>
              <div className="history-symbol punch-card" aria-hidden="true">{Array.from({ length: 20 }).map((_, index) => <i key={index} />)}</div>
              <h3>Punch cards</h3><p>Programs matched the machine format.</p>
            </article>
            <Arrow />
            <article>
              <span className="history-era">02</span>
              <div className="history-symbol compile-symbol" aria-hidden="true"><code>src</code><Arrow /><code>bin</code></div>
              <h3>Compilers</h3><p>Source stopped matching hardware instructions.</p>
            </article>
            <Arrow />
            <article>
              <span className="history-era">03</span>
              <div className="history-symbol vm-symbol" aria-hidden="true"><span>JVM</span><span>CLR</span></div>
              <h3>VMs + IL</h3><p>Bytecode stopped matching one execution platform.</p>
            </article>
            <Arrow />
            <article className="history-kern">
              <span className="history-era">04</span>
              <div className="history-symbol"><Brand /></div>
              <h3>KERN IL</h3><p>Repository meaning stops matching one model window.</p>
            </article>
          </div>
          <p className="history-caption">Once again, software goes full-circle—by adding another layer in the middle.</p>
        </section>

        <section className="languages section-pad" id="languages">
          <div className="language-heading"><p className="eyebrow">RECOGNIZED SOURCE FORMATS</p><h2>Ten languages. One portable contract.</h2><p>Python has a structured baseline today. The remaining formats use KERN&apos;s generic experimental fallback while language-specific lowering matures.</p></div>
          <div className="language-strip">
            {languages.map(({ name, logo, support, light }) => (
              <div className={light ? "language-logo light" : "language-logo"} key={name}>
                <span><img src={asset(logo)} alt="" /></span>
                <strong>{name}</strong>
                <small className={support === "Structured" ? "structured" : ""}>{support}</small>
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
          <p>Compile large codebases for machine attention.</p>
          <div><a href={`${BASE_PATH}/docs/`}>Docs</a><a href="https://github.com/enoch3712/KERN">GitHub</a><a href="https://github.com/enoch3712/KERN/blob/main/LICENSE">License</a></div>
          <small>Product and language marks belong to their respective owners. Compatibility references do not imply endorsement.</small>
        </footer>
      </div>
    </main>
  );
}
