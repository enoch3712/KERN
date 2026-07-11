# KERN-IL/0.2 — Deterministic IL Compiler

Status: implemented in KERN 0.2; corpus expansion remains ongoing.
Companion walkthrough with worked example: [`docs/deterministic-compiler.md`](../../deterministic-compiler.md).

## Goal

Replace model-generated IL summaries with a deterministic compiler that lowers
source into KERN-IL from AST information. Identical source path and bytes, tier,
and compiler/parser fingerprint produce identical IL. The IL retains signatures,
types, calls, control flow, side
effects, and exceptions, declares what it omits with real counts, and stamps
every symbol with a source-map handle so stale reads can be trapped by a
program instead of agent discipline.

## Decisions (settled)

| Question | Decision |
|---|---|
| Parser strategy | Python keeps stdlib `ast`; the current JavaScript/TypeScript frontend uses the pinned tree-sitter set. Other formats and parser failures use the labeled generic baseline. |
| Model enrichment | Deterministic IL is primary and always present. Model may append clearly-marked `INTENT` lines on top; it never replaces deterministic facts. `prepare`/`commit` machinery stays, demoted to this role. |
| Language scope v1 | Python (upgrade existing frontend) + TypeScript/JavaScript (new tree-sitter frontend). Others stay on generic baseline until format stabilizes. |
| Latent-fault defense | Risk tags in IL + contextual semantic handles backed by exact line-slice digests + new `verify` CLI verb, mandatory on the edit path. |
| Keyword micro-compression | Rejected. BPE already encodes the opcode vocabulary in 1–2 tokens; identifiers must stay verbatim; a per-page dictionary is a new latent-fault source. Tiering is the compression lever. |

## Published evidence

The release evidence is the generated record under
`benchmarks/results/python-det-v2.json`, currently one redistributable Python file
recorded at L1/L2/L3 with the `chars/4` estimator. Its size bucket and ratios are
generated record fields; Python 3.13 is the canonical artifact runtime. It includes
machine-checked fidelity results but does not
establish a universal rate. Earlier unpublished prototype measurements are design
history, not release evidence and not an acceptance threshold.

## Architecture

```text
source bytes
   │  frontend (per language)
   ▼
symbol models (common internal form)
   │  effect/raise propagation (language tables + intra-file fixpoint)
   ▼
emitter (shared, tiered)          ──►  .kern/ir/<path>.kern-il.txt
```

### Components

1. **`skills/kern/scripts/kern_compile.py`** (new) — frontends + effect engine +
   emitter. `kern_cache.py:baseline_for()` calls it; cache/manifest/render
   machinery is unchanged.
2. **Frontend interface.** Each frontend consumes source text and produces
   symbol models: qualified name, kind, signature, return type, decorators,
   span, exact-slice sha256, calls, flow ops, raise sites, module-level
   imports/constants. Python frontend uses stdlib `ast` (port + upgrade the
   existing `python_ir` logic). The current TS/JS frontend uses the mutually
   compatible versions pinned in `requirements-compiler.txt` via a node-type
   mapping table. Go, Rust, Java, C#, and other formats remain generic in 0.2.
3. **Effect engine.** Per-language table mapping known callees to effect
   classes: `fs:read`, `fs:write`, `net`, `proc`, `env`, `time`, `random`,
   `console`, `thread`. Intra-file fixpoint propagation: a function calling an
   effectful local function inherits its effects and raises, recorded with
   `via` provenance. Unclassified external calls are listed as
   `unknown-calls`, never silently dropped.
4. **Emitter (shared, tiered).** Renders symbol models at detail tiers:
   - **L1** (cold default): signature line, `CALLS` (deduplicated), `EFFECTS`,
     `RAISES`.
   - **L2** (warm default): + control-flow skeleton (`IF`/`LOOP`/`WHILE`/`TRY`/
     `CATCH`/`RET`/`RAISE`/`WITH`), no expressions.
   - **L3** (warm, on demand): + expressions, `CALL expr -> var` dataflow form.
   Tier is chosen per file from the config default or `ensure --tier`; each
   function line carries that tier marker (`~L1` etc.) so the omission is
   declared. Hot symbols remain exact-source faults, unchanged.
5. **Secret redaction.** Reuse existing `SECRET_NAME`/`SECRET_VALUE` logic in
   all frontends and the emitter (verified working: hardcoded `s2_…` key never
   reached IL in testing).

## KERN-IL/0.2 format

```text
KERN-IL/0.2
source_rel=src/loader.py
source_sha256=<64 hex>
repo_revision=none
generator=kern-det/0.2 lang=python frontend=pyast tier=L2

IMPORTS json, re, hashlib.sha256, pathlib.Path @L3-8
C ENTRY_PATTERN=re.compile(…) @L11 !FAULT(regex)

CLASS StaleSource(Exception) @L14-15 ^b8e2d1a4c56f7890

F load_entry(path: Path, expected_sha: str) -> dict @L18-24 ^a4f1c2e9d0b7a631 ~L3
  CALL path.read_bytes() -> data
  CALL sha256(data).hexdigest() -> current_sha
  IF current_sha != expected_sha: RAISE StaleSource(path)
  RET json.loads(data)
  EFFECTS fs:read
  RAISES StaleSource

OMIT docstrings=4 comments=0 blank=9 bodies-tiered=2
FAULT-BEFORE edit(any), regex(L11), exact-literals
```

Format rules:

- Header keys are fixed and machine-parseable. `repo_revision` is a reserved
  compatibility field pinned to `none`; repository dirtiness is not a derivation input.
- `^handle` = a SHA-256 prefix of at least 16 hex characters derived from the
  symbol's exact line-slice digest plus stable same-module semantic context.
  Line positions are excluded, so a pure move can still be reported as `moved`.
- Source-map handle = `source_rel : source_sha256 : symbol_path : span`,
  reconstructable from any symbol line plus the page header.
- `!FAULT(reason)` risk tags stamped on lines containing regex literals,
  float/bit math, concurrency primitives, crypto calls, or truncated/elided
  literals. Contract: a tagged line may not support a claim or an edit without
  an exact-source fault.
- `OMIT` carries mechanical per-file counts, not boilerplate.
- Optional model enrichment appends only `INTENT <symbol>: <one line>` lines in
  a dedicated trailing section marked `ENRICHMENT model=<name>` — derived,
  untrusted, never interleaved with deterministic facts.
- Codec bump `kern-il/0.2` invalidates all 0.1 pages via the existing
  codec-invalidation path.

## CLI changes (`kern_cache.py`)

- `ensure` — unchanged interface; now emits 0.2 via `kern_compile.py`; accepts
  `--tier {L1,L2,L3}` override.
- `verify <file> --symbol <path> --hash <semantic-handle>` (new) — recomputes the
  symbol and module context against current source. Returns `ok` | `moved` (same facts, new
  span, span returned) | `stale` (symbol bytes or same-file semantic context
  changed). `stale` returns `ok: false` and exits 1. SKILL.md makes a passing
  `verify` or fresh `fault` mandatory before editing any symbol read from IL or
  an image.
- Size floor: files whose source is below a configured token estimate
  (default ~600 tokens) get a `mode=source-cheaper` stub IL pointing at exact
  source instead of a full page.

## Fallback chain

1. Python: stdlib `ast` — always available.
2. Current TS/JS frontend: the pinned tree-sitter set when available; otherwise
   the generic line baseline with `QA tree-sitter unavailable`.
3. Any parse error: generic line baseline with the error noted, never a crash.

## Verification plan

1. **Determinism:** under the same compiler fingerprint and runtime, compile the
   corpus twice; byte-identical output required.
2. **Golden files:** fixture source → expected IL per language, per tier, in CI.
3. **Fact fidelity:** independently extract signatures, return data, calls,
   effects, raises, unknown-call counts, tier markers, and function/class source
   handles from the AST and assert every tier retains the facts it promises.
4. **Token benchmarks:** `benchmarks/token_bench.py` counts source vs IL per
   tier across a corpus bucketed by file size (Anthropic count-tokens API when
   a key is present, chars/4 estimator otherwise); results published under
   `benchmarks/results/`. Acceptance requires byte-stable generation, no missing
   promised facts, and publication of the observed ratios. A numeric claim may be
   made only for a size/language bucket represented by a redistributable fixture;
   no universal compression gate applies to the current one-file corpus.
5. **Fallback tests:** tree-sitter absent, syntax-broken files, binary input,
   secret redaction.
6. **verify verb tests:** ok / moved / stale paths, including the
   symbol-renamed and file-shifted cases.

## Out of scope (0.2)

LSP type resolution, cross-file call graph, Go/Rust/Java/C# frontends, image
renderer changes (it consumes IL text unchanged), incremental (per-edit)
recompilation.
