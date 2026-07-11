# React Frontend IR — Design

**Date:** 2026-07-11
**Scope:** TSX/JSX grammar routing fix + React semantic extraction (Stage 1 MVP). Next.js adapter, type enrichment, and other frameworks are later specs.

> **Historical note:** this spec predates the compiler-hardening merge from main. The shipped implementation adopts main's `parse_tsjs(text, typescript=, tsx=)` boolean API instead of the `dialect` parameter described below; the semantic vocabulary, tiers, and faulting are as specified. Current behavior: `docs/deterministic-compiler.md`.

## Problem

KERN-IL/0.2 lowers TS/JS through Tree-sitter, but:

1. `.tsx` files are routed to the plain TypeScript grammar (`kern_cache.py` passes `typescript=True`; `parse_tsjs` loads `language_typescript()`). JSX is a syntax error in that grammar, so JSX-bearing `.tsx` files degrade to a parse-error IR. `tree_sitter_typescript.language_tsx()` exists and is unused.
2. `.jsx` parses (the JavaScript grammar includes JSX) but the extractor has no JSX semantics: components render as `FN` symbols whose return statements are truncated JSX text. Hooks, state, effects, events, and render structure — the behavioral core of a React file — are invisible.

React source is a verbose encoding of compact behavior. A deterministic semantic lowering recovers that behavior at a fraction of the tokens.

## Decision summary

- **No new parser.** Tree-sitter JS and TSX grammars provide syntax; KERN supplies React meaning. Writing or committing a custom React grammar is explicitly out of scope — React is semantics on top of JS/TSX syntax, not a new syntax.
- **Extend KERN-IL, not a new format.** `COMPONENT` becomes a new symbol kind inside the existing `ModuleIR`/`Symbol`/`emit_il` pipeline. Spans, slice hashes, tiers, faults, verify, cache, and redaction are inherited verbatim. The runtime model learns one format.
- **Adapter seam, not query packs.** React lowering lives in a new module (`skills/kern/scripts/kern_react.py`) as a post-pass over the same Tree-sitter tree, in the codebase's existing manual-visitor style. Future frameworks (Next.js, Vue, Svelte) become sibling adapters emitting the same vocabulary. Declarative `.scm` query packs are deferred until a second framework proves the need.
- **Tiers stay the compression lever.** Component detail maps onto existing L1/L2/L3; no new knobs.

## Architecture

```text
TSX/JSX source
   ↓
parse_tsjs(text, dialect)          # dialect: "js" | "ts" | "tsx"
   ↓ tree-sitter CST + base symbols (FN/const/class/import)
kern_react.py post-pass            # same tree, upgrades qualifying FN → COMPONENT
   ↓
ModuleIR → emit_il (existing)      # tier rendering, faults, hashes
```

### Grammar routing

| suffix | grammar |
| --- | --- |
| `.js` `.jsx` `.mjs` `.cjs` | `tree_sitter_javascript` (JSX built in) |
| `.ts` | `tree_sitter_typescript.language_typescript()` |
| `.tsx` | `tree_sitter_typescript.language_tsx()` |

`parse_tsjs(text, typescript: bool)` becomes `parse_tsjs(text, dialect: str)`. Both call sites (`kern_cache.py` compile and verify paths) updated. `generator=` header line reports `lang=tsx frontend=tree-sitter+react` when the adapter fires.

### Component detection (deterministic)

A symbol is upgraded to `kind="component"` when:

- it is a function declaration, function expression, or arrow function, AND
- its name matches `^[A-Z]`, AND
- a `return` (or arrow expression body) contains `jsx_element`, `jsx_self_closing_element`, or `jsx_fragment`.

Also detected: `memo(Fn)`, `forwardRef(Fn)` wrappers — the inner function is lowered, the wrapper noted. Capitalized functions with no JSX stay plain `FN`. Lowercase functions returning JSX stay `FN` (not components by React convention).

## Extraction vocabulary

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

- **PROPS** — first parameter. Destructured pattern lists names with defaults (`onClose?=noop` when a default exists; `?` when the TS type marks it optional and that is syntactically visible). Non-destructured param renders as its name (`props`).
- **STATE** — `const [x, setX] = useState(init)` → `STATE x=init`; setter name recorded internally for EVENT lowering. `useReducer` renders the pair and arguments.
- **CTX / REF / HOOK** — `useContext`, `useRef`, and custom `use[A-Z]\w*` calls respectively. Custom hooks are opaque: call text only, no cross-file resolution.
- **EFFECT** — `useEffect` / `useLayoutEffect`. Dependency array rendered verbatim (`deps=[user.id]`, `deps=[]`); a missing array renders `deps=EVERY-RENDER`. Effect body: L2 shows the head only; L3 summarizes via existing `flow()`.
- **EVENT** — JSX attribute matching `on[A-Z]\w+={expr}`. If the handler body is a single known-setter call, lower to `set <state>=<arg>`; otherwise render the callee name or `flow()`-style summary at L3.
- **RENDER** — JSX tree, indentation = nesting:
  - `{cond && <X/>}` → `IF cond > X`; ternary → `IF cond > X ELSE > Y`
  - `.map(` callback returning JSX → `FOR param in receiver > X`
  - Host elements (lowercase) are structure; text/expression children render as `{expr}` capped by existing `ntext` (secret redaction inherited)
  - Capitalized JSX names are component dependencies; names that match imports cross-link naturally through the existing import lines
- Non-hook, non-render statements in the component body flow through the existing L3 flow-op rendering unchanged.
- All line math is `\n`-only, matching the repo rule (never `str.splitlines()`).

## Tier mapping

| Tier | Component detail |
| --- | --- |
| L1 | `COMPONENT name (props) span #hash` — one line, like current FN heads |
| L2 | + STATE/CTX/REF/HOOK/EFFECT/EVENT heads; RENDER collapsed to components-only tree (host elements and attributes dropped; IF/FOR structure kept) |
| L3 | Full render tree with attributes, effect bodies and handler bodies via `flow()` |

## Faulting

Ambiguity never disappears silently. Reuses the existing `!FAULT(...)` inline channel and FAULTS footer:

| Construct | Marker |
| --- | --- |
| Hook called via alias or namespace (`R.useState`, renamed import) | `!FAULT(aliased-hook)` |
| Dynamic component (`<Tag/>` where Tag is a lowercase variable or member expression) | `!FAULT(dynamic-component)` |
| Spread props as the sole prop source (`{...rest}`) | rendered `...rest` + `!FAULT(spread-props)` |
| Render prop / children-as-function | `!FAULT(render-prop)`, body summarized as flow |
| Hook call inside conditional | `!FAULT(conditional-hook)` |
| Render tree exceeding op budget | explicit `…+N` + `!FAULT(render-truncated)` |

Frontend IR remains a reasoning representation, not the write authority: edits still require faulting exact current source and verifying the slice hash, per the existing contract.

## Testing

- **`tests/test_react.py`** — golden fixtures per construct: useState, useReducer, useContext, useRef, custom hook, effect with/without deps, events (setter and non-setter), IF/FOR render lowering, fragments, memo/forwardRef, spread props, dynamic component, aliased hook, conditional hook, `.jsx` and `.tsx` dialects, TSX generics ambiguity (`<T,>` arrow).
- **Regression** — existing `test_tsjs.py` fixtures (no React constructs) must emit byte-identical IR; the adapter must be a strict no-op on non-component code.
- **Corpus** — run compile over KERN's own `app/*.jsx` and a cloned real-world Next.js repo: zero crashes, all ambiguous constructs surface as faults (grep the FAULTS footer), compression ratios recorded per tier for the record. No hard ratio gate — compression is a large-file property and the `min_ir_tokens=600` source-cheaper stub already handles small files.

## Non-goals (this spec)

- Next.js concepts: routes, layouts, `use client`/`use server` boundaries, server actions, metadata (Stage 2 spec)
- Type-aware enrichment via the TypeScript compiler API or LSP (Stage 3 spec)
- Vue / Svelte / Angular / Astro adapters
- Declarative `.scm` query-pack architecture
- A custom Tree-sitter React grammar (never needed)
