# Deterministic compiler walkthrough (KERN-IL/0.2)

This document walks one example file through every stage of the implemented deterministic
compiler, showing the artifact **before and after** each step. No model is involved
in baseline lowering. Identical source path and bytes, tier, and compiler/parser
fingerprint produce identical IL.

Status: implemented for Python and the current JavaScript/TypeScript frontend.
Other recognized formats retain the labeled generic baseline.

---

## Step 0 — the input (authoritative source)

`src/cache_loader.py`, 45 lines, ~343 tokens:

```python
"""Entry loader with hash verification for the KERN cache."""

from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path

MANIFEST_NAME = "manifest.json"
ENTRY_PATTERN = re.compile(r"^[a-z0-9_/]+\.kern-il\.txt$")


class StaleSource(Exception):
    """Raised when the on-disk source no longer matches the expected hash."""


def load_entry(path: Path, expected_sha: str) -> dict:
    """Read a cache entry, verify its hash, and parse it as JSON."""
    data = path.read_bytes()
    current_sha = sha256(data).hexdigest()
    if current_sha != expected_sha:
        raise StaleSource(path)
    return json.loads(data)


def find_entries(root: Path) -> list[Path]:
    """Return every manifest-listed entry under the cache root."""
    manifest = load_entry(root / MANIFEST_NAME, read_expected(root))
    entries = []
    for name, record in manifest["files"].items():
        if not ENTRY_PATTERN.match(name):
            continue
        candidate = root / "ir" / name
        if candidate.is_file():
            entries.append(candidate)
    return entries


def read_expected(root: Path) -> str:
    """Read the pinned manifest hash written by the last scan."""
    pin = (root / ".pin").read_text(encoding="utf-8").strip()
    if len(pin) != 64:
        raise ValueError(f"corrupt pin file in {root}")
    return pin
```

---

## Step 1 — parse: source → syntax tree

The file goes through a real parser, never a regex.

| Language | Parser | Dependency |
|---|---|---|
| Python | `ast` (standard library) | Python 3.10+ |
| JavaScript (`.js`, `.jsx`, `.mjs`, `.cjs`) and TypeScript (`.ts`, `.tsx`) | current tree-sitter frontend | pinned set in `requirements-compiler.txt` |
| Go, Rust, Java, C#, and other recognized formats | generic line baseline | none |
| Parser unavailable or syntax error | current generic line baseline | none (fallback, clearly labeled) |

**Before:** raw text.
**After:** a typed tree. For `load_entry` the parser produces (abridged):

```text
FunctionDef  name=load_entry  lineno=18  end_lineno=24
├── args:      [path: Path, expected_sha: str]     returns: dict
├── Assign     data = Call(path.read_bytes)
├── Assign     current_sha = Call(sha256(data).hexdigest)
├── If         Compare(current_sha != expected_sha)
│   └── Raise  Call(StaleSource, [path])
└── Return     Call(json.loads, [data])
```

Every node carries exact line/column positions. This is where determinism comes
from: the tree is a fact about the bytes, not an interpretation of them.

---

## Step 2 — extract: tree → symbol model

A mechanical walk collects, per symbol: qualified name, signature, span, calls,
control-flow operations, raise sites, and an exact line-slice digest. A second
deterministic pass combines that digest with stable same-module semantic context
to produce the emitted source-map handle.

**Before:** syntax tree.
**After:** one record per symbol (internal form, never stored):

```json
{
  "symbol": "load_entry",
  "kind": "function",
  "signature": "(path: Path, expected_sha: str) -> dict",
  "span": [18, 24],
  "slice_sha256": "a4f1c2e9…",
  "semantic_handle": "a4f1c2e9d0b7a631",
  "calls": ["path.read_bytes", "sha256().hexdigest", "json.loads"],
  "flow": [
    {"op": "CALL", "expr": "path.read_bytes()", "binds": "data"},
    {"op": "CALL", "expr": "sha256(data).hexdigest()", "binds": "current_sha"},
    {"op": "IF",   "test": "current_sha != expected_sha",
                   "then": [{"op": "RAISE", "expr": "StaleSource(path)"}]},
    {"op": "RET",  "expr": "json.loads(data)"}
  ],
  "raises": ["StaleSource"]
}
```

`slice_sha256` is the sha256 of source lines 18–24 exactly as they appear on disk.
It stays internal. The emitted semantic handle also covers imports, constants,
decorators, classes, and other same-module symbol facts, so stale contextual facts
invalidate the handle even when the target function's own lines did not change.

---

## Step 3 — effects and exception propagation

Still no model. Two mechanical passes over the symbol models:

**Effect table.** Known callees map to effect classes:

| Callee pattern | Effect |
|---|---|
| `*.read_bytes`, `*.read_text`, `open(..., "r")`, `*.is_file` | `fs:read` |
| `*.write_bytes`, `*.write_text`, `os.replace` | `fs:write` |
| `requests.*`, `urllib.*`, `fetch` | `net` |
| `subprocess.*`, `os.system` | `proc` |
| `time.*`, `datetime.now` | `time` |
| `random.*`, `uuid.*` | `random` |

**Propagation (intra-file fixpoint).** If `find_entries` calls `load_entry` and
`load_entry` has `fs:read`, then `find_entries` inherits `fs:read` — with the path
recorded. Same for raises. Calls to symbols outside the file that match no table
entry are listed explicitly as unknown, never silently dropped.

**Before:** `find_entries` record has `calls` but no semantics.
**After:**

```json
{
  "symbol": "find_entries",
  "effects": ["fs:read (via load_entry, read_expected, is_file)"],
  "raises":  ["StaleSource (via load_entry)", "ValueError (via read_expected)"],
  "unknown_calls": []
}
```

Note: `find_entries` raising `ValueError` is a fact that appears **nowhere on any
single line of the source** — it emerges from propagation. The deterministic IL can
state things the raw text never states in one place.

---

## Step 4 — emit: symbol models → KERN-IL page

One shared emitter renders all languages. Two comparisons follow.

### Historical output (`kern-il/0.1`, python-ast-baseline)

```text
KERN-IL/0.1
source_rel=src/cache_loader.py
source_sha256=412e2610…
generator=deterministic-baseline/0.1
mode=python-ast-baseline

MODULE @L1-45
IMPORT @L3 from __future__ import annotations
IMPORT @L5 import json
IMPORT @L6 import re
IMPORT @L7 from hashlib import sha256
IMPORT @L8 from pathlib import Path
C @L10 MANIFEST_NAME='manifest.json'
C @L11 ENTRY_PATTERN=re.compile('^[a-z0-9_/]+\\.kern-il\\.txt$')

CLASS StaleSource(Exception) @L14-15

F load_entry(path: Path, expected_sha: str)->dict @L18-24
  DOC Read a cache entry, verify its hash, and parse it as JSON.
  CALLS path.read_bytes, sha256(data).hexdigest, json.loads, StaleSource, sha256
  20|SET data=path.read_bytes()
  21|SET current_sha=sha256(data).hexdigest()
  22|IF current_sha != expected_sha
    23|ERR StaleSource(path)
  24|RET json.loads(data)
  …
```

Problems: the `CALLS` line duplicates the flow lines below it; every statement
carries its own line number; no effects; no exception propagation; no per-symbol
hash; the omissions block is fixed boilerplate.

### KERN-IL/0.2 output (abridged L3 shape)

```text
KERN-IL/0.2
source_rel=src/cache_loader.py
source_sha256=412e261066069477
repo_revision=none
generator=kern-det/0.2 lang=python frontend=pyast tier=L3

IMPORTS json, re, hashlib.sha256, pathlib.Path @L3-8
C MANIFEST_NAME='manifest.json' @L10
C ENTRY_PATTERN=re.compile(r'^[a-z0-9_/]+\.kern-il\.txt$') @L11 !FAULT(regex)

CLASS StaleSource(Exception) @L14-15 ^b8e2d1a4c56f7890

F load_entry(path: Path, expected_sha: str) -> dict @L18-24 ^a4f1c2e9d0b7a631 ~L3
  EFFECTS fs:read
  RAISES StaleSource
    CALL path.read_bytes() -> data
    CALL sha256(data).hexdigest() -> current_sha
    IF current_sha != expected_sha
      RAISE StaleSource(path)
    RET json.loads(data)

F find_entries(root: Path) -> list[Path] @L27-37 ^c91d33f2e401a857 ~L3
  EFFECTS fs:read (via load_entry, read_expected, is_file)
  RAISES StaleSource, ValueError (via load_entry, read_expected)
    CALL load_entry(root/MANIFEST_NAME, read_expected(root)) -> manifest
    LOOP (name, record) in manifest['files'].items()
      IF not ENTRY_PATTERN.match(name)
        CONTINUE
      IF (root/'ir'/name).is_file()
        CALL entries.append(...)
    RET entries

F read_expected(root: Path) -> str @L40-45 ^7d02ee189ab4c650 ~L3
  EFFECTS fs:read
  RAISES ValueError
    CALL (root/'.pin').read_text().strip() -> pin
    IF len(pin) != 64
      RAISE ValueError
    RET pin

OMIT assignments=4 blank=9 comments=0 docstrings=4 bodies-tier=L3
FAULT-BEFORE edit(any), exact-literals, regex(L11)
```

What changed and why:

- **`-> var` dataflow form.** `CALL path.read_bytes() -> data` replaces
  `SET data=path.read_bytes()` plus the redundant `CALLS` list.
- **Per-symbol `^handle`.** A collision-resistant SHA-256 prefix combines the
  exact symbol slice digest with stable, position-independent same-module semantic
  context. Callee or decorator changes invalidate dependent handles, while a
  comment-only move can retain the handle and be reported as `moved`.
- **`EFFECTS` / `RAISES`.** Computed in step 3, including propagated facts.
- **`!FAULT(reason)` risk tags.** The compiler stamps lines whose IL rendering is
  known-lossy or high-stakes: regex literals, float/bit math, concurrency
  primitives, truncated strings. Contract: a tagged line may not support a claim
  or an edit without an exact-source fault first.
- **`OMIT` with real counts.** Mechanical per-file numbers instead of boilerplate.
- **One line-span per symbol** instead of per statement.

---

## Step 5 — the source-map handle and the latent-fault trap

Every symbol line is addressable as:

```text
source_rel          : source_sha256 : symbol_path : span
src/cache_loader.py : 412e2610…     : load_entry  : L18-24   (^a4f1c2e9d0b7a631)
```

The failure this closes: an agent read the IL (or its image render) earlier, the
source has since changed — or the image was misread — and the agent edits from
stale context. Nothing traps, because unlike a CPU page fault, a *latent* fault
raises no signal. The defense is a program, not agent discipline:

```bash
python3 kern_cache.py --repo . verify src/cache_loader.py \
  --symbol load_entry --hash a4f1c2e9d0b7a631
```

| Result | Meaning | Required action |
|---|---|---|
| `ok` | Symbol bytes unchanged, same span | proceed |
| `moved` | Same bytes, new span (file shifted) | use returned span |
| `stale` | Symbol bytes or same-file semantic context changed | fault exact source; IL page invalid |

The skill contract makes `verify` mandatory on the edit path: no write to a symbol
that was read from IL or an image without a passing `verify` (or a fresh `fault`)
for that symbol's handle. `ok` and `moved` exit 0; `stale` returns `ok: false`
and exits 1.

---

## Step 6 — cache, render, done

Unchanged from today: the IL page is written to `.kern/ir/…`, the manifest records
`source_sha256` and `ir_sha256`, and the renderer may pack cold pages into lossless
WebP. The codec version bump (`kern-il/0.2`) automatically invalidates every 0.1
page on first contact, exactly as the existing codec-invalidation path already does.

---

## Token accounting

### Small files

The walkthrough output is illustrative, not a generated benchmark record. Small,
dense files can break even or expand after headers and source handles are added.
The runtime therefore skips full IL below its configured size floor and points the
agent to exact source instead.

### Published deterministic corpus

The checked-in deterministic result is
[`benchmarks/results/python-det-v2.json`](../benchmarks/results/python-det-v2.json).
It currently covers one redistributable Python file. Its size bucket and observed
L1/L2/L3 ratios are generated fields in the record, using the documented `chars/4`
estimator. Python 3.13 is the canonical artifact runtime; other supported Python
versions run the behavior suite but may render stdlib AST text differently. The
benchmark also checks each tier's signatures, return data, calls,
effects, raises, unknown-call
counts, tier markers, and function/class source handles against facts independently
extracted from the source AST.

This is useful implementation evidence, not a universal compression rate. The current
generated record classifies its redistributable Python input in the large bucket; the
record itself is authoritative for the observed ratios. New results should publish the
observed ratio for every represented size/language bucket, retain failures rather than
lowering a threshold, and keep exact source as the hot path.

### On micro-compressing keywords

Replacing IL keywords with shorter codes (`CALLS` → `K`, `EFFECTS` → `E`) is
mostly a false economy and is out of scope for 0.2:

- Modern BPE tokenizers already encode common short words as 1–2 tokens; a
  single-letter opcode saves at most ~1 token per line — bounded by roughly 10%
  of an L2 page, before subtracting the legend that must ship with every page.
- Identifiers, which dominate IL tokens, can never be mapped: the agent must
  grep, quote, and edit them verbatim.
- A per-page dictionary is a new latent-fault source — a misremembered mapping is
  exactly the "confidently wrong" failure this design exists to prevent.

Tiering remains the honest compression lever: compare observed L1/L2/L3 costs in
the generated benchmark record instead of assuming a fixed savings percentage.

---

## Verification plan

1. **Determinism:** under the same compiler fingerprint and runtime, compile every
   corpus file twice; outputs must be byte-identical.
2. **Golden files:** fixture source → expected IL, per language, in CI.
3. **Fact fidelity:** for each corpus file and tier, independently extract signatures,
   return data, calls, effects, raises, unknown-call counts, tier markers, and source
   handles from the AST and assert that the IL retains every promised fact.
4. **Token benchmarks:** count source vs. IL tokens (Anthropic count-tokens API
   when available, offline estimator otherwise) across a corpus bucketed by file
   size; publish per-language results under `benchmarks/results/`.
5. **Fallback:** corpus runs with the pinned tree-sitter set absent and with syntax-broken files
   must degrade to the labeled generic baseline, never crash.

---

## React frontend (tsx/jsx)

Step 1's grammar table lists `tree-sitter` for TypeScript and JavaScript, but two
gaps sat under that row: `.tsx` files were routed to the plain TypeScript grammar
(no JSX support, so JSX-bearing `.tsx` degraded to a parse error), and `.jsx`
parsed fine but rendered every component as a truncated `FN` — hooks, state,
effects, events, and render structure were invisible. `kern_react.py` closes both
gaps as a post-pass over the same tree-sitter tree: no new parser, no new format.
`COMPONENT` is a symbol kind inside the existing `ModuleIR`/`Symbol`/`emit_il`
pipeline, so spans, slice hashes, tiers, faults, verify, cache, and redaction are
inherited verbatim.

### Grammar routing

| suffix | grammar |
| --- | --- |
| `.js` `.jsx` `.mjs` `.cjs` | `tree_sitter_javascript` (JSX built in) |
| `.ts` | `tree_sitter_typescript.language_typescript()` |
| `.tsx` | `tree_sitter_typescript.language_tsx()` |

`parse_tsjs(text, typescript: bool = False, tsx: bool = False)` selects the
grammar (`tsx=True` wins over `typescript=True`), and `tsjs_available()` takes
the same flags for per-grammar capability probes. Both call sites
(`kern_cache.py` compile and verify paths) route this way. The React adapter
runs only on the JSX-capable grammars (JavaScript and TSX; plain TypeScript has
no JSX productions). The `generator=` header line reports
`lang=typescript frontend=tree-sitter+react` for a `.tsx` module when the
adapter fires, and plain `frontend=tree-sitter` otherwise — a strict no-op on
non-component code, verified by `TestNoOpOnPlainCode` in `tests/test_react.py`.

A symbol is upgraded to `kind="component"` when it is a function declaration,
function expression, or arrow function, its name matches `^[A-Z]`, and a
`return` (or arrow expression body) contains a `jsx_element`,
`jsx_self_closing_element`, or `jsx_fragment`. `memo(Fn)` / `forwardRef(Fn)`
wrappers are unwrapped: the inner function is lowered, the wrapper noted.
Capitalized functions with no JSX stay plain `FN`; lowercase functions
returning JSX also stay `FN` (not components by React convention).

### Extraction vocabulary

```text
COMPONENT UserCard L3-18 #a3f9c2d1
  PROPS user, onClose?=noop
  STATE open=false
  STATE [state, dispatch]=useReducer(reducer, init)
  CTX theme=useContext(ThemeContext)
  REF inputRef
  HOOK data=useUserData(id)
  EFFECT deps=[user.id]
  EVENT Card.onClick -> set open=true
  RENDER
    Card
      Avatar src=user.avatar
      span {user.name}
      IF open > UserDetails user=user
      FOR item in items > Row key=item.id
```

Rules:

- **PROPS** — first parameter. Destructured pattern lists names with defaults
  (`onClose?=noop` when a default exists; `?` when the TS type marks it optional
  and that is syntactically visible). Non-destructured param renders as its name
  (`props`).
- **STATE** — `const [x, setX] = useState(init)` → `STATE x=init`; setter name
  recorded internally for EVENT lowering. `useReducer` renders the pair and
  arguments.
- **CTX / REF / HOOK** — `useContext`, `useRef`, and custom `use[A-Z]\w*` calls
  respectively. Custom hooks are opaque: call text only, no cross-file
  resolution.
- **EFFECT** — `useEffect` / `useLayoutEffect`. Dependency array rendered
  verbatim (`deps=[user.id]`, `deps=[]`); a missing array renders
  `deps=EVERY-RENDER`. Effect body: L2 shows the head only; L3 summarizes via
  existing `flow()`.
- **EVENT** — JSX attribute matching `on[A-Z]\w+={expr}`. If the handler body is
  a single known-setter call, lower to `set <state>=<arg>`; otherwise render the
  callee name or `flow()`-style summary at L3.
- **RENDER** — JSX tree, indentation = nesting:
  - `{cond && <X/>}` → `IF cond > X`; ternary → `IF cond > X ELSE > Y`
  - `.map(` callback returning JSX → `FOR param in receiver > X`
  - Host elements (lowercase) are structure; text/expression children render as
    `{expr}` capped by existing `ntext` (secret redaction inherited)
  - Capitalized JSX names are component dependencies; names that match imports
    cross-link naturally through the existing import lines
- Non-hook, non-render statements in the component body flow through the
  existing L3 flow-op rendering unchanged (hook calls are skipped there —
  they already surface as STATE/CTX/REF/HOOK/EFFECT heads). Components also
  emit the same `EFFECTS` provenance line as plain functions (effect classes
  plus `unknown-calls=N`) at L2 and L3.
- All line math is `\n`-only, matching the repo rule (never `str.splitlines()`).

### Tier mapping

| Tier | Component detail |
| --- | --- |
| L1 | `COMPONENT name (props) span #hash` — one line, like current FN heads |
| L2 | + STATE/CTX/REF/HOOK/EFFECT/EVENT heads and the `EFFECTS` provenance line; RENDER collapsed to components-only tree (host elements and attributes dropped; IF/FOR structure kept) |
| L3 | Full render tree with attributes, effect bodies, handler bodies, and non-hook body statements via `flow()` |

### Faulting

Ambiguity never disappears silently. Six markers reuse the existing
`!FAULT(...)` inline channel and FAULTS footer:

| Construct | Marker |
| --- | --- |
| Hook called via alias or namespace (`R.useState`, renamed import) | `!FAULT(aliased-hook)` |
| Dynamic component (`<Tag/>` where Tag is a lowercase variable or member expression) | `!FAULT(dynamic-component)` |
| Spread props as the sole prop source (`{...rest}`) | rendered `...rest` + `!FAULT(spread-props)` |
| Render prop / children-as-function | `!FAULT(render-prop)`, body summarized as flow |
| Hook call inside conditional | `!FAULT(conditional-hook)` |
| Render tree exceeding op budget | explicit `…+N` + `!FAULT(render-truncated)` |

Frontend IR remains a reasoning representation, not the write authority: edits
still require faulting exact current source and verifying the slice hash, per
the existing contract (Step 5).

### Corpus run

Compiled at L2 over two real corpora — KERN's own `app/` directory (4 files:
`layout.jsx`, `page.jsx`, `docs/layout.jsx`, `docs/page.jsx`) and a fresh
shallow clone of [`vercel/commerce`](https://github.com/vercel/commerce) (65
`.js`/`.jsx`/`.ts`/`.tsx` files, `node_modules` excluded):

```text
files=69 crashes=0 components=61 faults=8
ratio min=1.9 median=3.3 max=10.1
```

Zero crashes, components detected on both corpora, ambiguous constructs (spread
props, dynamic components, render-prop children) surfaced as faults rather than
silently dropped — the done bar this spec set for Stage 1 MVP.
