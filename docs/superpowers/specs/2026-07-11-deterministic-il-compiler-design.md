# KERN-IL/0.2 — Deterministic IL Compiler

Status: approved design, pending implementation plan.
Companion walkthrough with worked example: [`docs/deterministic-compiler.md`](../../deterministic-compiler.md).

## Goal

Replace model-generated IL summaries with a deterministic compiler that lowers
source into KERN-IL from AST information. Same source bytes always produce the
same IL bytes. The IL retains signatures, types, calls, control flow, side
effects, and exceptions, declares what it omits with real counts, and stamps
every symbol with a source-map handle so stale reads can be trapped by a
program instead of agent discipline.

## Decisions (settled)

| Question | Decision |
|---|---|
| Parser strategy | tree-sitter for non-Python languages; Python keeps stdlib `ast`. Graceful fallback to generic line baseline when tree-sitter is absent or parsing fails. |
| Model enrichment | Deterministic IL is primary and always present. Model may append clearly-marked `INTENT` lines on top; it never replaces deterministic facts. `prepare`/`commit` machinery stays, demoted to this role. |
| Language scope v1 | Python (upgrade existing frontend) + TypeScript/JavaScript (new tree-sitter frontend). Others stay on generic baseline until format stabilizes. |
| Latent-fault defense | Risk tags in IL + per-symbol slice hashes + new `verify` CLI verb, mandatory on the edit path. |
| Keyword micro-compression | Rejected. BPE already encodes the opcode vocabulary in 1–2 tokens; identifiers must stay verbatim; a per-page dictionary is a new latent-fault source. Tiering is the compression lever. |

## Measured foundation (3,704-line production Python file, ~47k source tokens)

| Tier | Per-function content | Tokens | Compression |
|---|---|---:|---:|
| L0 | signatures, classes, constants, imports | 1,891 | 24.9× |
| L1 | + deduplicated calls + raises | 4,567 | 10.3× |
| L2 | + control-flow skeleton, no expressions | 7,273 | 6.5× |
| L3 | + full expressions on flow lines | 10,095 | 4.7× |
| 0.1 today | statement dump | 28,673 | 1.6× |

L2 matches the model-enriched pilot (6.33×) with zero model tokens. The 0.1
baseline's weakness is the emitter, not the AST approach. On small dense files
(≤ ~50 lines) IL breaks even at best — a size floor applies.

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
   existing `python_ir` logic). TS/JS frontend uses `tree-sitter` +
   `tree-sitter-typescript`/`tree-sitter-javascript` via a node-type mapping
   table.
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
   Tier is chosen per file (config default) and per symbol (on-demand
   re-ensure); each function line carries its tier marker (`~L1` etc.) so the
   omission is declared. Hot symbols remain exact-source faults, unchanged.
5. **Secret redaction.** Reuse existing `SECRET_NAME`/`SECRET_VALUE` logic in
   all frontends and the emitter (verified working: hardcoded `s2_…` key never
   reached IL in testing).

## KERN-IL/0.2 format

```text
KERN-IL/0.2
source_rel=src/loader.py
source_sha256=<64 hex>
repo_revision=<git short-sha | dirty:<short-sha> | none>
generator=kern-det/0.2 lang=python frontend=pyast tier=L2

IMPORTS json, re, hashlib.sha256, pathlib.Path @L3-8
C ENTRY_PATTERN=re.compile(…) @L11 !FAULT(regex)

CLASS StaleSource(Exception) @L14-15 ^b8e2d1a4

F load_entry(path: Path, expected_sha: str) -> dict @L18-24 ^a4f1c2e9 ~L3
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

- Header keys are fixed and machine-parseable (existing `commit` validation
  extends to `repo_revision` and `tier`).
- `^hash` = first 8 hex of sha256 of the symbol's exact source slice.
- Source-map handle = `repo_revision : source_sha256 : symbol_path : span`,
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
- `verify <file> --symbol <path> --hash <slice-hash>` (new) — recomputes the
  symbol slice against current source. Returns `ok` | `moved` (same bytes, new
  span, span returned) | `stale` (bytes changed). SKILL.md makes a passing
  `verify` or fresh `fault` mandatory before editing any symbol read from IL or
  an image.
- Size floor: files whose source is below a configured token estimate
  (default ~600 tokens) get a `mode=source-cheaper` stub IL pointing at exact
  source instead of a full page.

## Fallback chain

1. Python: stdlib `ast` — always available.
2. TS/JS: tree-sitter if importable; otherwise generic line baseline with
   `QA tree-sitter unavailable`.
3. Any parse error: generic line baseline with the error noted, never a crash.

## Verification plan

1. **Determinism:** compile corpus twice; byte-identical output required.
2. **Golden files:** fixture source → expected IL per language, per tier, in CI.
3. **Fact fidelity:** independently extract signatures, raise sites, and call
   names from the AST and assert each appears in the IL (machine-checked).
4. **Token benchmarks:** `benchmarks/token_bench.py` counts source vs IL per
   tier across a corpus bucketed by file size (Anthropic count-tokens API when
   a key is present, chars/4 estimator otherwise); results published under
   `benchmarks/results/`. Acceptance: ≥6× at L2 on the large-Python bucket
   (measured 6.5×), reported honestly if lower.
5. **Fallback tests:** tree-sitter absent, syntax-broken files, binary input,
   secret redaction.
6. **verify verb tests:** ok / moved / stale paths, including the
   symbol-renamed and file-shifted cases.

## Out of scope (0.2)

LSP type resolution, cross-file call graph, Go/Rust/Java/C# frontends, image
renderer changes (it consumes IL text unchanged), incremental (per-edit)
recompilation.
