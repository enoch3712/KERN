# KERN-IL/0.2 Deterministic Compiler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace model-generated KERN-IL with a deterministic compiler (`kern_compile.py`) that lowers Python and TS/JS source into tiered KERN-IL/0.2 pages with effects, exceptions, per-symbol source-map hashes, and a `verify` CLI verb that traps stale reads.

**Architecture:** A new module `skills/kern/scripts/kern_compile.py` holds language frontends (Python stdlib `ast`; TS/JS via optional tree-sitter) that produce a common symbol model, an effect/raise propagation engine, and a shared tiered emitter (L1/L2/L3). `kern_cache.py` keeps all cache/manifest machinery and calls the compiler from `baseline_for()`; it gains a codec bump to `kern-il/0.2`, a size floor, a `--tier` flag, and a `verify` verb. A benchmark script measures compression per tier and file-size bucket.

**Tech Stack:** Python 3.10+ stdlib only, except optional `tree-sitter`, `tree-sitter-javascript`, `tree-sitter-typescript` (graceful fallback when absent). Tests use stdlib `unittest` (repo has no pytest).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-11-deterministic-il-compiler-design.md`. Walkthrough with format examples: `docs/deterministic-compiler.md`.
- Determinism is a hard invariant: same source bytes → byte-identical IL. Never put timestamps, absolute paths, or environment data in IL output.
- Codec string is exactly `kern-il/0.2`; generator string is exactly `kern-det/0.2`; IL first line is exactly `KERN-IL/0.2`.
- Tier names are exactly `L1`, `L2`, `L3`. Default tier `L2`. Size floor config key `min_ir_tokens`, default `600` (chars/4 estimate).
- `kern_compile.py` must not import `kern_cache.py` (the reverse dependency only, and only as a local import inside functions).
- Secret redaction rules are copied from the existing `kern_cache.py` (`SECRET_NAME`, `SECRET_VALUE`); a likely credential must never appear in IL.
- All scripts must keep passing `python3 -m py_compile skills/kern/scripts/kern_cache.py skills/kern/scripts/render_ir.py skills/kern/scripts/kern_compile.py`.
- Run all tests with `python3 -m unittest discover -s tests -v` from repo root.
- Work on branch `design/deterministic-il-compiler` (already exists, has the spec).

## File Structure

- `skills/kern/scripts/kern_compile.py` — NEW. Symbol model, Python frontend, TS/JS frontend, effect engine, tiered emitter. Self-contained.
- `skills/kern/scripts/kern_cache.py` — MODIFY. Codec bump, `baseline_for` dispatch, size-floor stub, `git_revision`, `--tier`, `verify` verb; delete the old AST baseline (`python_ir`, `function_card`, `outline`, `expr`, `LiteralSanitizer`, `sanitize_string`, `call_name`, `target_text`). Keep `generic_ir` + `redact_line` + `SECRET_*`.
- `benchmarks/token_bench.py` — NEW. Per-tier token benchmark + fidelity check.
- `tests/__init__.py`, `tests/test_kern_compile.py`, `tests/test_effects.py`, `tests/test_emitter.py`, `tests/test_tsjs.py`, `tests/test_cache_integration.py`, `tests/test_verify.py`, `tests/test_token_bench.py` — NEW.
- `skills/kern/SKILL.md`, `docs/architecture.md`, `CHANGELOG.md`, `README.md` — MODIFY (docs, Task 9).

---

### Task 1: Symbol model + Python frontend (signatures, spans, slice hashes)

**Files:**
- Create: `skills/kern/scripts/kern_compile.py`
- Create: `tests/__init__.py` (empty)
- Test: `tests/test_kern_compile.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `kern_compile.parse_python(text: str) -> ModuleIR`; dataclasses `FlowOp(op, detail, binds, depth, line, risk)`, `Symbol(kind, name, span, signature, returns, decorators, slice8, calls, raises, flow, is_async, bases, detail, risk, effects, raises_all, unknown_calls)`, `ModuleIR(lang, frontend, symbols, omit, parse_error)`; helpers `slice_sha8(text, start, end) -> str` (8 hex), `expr_text(node, max_length, secret_hint) -> str`, `sanitize_string(value, secret_hint) -> str`; constants `CODEC_VERSION = "kern-il/0.2"`, `GENERATOR = "kern-det/0.2"`. Later tasks fill `flow` (Task 2) and `effects`/`raises_all` (Task 3).

- [ ] **Step 1: Write the failing test**

Create `tests/__init__.py` (empty file) and `tests/test_kern_compile.py`:

```python
import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "kern" / "scripts"))
import kern_compile  # noqa: E402

SAMPLE = '''"""Module docstring."""

import json
from hashlib import sha256
from pathlib import Path

MANIFEST_NAME = "manifest.json"
API_KEY = "s2_abcdefghijklmnop1234"


class StaleSource(Exception):
    """Raised on hash mismatch."""


def load_entry(path: Path, expected_sha: str) -> dict:
    """Read, verify, parse."""
    data = path.read_bytes()
    current_sha = sha256(data).hexdigest()
    if current_sha != expected_sha:
        raise StaleSource(path)
    return json.loads(data)


class Loader:
    async def fetch(self, url):
        return await self.client.get(url)
'''


class TestPythonFrontend(unittest.TestCase):
    def setUp(self):
        self.mod = kern_compile.parse_python(SAMPLE)

    def sym(self, name):
        return next(s for s in self.mod.symbols if s.name == name)

    def test_module_metadata(self):
        self.assertEqual(self.mod.lang, "python")
        self.assertEqual(self.mod.frontend, "pyast")
        self.assertEqual(self.mod.parse_error, "")

    def test_function_symbol(self):
        f = self.sym("load_entry")
        self.assertEqual(f.kind, "function")
        self.assertIn("path: Path", f.signature)
        self.assertIn("expected_sha: str", f.signature)
        self.assertEqual(f.returns, "dict")
        self.assertFalse(f.is_async)
        self.assertIn("path.read_bytes", f.calls)
        self.assertIn("json.loads", f.calls)
        self.assertEqual(f.raises, ["StaleSource"])

    def test_slice_hash_matches_exact_source_lines(self):
        f = self.sym("load_entry")
        start, end = f.span
        lines = SAMPLE.splitlines(keepends=True)
        expected = hashlib.sha256("".join(lines[start - 1:end]).encode()).hexdigest()[:8]
        self.assertEqual(f.slice8, expected)
        self.assertEqual(SAMPLE.splitlines()[start - 1].strip(), "def load_entry(path: Path, expected_sha: str) -> dict:")

    def test_method_is_qualified_and_async(self):
        m = self.sym("Loader.fetch")
        self.assertTrue(m.is_async)

    def test_class_symbol(self):
        c = self.sym("StaleSource")
        self.assertEqual(c.kind, "class")
        self.assertEqual(c.bases, "Exception")
        self.assertEqual(len(c.slice8), 8)

    def test_secret_const_redacted(self):
        consts = [s for s in self.mod.symbols if s.kind == "const"]
        api = next(s for s in consts if s.name == "API_KEY")
        self.assertNotIn("s2_abcdefghijklmnop1234", api.detail)
        self.assertIn("REDACTED", api.detail)

    def test_imports_collected(self):
        imports = [s for s in self.mod.symbols if s.kind == "import"]
        details = " ".join(s.detail for s in imports)
        self.assertIn("json", details)
        self.assertIn("sha256", details)

    def test_omit_counts(self):
        self.assertGreaterEqual(self.mod.omit["docstrings"], 3)
        self.assertGreaterEqual(self.mod.omit["blank"], 5)

    def test_parse_error_reported(self):
        bad = kern_compile.parse_python("def broken(:\n")
        self.assertNotEqual(bad.parse_error, "")
        self.assertEqual(bad.symbols, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_kern_compile -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kern_compile'`

- [ ] **Step 3: Write minimal implementation**

Create `skills/kern/scripts/kern_compile.py`:

```python
#!/usr/bin/env python3
"""Deterministic KERN-IL/0.2 compiler: language frontends, effect engine, tiered emitter."""

from __future__ import annotations

import ast
import copy
import hashlib
import re
from dataclasses import dataclass, field

CODEC_VERSION = "kern-il/0.2"
GENERATOR = "kern-det/0.2"

SECRET_NAME = re.compile(
    r"(?i)(api[_-]?key|access[_-]?key|auth|bearer|credential|passwd|password|private[_-]?key|secret|token)"
)
SECRET_VALUE = re.compile(
    r"(?i)(?:sk|rk|pk|s2)[_-][A-Za-z0-9_-]{12,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"(?:aws|ghp|github_pat)_[A-Za-z0-9_-]{12,}"
)
SPACE = re.compile(r"\s+")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def slice_sha8(source_text: str, start: int, end: int) -> str:
    lines = source_text.splitlines(keepends=True)
    return sha256_hex("".join(lines[start - 1:end]).encode("utf-8", "surrogatepass"))[:8]


def sanitize_string(value: str, secret_hint: bool = False) -> str:
    digest = sha256_hex(value.encode("utf-8", "surrogatepass"))[:12]
    if secret_hint or SECRET_VALUE.search(value):
        return f"<REDACTED len={len(value)} sha256={digest}>"
    if len(value) > 160:
        return f"<STR len={len(value)} sha256={digest}>"
    return value


class _LiteralSanitizer(ast.NodeTransformer):
    def visit_Constant(self, node: ast.Constant):
        if isinstance(node.value, str):
            return ast.copy_location(ast.Constant(sanitize_string(node.value)), node)
        return node


def expr_text(node: ast.AST | None, max_length: int = 200, secret_hint: bool = False) -> str:
    if node is None:
        return "None"
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        rendered = repr(sanitize_string(node.value, secret_hint))
    else:
        clone = _LiteralSanitizer().visit(copy.deepcopy(node))
        ast.fix_missing_locations(clone)
        try:
            rendered = ast.unparse(clone)
        except Exception:
            rendered = f"<{node.__class__.__name__}>"
    rendered = SPACE.sub(" ", rendered).strip()
    if secret_hint and not rendered.startswith("'<REDACTED"):
        digest = sha256_hex(rendered.encode())[:12]
        rendered = f"<REDACTED_EXPR len={len(rendered)} sha256={digest}>"
    if len(rendered) > max_length:
        digest = sha256_hex(rendered.encode())[:12]
        rendered = rendered[: max_length - 24] + f"…<sha256={digest}>"
    return rendered


def _target(node: ast.AST) -> str:
    try:
        return SPACE.sub(" ", ast.unparse(node)).strip()
    except Exception:
        return f"<{node.__class__.__name__}>"


@dataclass
class FlowOp:
    op: str
    detail: str = ""
    binds: str = ""
    depth: int = 0
    line: int = 0
    risk: str = ""


@dataclass
class Symbol:
    kind: str  # function | class | const | import
    name: str
    span: tuple = (0, 0)
    signature: str = ""
    returns: str = ""
    decorators: list = field(default_factory=list)
    slice8: str = ""
    calls: list = field(default_factory=list)
    raises: list = field(default_factory=list)
    flow: list = field(default_factory=list)
    is_async: bool = False
    bases: str = ""
    detail: str = ""
    risk: str = ""
    effects: dict = field(default_factory=dict)      # effect -> list of via names ([] = direct)
    raises_all: dict = field(default_factory=dict)   # exception -> list of via names
    unknown_calls: int = 0


@dataclass
class ModuleIR:
    lang: str
    frontend: str
    symbols: list
    omit: dict
    parse_error: str = ""


def _import_detail(node: ast.Import | ast.ImportFrom) -> str:
    if isinstance(node, ast.Import):
        return ", ".join(a.name for a in node.names)
    names = ",".join(a.name for a in node.names)
    return f"{node.module or '.'}:{names}"


def _omit_counts(text: str, tree: ast.Module) -> dict:
    lines = text.splitlines()
    comments = sum(1 for l in lines if l.strip().startswith("#"))
    blank = sum(1 for l in lines if not l.strip())
    docstrings = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if ast.get_docstring(node) is not None:
                docstrings += 1
    assigns = sum(1 for n in ast.walk(tree) if isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)))
    return {"docstrings": docstrings, "comments": comments, "blank": blank, "assignments": assigns}


def _function_symbol(node, qualified: str, text: str) -> Symbol:
    calls: list[str] = []
    raises: list[str] = []
    for ch in ast.walk(node):
        if isinstance(ch, ast.Call):
            try:
                name = expr_text(ch.func, 80)
            except Exception:
                name = "<call>"
            if name not in calls:
                calls.append(name)
        elif isinstance(ch, ast.Raise) and ch.exc is not None:
            name = expr_text(ch.exc, 60).split("(")[0]
            if name not in raises:
                raises.append(name)
    start = min([d.lineno for d in node.decorator_list] + [node.lineno])
    end = node.end_lineno or node.lineno
    return Symbol(
        kind="function",
        name=qualified,
        span=(start, end),
        signature=expr_text(node.args, 200),
        returns=expr_text(node.returns, 60) if node.returns else "",
        decorators=[expr_text(d, 60) for d in node.decorator_list],
        slice8=slice_sha8(text, start, end),
        calls=calls,
        raises=raises,
        flow=[],  # filled by flow_ops in Task 2
        is_async=isinstance(node, ast.AsyncFunctionDef),
    )


def parse_python(text: str) -> ModuleIR:
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return ModuleIR("python", "pyast", [], {}, parse_error=f"L{exc.lineno}: {exc.msg}")
    symbols: list[Symbol] = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            symbols.append(Symbol(kind="import", name="", detail=_import_detail(node),
                                  span=(node.lineno, node.end_lineno or node.lineno)))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = ",".join(_target(t) for t in targets)
            hint = bool(SECRET_NAME.search(names))
            symbols.append(Symbol(kind="const", name=names,
                                  detail=expr_text(node.value, 100, hint),
                                  span=(node.lineno, node.end_lineno or node.lineno)))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(_function_symbol(node, node.name, text))
        elif isinstance(node, ast.ClassDef):
            end = node.end_lineno or node.lineno
            symbols.append(Symbol(kind="class", name=node.name,
                                  bases=",".join(expr_text(b, 60) for b in node.bases),
                                  span=(node.lineno, end), slice8=slice_sha8(text, node.lineno, end)))
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.append(_function_symbol(member, f"{node.name}.{member.name}", text))
    return ModuleIR("python", "pyast", symbols, _omit_counts(text, tree))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_kern_compile -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/kern/scripts/kern_compile.py tests/__init__.py tests/test_kern_compile.py
git commit -m "feat: kern_compile symbol model and Python frontend"
```

---

### Task 2: Control-flow extraction with risk tags

**Files:**
- Modify: `skills/kern/scripts/kern_compile.py` (add `flow_ops`, `expr_risk`; wire into `_function_symbol`)
- Test: `tests/test_kern_compile.py` (append test class)

**Interfaces:**
- Consumes: Task 1 dataclasses.
- Produces: `flow_ops(statements: list[ast.stmt], depth=0, budget=200) -> list[FlowOp]`; `expr_risk(node: ast.AST) -> str` returning one of `"regex" | "crypto" | "concurrency" | "math" | ""`. `_function_symbol` now fills `Symbol.flow`. Ops emitted: `CALL` (bare calls and assign-from-call, with `binds`), `IF/ELSE/LOOP/WHILE/WITH/TRY/CATCH/FINALLY/RET/RAISE/AWAIT/YIELD/MATCH/CASE/NESTED/BREAK/CONTINUE`. Plain assignments are intentionally NOT emitted (they are counted in `omit["assignments"]`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_kern_compile.py`:

```python
FLOW_SAMPLE = '''
import re
import threading

PATTERN = re.compile(r"^x+$")


def process(path, items):
    data = path.read_bytes()
    total = 0
    for item in items:
        if item.bad:
            raise ValueError(item)
        total += 1
    try:
        result = transform(data)
    except KeyError as exc:
        log(exc)
    finally:
        cleanup()
    with threading.Lock():
        shared.append(total)
    return result


def transform(data):
    return re.sub(r"a+", "b", data.decode())
'''


class TestFlowOps(unittest.TestCase):
    def setUp(self):
        self.mod = kern_compile.parse_python(FLOW_SAMPLE)
        self.proc = next(s for s in self.mod.symbols if s.name == "process")

    def ops(self):
        return [(o.op, o.depth) for o in self.proc.flow]

    def test_call_with_binds(self):
        first = self.proc.flow[0]
        self.assertEqual(first.op, "CALL")
        self.assertEqual(first.binds, "data")
        self.assertIn("path.read_bytes", first.detail)
        self.assertGreater(first.line, 0)

    def test_plain_assignment_not_emitted(self):
        details = " ".join(o.detail for o in self.proc.flow)
        self.assertNotIn("total = 0", details)

    def test_structure(self):
        ops = self.ops()
        self.assertIn(("LOOP", 0), ops)
        self.assertIn(("IF", 1), ops)
        self.assertIn(("RAISE", 2), ops)
        self.assertIn(("TRY", 0), ops)
        self.assertIn(("CATCH", 0), ops)
        self.assertIn(("FINALLY", 0), ops)
        self.assertIn(("WITH", 0), ops)
        self.assertIn(("RET", 0), ops)

    def test_regex_risk_tagged(self):
        trans = next(s for s in self.mod.symbols if s.name == "transform")
        ret = next(o for o in trans.flow if o.op == "RET")
        self.assertEqual(ret.risk, "regex")

    def test_concurrency_risk_tagged(self):
        withs = [o for o in self.proc.flow if o.op == "WITH"]
        self.assertEqual(withs[0].risk, "concurrency")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_kern_compile.TestFlowOps -v`
Expected: FAIL — flow list empty (`IndexError` / assertion failures).

- [ ] **Step 3: Write implementation**

Add to `kern_compile.py` (above `_function_symbol`), and change `_function_symbol` so `flow=flow_ops(node.body)`:

```python
_RISK_CALL = [
    ("regex", re.compile(r"^re\.(compile|match|search|sub|split|fullmatch|findall|finditer)$")),
    ("crypto", re.compile(r"^(hashlib|hmac|secrets)\.")),
    ("concurrency", re.compile(r"^(threading|asyncio|multiprocessing|concurrent)\.")),
]
_RISK_MATH = re.compile(r"(\*\*|<<|>>)")
_TRY_TYPES = (ast.Try, getattr(ast, "TryStar", ast.Try))


def expr_risk(node: ast.AST | None) -> str:
    if node is None:
        return ""
    for ch in ast.walk(node):
        if isinstance(ch, ast.Call):
            try:
                fn = ast.unparse(ch.func)
            except Exception:
                continue
            for name, rx in _RISK_CALL:
                if rx.search(fn):
                    return name
        elif isinstance(ch, ast.withitem):
            try:
                fn = ast.unparse(ch.context_expr)
            except Exception:
                continue
            if fn.startswith(("threading.", "asyncio.", "multiprocessing.")):
                return "concurrency"
    try:
        if _RISK_MATH.search(ast.unparse(node)):
            return "math"
    except Exception:
        pass
    return ""


def flow_ops(statements: list, depth: int = 0, budget: int = 200) -> list:
    ops: list[FlowOp] = []

    def add(node, op, detail="", binds="", risk=""):
        if len(ops) < budget:
            ops.append(FlowOp(op=op, detail=detail, binds=binds, depth=depth,
                              line=getattr(node, "lineno", 0), risk=risk))

    def sub(body):
        return flow_ops(body, depth + 1, budget - len(ops))

    for s in statements:
        if len(ops) >= budget:
            break
        if isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str):
            continue  # docstring
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            add(s, "NESTED", s.name)
        elif isinstance(s, (ast.Assign, ast.AnnAssign)) and isinstance(getattr(s, "value", None), (ast.Call, ast.Await)):
            targets = s.targets if isinstance(s, ast.Assign) else [s.target]
            names = ",".join(_target(t) for t in targets)
            call = s.value.value if isinstance(s.value, ast.Await) else s.value
            hint = bool(SECRET_NAME.search(names))
            add(s, "CALL", expr_text(call, 120, hint), binds=names, risk=expr_risk(call))
        elif isinstance(s, ast.If):
            add(s, "IF", expr_text(s.test, 100), risk=expr_risk(s.test))
            ops.extend(sub(s.body))
            if s.orelse:
                add(s, "ELSE")
                ops.extend(sub(s.orelse))
        elif isinstance(s, (ast.For, ast.AsyncFor)):
            add(s, "LOOP", f"{_target(s.target)} in {expr_text(s.iter, 100)}")
            ops.extend(sub(s.body))
        elif isinstance(s, ast.While):
            add(s, "WHILE", expr_text(s.test, 100), risk=expr_risk(s.test))
            ops.extend(sub(s.body))
        elif isinstance(s, (ast.With, ast.AsyncWith)):
            detail = ", ".join(expr_text(i.context_expr, 80) for i in s.items)
            risk = ""
            for i in s.items:
                risk = risk or expr_risk(i.context_expr)
            add(s, "WITH", detail, risk=risk)
            ops.extend(sub(s.body))
        elif isinstance(s, _TRY_TYPES):
            add(s, "TRY")
            ops.extend(sub(s.body))
            for h in s.handlers:
                add(h, "CATCH", expr_text(h.type, 60))
                ops.extend(sub(h.body))
            if s.finalbody:
                add(s, "FINALLY")
                ops.extend(sub(s.finalbody))
        elif isinstance(s, ast.Return):
            add(s, "RET", expr_text(s.value, 100), risk=expr_risk(s.value))
        elif isinstance(s, ast.Raise):
            add(s, "RAISE", expr_text(s.exc, 80))
        elif isinstance(s, ast.Match):
            add(s, "MATCH", expr_text(s.subject, 80))
            for case in s.cases:
                add(case, "CASE", expr_text(case.pattern, 60))
                ops.extend(sub(case.body))
        elif isinstance(s, ast.Expr):
            v = s.value
            if isinstance(v, ast.Await):
                add(s, "AWAIT", expr_text(v.value, 120))
            elif isinstance(v, ast.Call):
                add(s, "CALL", expr_text(v, 120), risk=expr_risk(v))
            elif isinstance(v, (ast.Yield, ast.YieldFrom)):
                add(s, "YIELD", expr_text(getattr(v, "value", None), 80))
        elif isinstance(s, (ast.Break, ast.Continue)):
            add(s, s.__class__.__name__.upper())
    return ops[:budget]
```

Note: `ast.Match` requires Python 3.10+ (global constraint). In `_function_symbol`, replace `flow=[]` with `flow=flow_ops(node.body)`.

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest tests.test_kern_compile -v`
Expected: all PASS (Task 1 tests still green).

- [ ] **Step 5: Commit**

```bash
git add skills/kern/scripts/kern_compile.py tests/test_kern_compile.py
git commit -m "feat: control-flow extraction with risk tags"
```

---

### Task 3: Effect tables and effect/raise propagation

**Files:**
- Modify: `skills/kern/scripts/kern_compile.py` (add `EFFECT_RULES`, `classify_call`, `propagate`)
- Test: `tests/test_effects.py`

**Interfaces:**
- Consumes: `ModuleIR`, `Symbol` from Task 1.
- Produces: `classify_call(call_name: str) -> list[str]`; `propagate(module: ModuleIR) -> None` filling `Symbol.effects: dict[str, list[str]]` (effect → sorted via-names, `[]` = direct), `Symbol.raises_all: dict[str, list[str]]`, `Symbol.unknown_calls: int`. Idempotent: calling twice changes nothing (emitter calls it on every emit).

- [ ] **Step 1: Write the failing test**

Create `tests/test_effects.py`:

```python
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "kern" / "scripts"))
import kern_compile  # noqa: E402

SAMPLE = '''
def read_expected(root):
    pin = (root / ".pin").read_text().strip()
    if len(pin) != 64:
        raise ValueError(root)
    return pin


def load_entry(path, expected_sha):
    data = path.read_bytes()
    if not data:
        raise StaleSource(path)
    return data


def find_entries(root):
    manifest = load_entry(root / "m.json", read_expected(root))
    frobnicate(manifest)
    return manifest
'''


class TestClassify(unittest.TestCase):
    def test_fs_read(self):
        self.assertIn("fs:read", kern_compile.classify_call("path.read_bytes"))
        self.assertIn("fs:read", kern_compile.classify_call("open"))
    def test_fs_write(self):
        self.assertIn("fs:write", kern_compile.classify_call("os.replace"))
    def test_proc(self):
        self.assertIn("proc", kern_compile.classify_call("subprocess.run"))
    def test_net(self):
        self.assertIn("net", kern_compile.classify_call("requests.post"))
    def test_unknown(self):
        self.assertEqual(kern_compile.classify_call("frobnicate"), [])


class TestPropagate(unittest.TestCase):
    def setUp(self):
        self.mod = kern_compile.parse_python(SAMPLE)
        kern_compile.propagate(self.mod)

    def sym(self, name):
        return next(s for s in self.mod.symbols if s.name == name)

    def test_direct_effect(self):
        self.assertEqual(self.sym("load_entry").effects.get("fs:read"), [])

    def test_inherited_effect_with_via(self):
        eff = self.sym("find_entries").effects
        self.assertIn("fs:read", eff)
        self.assertIn("load_entry", eff["fs:read"])

    def test_raises_propagate(self):
        ra = self.sym("find_entries").raises_all
        self.assertIn("StaleSource", ra)
        self.assertIn("ValueError", ra)
        self.assertIn("read_expected", ra["ValueError"])

    def test_unknown_counted(self):
        self.assertGreaterEqual(self.sym("find_entries").unknown_calls, 1)

    def test_idempotent(self):
        before = {s.name: dict(s.effects) for s in self.mod.symbols}
        kern_compile.propagate(self.mod)
        after = {s.name: dict(s.effects) for s in self.mod.symbols}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_effects -v`
Expected: FAIL — `AttributeError: module 'kern_compile' has no attribute 'classify_call'`

- [ ] **Step 3: Write implementation**

Add to `kern_compile.py`:

```python
EFFECT_RULES = [
    ("fs:read", re.compile(
        r"^(open|fitz\.open|os\.(walk|listdir|stat|scandir)|os\.path\.(exists|isfile|isdir|getsize|getmtime|basename)"
        r")$|\.(read|readlines|read_text|read_bytes|exists|is_file|is_dir|stat|iterdir|glob)$")),
    ("fs:write", re.compile(
        r"^(os\.(replace|remove|makedirs|mkdir|rename|unlink|chmod)|shutil\.\w+|tempfile\.\w+)$"
        r"|\.(write|writelines|write_text|write_bytes|save|unlink|mkdir|touch|chmod)$")),
    ("net", re.compile(r"^(requests|urllib|socket|http)\.")),
    ("proc", re.compile(r"^(subprocess\.\w+|os\.(system|popen|execv|execve|spawnl))$")),
    ("env", re.compile(r"^os\.(getenv|putenv|environ\.get)$")),
    ("time", re.compile(r"^(time\.(time|sleep|monotonic|time_ns)|datetime\.(now|utcnow|datetime\.now))$|\.sleep$")),
    ("random", re.compile(r"^(random|uuid|secrets)\.")),
    ("console", re.compile(r"^(print|input)$|^logging\.")),
    ("thread", re.compile(r"^(threading|asyncio|multiprocessing|concurrent)\.|^ThreadPoolExecutor$")),
]


def classify_call(call_name: str) -> list:
    name = call_name.split("(")[0].strip()
    return [effect for effect, rx in EFFECT_RULES if rx.search(name)]


def propagate(module: ModuleIR) -> None:
    funcs = [s for s in module.symbols if s.kind == "function"]
    by_tail: dict[str, list] = {}
    for s in funcs:
        by_tail.setdefault(s.name.split(".")[-1], []).append(s)
    for s in funcs:
        if not s.effects:
            s.effects = {e: [] for c in s.calls for e in classify_call(c)}
        if not s.raises_all:
            s.raises_all = {r: [] for r in s.raises}
        unknown = 0
        for c in s.calls:
            tail = c.split("(")[0].split(".")[-1]
            if not classify_call(c) and tail not in by_tail:
                unknown += 1
        s.unknown_calls = unknown
    changed, rounds = True, 0
    while changed and rounds < 32:
        changed, rounds = False, rounds + 1
        for s in funcs:
            for c in s.calls:
                tail = c.split("(")[0].split(".")[-1]
                cands = by_tail.get(tail, [])
                if len(cands) != 1 or cands[0] is s:
                    continue
                callee = cands[0]
                for eff in callee.effects:
                    if eff not in s.effects:
                        s.effects[eff] = [tail]
                        changed = True
                    elif s.effects[eff] and tail not in s.effects[eff]:
                        s.effects[eff] = sorted(s.effects[eff] + [tail])
                for exc in callee.raises_all:
                    if exc not in s.raises_all:
                        s.raises_all[exc] = [tail]
                        changed = True
                    elif s.raises_all[exc] and tail not in s.raises_all[exc]:
                        s.raises_all[exc] = sorted(s.raises_all[exc] + [tail])
```

Note the `if not s.effects:` guards — they make `propagate` idempotent.

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest tests.test_effects tests.test_kern_compile -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/kern/scripts/kern_compile.py tests/test_effects.py
git commit -m "feat: effect tables and effect/raise propagation"
```

---

### Task 4: Tiered emitter

**Files:**
- Modify: `skills/kern/scripts/kern_compile.py` (add `emit_il`, `_function_lines`, `_render_provenanced`)
- Test: `tests/test_emitter.py`

**Interfaces:**
- Consumes: `ModuleIR`, `propagate` (called inside `emit_il`).
- Produces: `emit_il(module: ModuleIR, source_rel: str, source_sha256: str, repo_revision: str = "none", tier: str = "L2") -> str`. Output format per spec: 5 header lines + blank, `IMPORTS`, `C`, `CLASS`, `F` blocks, `OMIT`, `FAULT-BEFORE`. Task 6 calls this from `kern_cache.baseline_for`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_emitter.py`:

```python
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "kern" / "scripts"))
import kern_compile  # noqa: E402

SAMPLE = '''
import re
from pathlib import Path

TOKEN = "ghp_abcdefghijklmnop1234"
PATTERN = re.compile(r"^[a-z]+$")


def load_entry(path: Path, expected_sha: str) -> dict:
    data = path.read_bytes()
    if not data:
        raise ValueError(path)
    return parse(data)


def parse(data):
    return data.decode()
'''


def emit(tier):
    mod = kern_compile.parse_python(SAMPLE)
    return kern_compile.emit_il(mod, "src/x.py", "a" * 64, "d7e8242", tier)


class TestEmitter(unittest.TestCase):
    def test_header(self):
        il = emit("L2").splitlines()
        self.assertEqual(il[0], "KERN-IL/0.2")
        self.assertEqual(il[1], "source_rel=src/x.py")
        self.assertEqual(il[2], "source_sha256=" + "a" * 64)
        self.assertEqual(il[3], "repo_revision=d7e8242")
        self.assertIn("generator=kern-det/0.2 lang=python frontend=pyast tier=L2", il[4])

    def test_function_line_format(self):
        il = emit("L2")
        fline = next(l for l in il.splitlines() if l.startswith("F load_entry"))
        self.assertRegex(fline, r"^F load_entry\(.+\) -> dict @L\d+-\d+ \^[0-9a-f]{8} ~L2$")

    def test_tier_l1_has_no_flow(self):
        il = emit("L1")
        self.assertNotIn("  IF", il)
        self.assertIn("CALLS", il)
        self.assertIn("EFFECTS fs:read", il)
        self.assertIn("RAISES ValueError", il)

    def test_tier_l2_flow_without_expressions(self):
        il = emit("L2")
        body = [l for l in il.splitlines() if l.startswith("    ")]
        joined = "\n".join(body)
        self.assertIn("IF", joined)
        self.assertIn("RAISE", joined)
        self.assertNotIn("not data", joined)

    def test_tier_l3_flow_with_expressions_and_binds(self):
        il = emit("L3")
        self.assertIn("CALL path.read_bytes() -> data", il)
        self.assertIn("IF not data", il)

    def test_l3_is_larger_than_l2_is_larger_than_l1(self):
        self.assertGreater(len(emit("L3")), len(emit("L2")))
        self.assertGreater(len(emit("L2")), len(emit("L1")))

    def test_secret_never_in_output(self):
        for tier in ("L1", "L2", "L3"):
            self.assertNotIn("ghp_abcdefghijklmnop1234", emit(tier))

    def test_regex_const_fault_tagged(self):
        il = emit("L2")
        cline = next(l for l in il.splitlines() if l.startswith("C PATTERN"))
        self.assertIn("!FAULT(regex)", cline)
        self.assertIn("regex(L", il.splitlines()[-1])

    def test_omit_counts_and_fault_before(self):
        lines = emit("L2").splitlines()
        self.assertTrue(lines[-2].startswith("OMIT "))
        self.assertIn("bodies-tier=L2", lines[-2])
        self.assertTrue(lines[-1].startswith("FAULT-BEFORE edit(any), exact-literals"))

    def test_deterministic(self):
        self.assertEqual(emit("L2"), emit("L2"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_emitter -v`
Expected: FAIL — `AttributeError: ... no attribute 'emit_il'`

- [ ] **Step 3: Write implementation**

Add to `kern_compile.py`. Also extend `parse_python` const handling to set `risk`: after building the const `Symbol`, set `symbol.risk = expr_risk(node.value)` (requires `expr_risk` from Task 2 — it is already defined above `parse_python` after Task 2 reordering; if not, move it above).

```python
_TIER_LEVEL = {"L1": 1, "L2": 2, "L3": 3}
_ELIDED = ("<REDACTED", "…<sha256=", "<STR ")


def _render_provenanced(mapping: dict, unknown: int) -> str:
    parts = []
    for key in sorted(mapping):
        vias = mapping[key]
        parts.append(key + (f" (via {', '.join(sorted(vias))})" if vias else ""))
    if unknown:
        parts.append(f"unknown-calls={unknown}")
    return ", ".join(parts)


def _function_lines(s: Symbol, level: int, tier: str, faults: list) -> list:
    head = "ASYNC F" if s.is_async else "F"
    lines = [f"{head} {s.name}({s.signature}) -> {s.returns or 'Any'} "
             f"@L{s.span[0]}-{s.span[1]} ^{s.slice8} ~{tier}"]
    if s.decorators:
        lines.append("  DECORATORS " + ", ".join(s.decorators))
    if s.calls:
        shown = s.calls[:25]
        extra = f" …+{len(s.calls) - 25}" if len(s.calls) > 25 else ""
        lines.append("  CALLS " + ", ".join(shown) + extra)
    effects = _render_provenanced(s.effects, s.unknown_calls)
    if effects:
        lines.append("  EFFECTS " + effects)
    raises = _render_provenanced(s.raises_all, 0)
    if raises:
        lines.append("  RAISES " + raises)
    if level >= 2:
        for op in s.flow:
            pad = "  " * (op.depth + 2)
            piece = op.op
            if level == 3 and op.detail:
                piece += f" {op.detail}"
            elif level == 2 and op.op in ("CATCH", "NESTED", "CASE") and op.detail:
                piece += f" {op.detail}"
            if level == 3 and op.binds:
                piece += f" -> {op.binds}"
            risk = op.risk
            if not risk and level == 3 and any(m in op.detail for m in _ELIDED):
                risk = "elided-literal"
            if risk:
                piece += f" !FAULT({risk})"
                faults.append(f"{risk}(L{op.line})")
            lines.append(pad + piece)
    return lines


def emit_il(module: ModuleIR, source_rel: str, source_sha256: str,
            repo_revision: str = "none", tier: str = "L2") -> str:
    level = _TIER_LEVEL[tier]
    propagate(module)
    out = [
        "KERN-IL/0.2",
        f"source_rel={source_rel}",
        f"source_sha256={source_sha256}",
        f"repo_revision={repo_revision}",
        f"generator={GENERATOR} lang={module.lang} frontend={module.frontend} tier={tier}",
        "",
    ]
    faults: list[str] = []
    imports = [s for s in module.symbols if s.kind == "import"]
    if imports:
        lo = min(s.span[0] for s in imports)
        hi = max(s.span[1] for s in imports)
        out.append(f"IMPORTS {'; '.join(s.detail for s in imports)} @L{lo}-{hi}")
    for s in module.symbols:
        if s.kind == "const":
            tag = ""
            if s.risk:
                tag = f" !FAULT({s.risk})"
                faults.append(f"{s.risk}(L{s.span[0]})")
            out.append(f"C {s.name}={s.detail} @L{s.span[0]}{tag}")
    for s in module.symbols:
        if s.kind == "class":
            out.extend(["", f"CLASS {s.name}({s.bases}) @L{s.span[0]}-{s.span[1]} ^{s.slice8}"])
        elif s.kind == "function":
            out.append("")
            out.extend(_function_lines(s, level, tier, faults))
    omit = " ".join(f"{k}={v}" for k, v in sorted(module.omit.items()))
    out.extend([
        "",
        f"OMIT {omit} bodies-tier={tier}",
        "FAULT-BEFORE edit(any), exact-literals"
        + "".join(f", {f}" for f in dict.fromkeys(faults)),
    ])
    return "\n".join(out).rstrip() + "\n"
```

- [ ] **Step 4: Run all tests**

Run: `python3 -m unittest discover -s tests -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/kern/scripts/kern_compile.py tests/test_emitter.py
git commit -m "feat: tiered KERN-IL/0.2 emitter with fault tags and omit counts"
```

---

### Task 5: TS/JS tree-sitter frontend (optional dependency)

**Files:**
- Modify: `skills/kern/scripts/kern_compile.py` (add `tsjs_available`, `parse_tsjs`)
- Test: `tests/test_tsjs.py`

**Interfaces:**
- Consumes: `Symbol`, `FlowOp`, `ModuleIR`, `emit_il`.
- Produces: `tsjs_available() -> bool`; `parse_tsjs(text: str, typescript: bool = False) -> ModuleIR` with `frontend="tree-sitter"`, `lang="typescript"|"javascript"`. Tests skip when tree-sitter absent. Task 6 dispatches on file suffix.

- [ ] **Step 1: Try installing the optional dependency**

Run: `python3 -m pip install --user tree-sitter tree-sitter-javascript tree-sitter-typescript`
If install fails (no network/permission), proceed anyway — tests skip and the fallback path is exercised instead. Do not add these to any requirements file; they stay optional.

- [ ] **Step 2: Write the failing test**

Create `tests/test_tsjs.py`:

```python
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "kern" / "scripts"))
import kern_compile  # noqa: E402

TS_SAMPLE = '''
import { readFile } from "fs/promises";

const MAX = 10;

export class Loader {
  async fetch(url: string): Promise<string> {
    const data = await readFile(url);
    if (!data) {
      throw new Error(url);
    }
    return data.toString();
  }
}

export function parse(raw: string): number {
  return Number(raw);
}
'''


class TestAvailability(unittest.TestCase):
    def test_available_returns_bool(self):
        self.assertIsInstance(kern_compile.tsjs_available(), bool)


@unittest.skipUnless(kern_compile.tsjs_available(), "tree-sitter not installed")
class TestTsFrontend(unittest.TestCase):
    def setUp(self):
        self.mod = kern_compile.parse_tsjs(TS_SAMPLE, typescript=True)

    def sym(self, name):
        return next(s for s in self.mod.symbols if s.name == name)

    def test_module_metadata(self):
        self.assertEqual(self.mod.lang, "typescript")
        self.assertEqual(self.mod.frontend, "tree-sitter")

    def test_function_and_method(self):
        f = self.sym("parse")
        self.assertEqual(f.kind, "function")
        self.assertIn("raw: string", f.signature)
        m = self.sym("Loader.fetch")
        self.assertTrue(m.is_async)
        self.assertIn("readFile", " ".join(m.calls))

    def test_flow_and_raises(self):
        m = self.sym("Loader.fetch")
        ops = [o.op for o in m.flow]
        self.assertIn("IF", ops)
        self.assertIn("RAISE", ops)
        self.assertIn("RET", ops)
        self.assertIn("Error", m.raises)

    def test_slice_hash_present(self):
        self.assertEqual(len(self.sym("parse").slice8), 8)

    def test_emit_works(self):
        il = kern_compile.emit_il(self.mod, "src/x.ts", "b" * 64, "none", "L2")
        self.assertTrue(il.startswith("KERN-IL/0.2"))
        self.assertIn("F parse", il)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 -m unittest tests.test_tsjs -v`
Expected: FAIL — `AttributeError: ... no attribute 'tsjs_available'` (or, with tree-sitter installed, missing `parse_tsjs`).

- [ ] **Step 4: Write implementation**

Add to `kern_compile.py`:

```python
def tsjs_available() -> bool:
    try:
        import tree_sitter            # noqa: F401
        import tree_sitter_javascript  # noqa: F401
        import tree_sitter_typescript  # noqa: F401
        return True
    except ImportError:
        return False


_TS_FLOW = {
    "if_statement": "IF", "for_statement": "LOOP", "for_in_statement": "LOOP",
    "while_statement": "WHILE", "do_statement": "WHILE", "try_statement": "TRY",
    "return_statement": "RET", "throw_statement": "RAISE", "switch_statement": "MATCH",
}
_TS_FUNC_NODES = {"function_declaration", "generator_function_declaration", "method_definition"}


def parse_tsjs(text: str, typescript: bool = False) -> ModuleIR:
    from tree_sitter import Language, Parser
    if typescript:
        import tree_sitter_typescript as ts_lang
        language = Language(ts_lang.language_typescript())
        lang_name = "typescript"
    else:
        import tree_sitter_javascript as js_lang
        language = Language(js_lang.language())
        lang_name = "javascript"
    parser = Parser(language)
    tree = parser.parse(text.encode("utf-8"))
    raw = text.encode("utf-8")

    def ntext(n, cap=120):
        piece = raw[n.start_byte:n.end_byte].decode("utf-8", "replace")
        piece = SPACE.sub(" ", piece).strip()
        if SECRET_VALUE.search(piece):
            return sanitize_string(piece, secret_hint=True)
        return piece[:cap]

    def span(n):
        return (n.start_point[0] + 1, n.end_point[0] + 1)

    def field_node(n, name):
        return n.child_by_field_name(name)

    def collect_calls(n, acc):
        if n.type == "call_expression":
            fn = field_node(n, "function")
            if fn is not None:
                name = ntext(fn, 80)
                if name not in acc:
                    acc.append(name)
        for c in n.children:
            collect_calls(c, acc)

    def collect_raises(n, acc):
        if n.type == "throw_statement":
            body = ntext(n, 80)
            name = body.removeprefix("throw").strip().removeprefix("new").strip()
            name = name.split("(")[0].rstrip(";").strip()
            if name and name not in acc:
                acc.append(name)
        for c in n.children:
            collect_raises(c, acc)

    def flow(n, depth=0, budget=200):
        ops = []

        def add(node, op, detail="", binds=""):
            if len(ops) < budget:
                ops.append(FlowOp(op=op, detail=detail, binds=binds, depth=depth,
                                  line=node.start_point[0] + 1))

        for c in n.named_children:
            if len(ops) >= budget:
                break
            t = c.type
            if t in _TS_FLOW:
                cond = field_node(c, "condition")
                detail = ntext(cond, 100).strip("()") if cond is not None else ""
                if t == "return_statement":
                    detail = ntext(c, 100).removeprefix("return").strip().rstrip(";")
                if t == "throw_statement":
                    detail = ntext(c, 80).removeprefix("throw").strip().rstrip(";")
                add(c, _TS_FLOW[t], detail)
                for name in ("body", "consequence"):
                    inner = field_node(c, name)
                    if inner is not None:
                        ops.extend(flow(inner, depth + 1, budget - len(ops)))
                alt = field_node(c, "alternative")
                if alt is not None:
                    add(c, "ELSE")
                    ops.extend(flow(alt, depth + 1, budget - len(ops)))
                handler = field_node(c, "handler")
                if handler is not None:
                    add(handler, "CATCH", ntext(field_node(handler, "parameter") or handler, 40))
                    hbody = field_node(handler, "body")
                    if hbody is not None:
                        ops.extend(flow(hbody, depth + 1, budget - len(ops)))
            elif t == "expression_statement" and c.named_children and c.named_children[0].type in ("call_expression", "await_expression"):
                add(c, "CALL", ntext(c.named_children[0], 120))
            elif t in ("lexical_declaration", "variable_declaration"):
                for d in c.named_children:
                    if d.type == "variable_declarator":
                        value = field_node(d, "value")
                        if value is not None and value.type in ("call_expression", "await_expression"):
                            name = field_node(d, "name")
                            add(d, "CALL", ntext(value, 120), binds=ntext(name, 40) if name is not None else "")
            else:
                ops.extend(flow(c, depth, budget - len(ops)))
        return ops[:budget]

    def function_symbol(node, qualified):
        calls, raises = [], []
        collect_calls(node, calls)
        collect_raises(node, raises)
        params = field_node(node, "parameters")
        rtype = field_node(node, "return_type")
        body = field_node(node, "body")
        a, b = span(node)
        is_async = any(ch.type == "async" for ch in node.children)
        return Symbol(
            kind="function", name=qualified, span=(a, b),
            signature=ntext(params, 200).strip("()") if params is not None else "",
            returns=ntext(rtype, 60).lstrip(": ") if rtype is not None else "",
            slice8=slice_sha8(text, a, b), calls=calls, raises=raises,
            flow=flow(body) if body is not None else [], is_async=is_async,
        )

    symbols: list[Symbol] = []

    def top(n, class_prefix=""):
        for c in n.named_children:
            t = c.type
            if t in ("import_statement",):
                symbols.append(Symbol(kind="import", name="", detail=ntext(c, 120), span=span(c)))
            elif t in ("lexical_declaration", "variable_declaration") and not class_prefix:
                for d in c.named_children:
                    if d.type == "variable_declarator":
                        name = field_node(d, "name")
                        value = field_node(d, "value")
                        if value is not None and value.type in ("arrow_function", "function_expression"):
                            symbols.append(function_symbol(value, ntext(name, 60)))
                        elif name is not None:
                            detail = ntext(value, 100) if value is not None else ""
                            hint = bool(SECRET_NAME.search(ntext(name, 60)))
                            if hint:
                                detail = sanitize_string(detail, secret_hint=True)
                            symbols.append(Symbol(kind="const", name=ntext(name, 60), detail=detail, span=span(c)))
            elif t in _TS_FUNC_NODES:
                name = field_node(c, "name")
                qual = (class_prefix + ntext(name, 60)) if name is not None else class_prefix + "<anonymous>"
                symbols.append(function_symbol(c, qual))
            elif t == "class_declaration":
                name = field_node(c, "name")
                cname = ntext(name, 60) if name is not None else "<anonymous>"
                a, b = span(c)
                symbols.append(Symbol(kind="class", name=cname, span=(a, b), slice8=slice_sha8(text, a, b)))
                body = field_node(c, "body")
                if body is not None:
                    top(body, class_prefix=cname + ".")
            elif t in ("export_statement", "program", "statement_block"):
                top(c, class_prefix)
    top(tree.root_node)

    lines = text.splitlines()
    omit = {
        "docstrings": 0,
        "comments": sum(1 for l in lines if l.strip().startswith("//")),
        "blank": sum(1 for l in lines if not l.strip()),
        "assignments": 0,
    }
    return ModuleIR(lang_name, "tree-sitter", symbols, omit)
```

- [ ] **Step 5: Run tests**

Run: `python3 -m unittest tests.test_tsjs -v`
Expected: PASS (or SKIP with "tree-sitter not installed" — both acceptable; `TestAvailability` must pass either way). If tree-sitter IS installed and node-type names mismatch the grammar version, debug with `python3 -c "import kern_compile; m = kern_compile.parse_tsjs(open('tests/test_tsjs.py').read().split(\"'''\")[1], True); print([s.name for s in m.symbols])"` and adjust `_TS_FLOW`/node names — grammar node types are stable across recent versions but `parameter` vs `parameters` on catch clauses varies.

- [ ] **Step 6: Commit**

```bash
git add skills/kern/scripts/kern_compile.py tests/test_tsjs.py
git commit -m "feat: TS/JS tree-sitter frontend with graceful absence"
```

---

### Task 6: Integrate compiler into kern_cache (codec bump, dispatch, size floor, --tier)

**Files:**
- Modify: `skills/kern/scripts/kern_cache.py`
- Test: `tests/test_cache_integration.py`

**Interfaces:**
- Consumes: `kern_compile.parse_python`, `parse_tsjs`, `tsjs_available`, `emit_il` (local import inside `baseline_for`).
- Produces: `baseline_for(root, source, relative, digest, config, tier=None) -> str` (new signature); `git_revision(root: Path) -> str`; `ensure_file(root, paths, relative, source, config, tier=None)`; `prepare_file(root, paths, relative, source, config)`; constants `CODEC_VERSION = "kern-il/0.2"`, `BASELINE_GENERATOR = "kern-det/0.2"`, `TSJS_SUFFIXES = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}`; config keys `min_ir_tokens: 600`, `default_tier: "L2"`; manifest field `ir_tier`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cache_integration.py`:

```python
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "kern" / "scripts"))
import kern_cache  # noqa: E402

BIG_PY = '"""Doc."""\n\nimport json\n\n' + "\n\n".join(
    f'def fn_{i}(path, n):\n'
    f'    """Doc {i}."""\n'
    f'    data = path.read_bytes()\n'
    f'    if not data:\n'
    f'        raise ValueError(n)\n'
    f'    return json.loads(data)\n'
    for i in range(30)
)


class TestCacheIntegration(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "big.py").write_text(BIG_PY)
        (self.root / "tiny.py").write_text("X = 1\n")
        self.paths, self.config = kern_cache.initialize(self.root)

    def ensure(self, name, tier=None):
        rel, src = kern_cache.normalize_rel(self.root, name)
        return kern_cache.ensure_file(self.root, self.paths, rel, src, self.config, tier=tier)

    def test_codec_is_0_2(self):
        self.assertEqual(kern_cache.CODEC_VERSION, "kern-il/0.2")

    def test_big_python_file_gets_deterministic_il(self):
        result = self.ensure("big.py")
        il = Path(result["ir"]).read_text()
        self.assertTrue(il.startswith("KERN-IL/0.2"))
        self.assertIn("tier=L2", il)
        self.assertIn("F fn_0(", il)
        self.assertIn("EFFECTS fs:read", il)
        self.assertIn("RAISES ValueError", il)

    def test_tier_override(self):
        result = self.ensure("big.py", tier="L1")
        il = Path(result["ir"]).read_text()
        self.assertIn("tier=L1", il)
        self.assertNotIn("    IF", il)
        manifest = json.loads((self.paths["manifest"]).read_text())
        self.assertEqual(manifest["files"]["big.py"]["ir_tier"], "L1")

    def test_tiny_file_gets_source_cheaper_stub(self):
        result = self.ensure("tiny.py")
        il = Path(result["ir"]).read_text()
        self.assertIn("mode=source-cheaper", il)
        self.assertNotIn("F ", il)

    def test_syntax_error_falls_back_to_generic(self):
        (self.root / "broken.py").write_text("def broken(:\n    pass\n" * 300)
        il = Path(self.ensure("broken.py")["ir"]).read_text()
        self.assertIn("mode=generic-line-baseline", il)

    def test_repo_revision_header_present(self):
        il = Path(self.ensure("big.py")["ir"]).read_text()
        self.assertIn("repo_revision=", il)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_cache_integration -v`
Expected: FAIL — `TypeError: ensure_file() got an unexpected keyword argument` and codec assertion failure.

- [ ] **Step 3: Modify `kern_cache.py`**

Apply these changes:

1. Constants (`kern_cache.py:22-24`):
```python
CODEC_VERSION = "kern-il/0.2"
BASELINE_GENERATOR = "kern-det/0.2"
TSJS_SUFFIXES = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}
```
2. `DEFAULT_CONFIG`: add `"min_ir_tokens": 600,` and `"default_tier": "L2",` after `"image_profile": "dense",`.
3. Add after `git_files`:
```python
def git_revision(root: Path) -> str:
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False, text=True,
        )
        if head.returncode != 0:
            return "none"
        sha = head.stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False, text=True,
        )
        return f"dirty:{sha}" if dirty.stdout.strip() else sha
    except OSError:
        return "none"
```
4. Delete `sanitize_string`, `LiteralSanitizer`, `expr`, `target_text`, `call_name`, `outline`, `function_card`, `python_ir` (lines ~366-568). Keep `SECRET_NAME`, `SECRET_VALUE`, `SPACE`, `redact_line`, `generic_ir`, `GENERIC_KEEP`.
5. Replace `baseline_for` with:
```python
def stub_ir(text: str, relative: str, digest: str) -> str:
    lines = [
        CODEC_VERSION.upper(),
        f"source_rel={relative}",
        f"source_sha256={digest}",
        f"generator={BASELINE_GENERATOR}",
        "mode=source-cheaper",
        f"QA source is ~{max(1, len(text) // 4)} tokens ({len(text.splitlines())} lines), below the IL floor; fault exact source.",
    ]
    return "\n".join(lines) + "\n"


def baseline_for(root: Path, source: Path, relative: str, digest: str,
                 config: dict[str, Any], tier: str | None = None) -> tuple[str, str]:
    """Return (il_text, tier_used)."""
    text = source.read_text(encoding="utf-8", errors="replace")
    if max(1, len(text) // 4) < int(config.get("min_ir_tokens", 600)):
        return stub_ir(text, relative, digest), "stub"
    selected = tier or str(config.get("default_tier", "L2"))
    note = "generic language fallback"
    try:
        import kern_compile
        suffix = source.suffix.lower()
        module = None
        if suffix == ".py":
            module = kern_compile.parse_python(text)
        elif suffix in TSJS_SUFFIXES and kern_compile.tsjs_available():
            module = kern_compile.parse_tsjs(text, typescript=suffix in {".ts", ".tsx"})
        if module is not None:
            if module.parse_error:
                note = f"parse failed: {module.parse_error}"
            else:
                revision = git_revision(root)
                return kern_compile.emit_il(module, relative, digest, revision, selected), selected
    except Exception as exc:
        note = f"deterministic compiler failed: {exc}"
    return generic_ir(text, relative, digest, note), "generic"
```
Note: the IL first line is `KERN-IL/0.2` from both `emit_il` and `generic_ir`/`stub_ir` (they use `CODEC_VERSION.upper()`), so `commit_file`'s existing first-line check keeps working unchanged.
6. `ensure_file`: change signature to `def ensure_file(root, paths, relative, source, config, tier=None)`; replace the two lines
```python
        ir = baseline_for(source, relative, digest)
```
with
```python
        ir, tier_used = baseline_for(root, source, relative, digest, config, tier)
```
and inside the manifest update dict add `"ir_tier": tier_used,` next to `"ir_generator": BASELINE_GENERATOR,`. Also: when `tier` is explicitly passed and differs from the recorded `record.get("ir_tier")`, treat the cache as unusable (add `and (tier is None or record.get("ir_tier") == tier)` to the `usable = (...)` expression).
7. `prepare_file`: change signature to `(root, paths, relative, source, config)` and its call to `ensure_file(root, paths, relative, source, config)`.
8. `sync_cache`: change its `ensure_file(root, paths, relative, source)` call to `ensure_file(root, paths, relative, source, config)`.
9. `parse_args`: on the `ensure` subparser add `command.add_argument("--tier", choices=("L1", "L2", "L3"))` — restructure the `for name in ("ensure", "prepare", "paths"):` loop into individual subparser blocks so only `ensure` gets `--tier`.
10. `main`: `ensure` branch becomes `result = ensure_file(root, paths, relative, source, config, tier=getattr(args, "tier", None))`; `prepare` branch passes `config`.

- [ ] **Step 4: Run all tests + compile check**

Run: `python3 -m unittest discover -s tests -v && python3 -m py_compile skills/kern/scripts/kern_cache.py skills/kern/scripts/kern_compile.py skills/kern/scripts/render_ir.py`
Expected: all PASS, no compile errors.

- [ ] **Step 5: Smoke-test on this repo**

Run: `python3 skills/kern/scripts/kern_cache.py --repo . scan && python3 skills/kern/scripts/kern_cache.py --repo . ensure skills/kern/scripts/kern_cache.py && head -20 .kern/ir/skills/kern/scripts/kern_cache.py.kern-il.txt && rm -rf .kern`
Expected: JSON success output; IL starts with `KERN-IL/0.2`, contains `tier=L2` and `F ` lines.

- [ ] **Step 6: Commit**

```bash
git add skills/kern/scripts/kern_cache.py tests/test_cache_integration.py
git commit -m "feat: kern-il/0.2 codec, deterministic baseline dispatch, size floor, --tier"
```

---

### Task 7: `verify` CLI verb

**Files:**
- Modify: `skills/kern/scripts/kern_cache.py` (add `verify_symbol`, CLI wiring)
- Test: `tests/test_verify.py`

**Interfaces:**
- Consumes: `kern_compile.parse_python` / `parse_tsjs` (local import), Task 6 constants.
- Produces: `verify_symbol(root, paths, relative, source, symbol: str, expected_hash: str, expected_span: str | None) -> dict` with `result` ∈ `"ok" | "moved" | "stale"`; CLI `kern_cache.py --repo R verify FILE --symbol NAME --hash HHHHHHHH [--span La-b]`. Task 9 documents it in SKILL.md.

- [ ] **Step 1: Write the failing test**

Create `tests/test_verify.py`:

```python
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "kern" / "scripts"))
import kern_cache  # noqa: E402
import kern_compile  # noqa: E402

SRC = '''import json


def load_entry(path, expected_sha):
    data = path.read_bytes()
    if not data:
        raise ValueError(path)
    return json.loads(data)
'''


class TestVerify(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.file = self.root / "mod.py"
        self.file.write_text(SRC)
        self.paths, self.config = kern_cache.initialize(self.root)
        mod = kern_compile.parse_python(SRC)
        self.sym = next(s for s in mod.symbols if s.name == "load_entry")

    def verify(self, expected_hash, span=None):
        rel, src = kern_cache.normalize_rel(self.root, "mod.py")
        return kern_cache.verify_symbol(self.root, self.paths, rel, src,
                                        "load_entry", expected_hash, span)

    def test_ok(self):
        r = self.verify(self.sym.slice8, f"L{self.sym.span[0]}-{self.sym.span[1]}")
        self.assertEqual(r["result"], "ok")

    def test_moved_when_file_shifts(self):
        self.file.write_text("# new comment line\n" + SRC)
        r = self.verify(self.sym.slice8, f"L{self.sym.span[0]}-{self.sym.span[1]}")
        self.assertEqual(r["result"], "moved")
        self.assertIn("current_span", r)

    def test_stale_when_body_changes(self):
        self.file.write_text(SRC.replace("json.loads(data)", "json.loads(data.strip())"))
        r = self.verify(self.sym.slice8)
        self.assertEqual(r["result"], "stale")
        self.assertEqual(r["reason"], "symbol-bytes-changed")

    def test_stale_when_symbol_deleted(self):
        self.file.write_text("import json\n")
        r = self.verify(self.sym.slice8)
        self.assertEqual(r["result"], "stale")
        self.assertEqual(r["reason"], "symbol-not-found")

    def test_unsupported_suffix_raises(self):
        (self.root / "x.rb").write_text("def x; end\n" * 200)
        rel, src = kern_cache.normalize_rel(self.root, "x.rb")
        with self.assertRaises(ValueError):
            kern_cache.verify_symbol(self.root, self.paths, rel, src, "x", "deadbeef", None)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_verify -v`
Expected: FAIL — `AttributeError: ... no attribute 'verify_symbol'`

- [ ] **Step 3: Write implementation**

Add to `kern_cache.py` (after `fault_source`):

```python
def verify_symbol(root: Path, paths: dict[str, Path], relative: str, source: Path,
                  symbol: str, expected_hash: str, expected_span: str | None = None) -> dict[str, Any]:
    import kern_compile
    text = source.read_text(encoding="utf-8", errors="replace")
    suffix = source.suffix.lower()
    if suffix == ".py":
        module = kern_compile.parse_python(text)
    elif suffix in TSJS_SUFFIXES and kern_compile.tsjs_available():
        module = kern_compile.parse_tsjs(text, typescript=suffix in {".ts", ".tsx"})
    else:
        raise ValueError(f"verify does not support {suffix or 'this file type'}; use fault with --expect-sha")
    if module.parse_error:
        raise RuntimeError(f"current source does not parse ({module.parse_error}); fault exact source")
    base = {"ok": True, "operation": "verify", "source_rel": relative, "symbol": symbol,
            "source_sha256": sha256_bytes(text.encode("utf-8", "surrogatepass"))}
    matches = [s for s in module.symbols if s.kind in {"function", "class"} and s.name == symbol]
    if not matches:
        return {**base, "result": "stale", "reason": "symbol-not-found"}
    found = matches[0]
    current_span = f"L{found.span[0]}-{found.span[1]}"
    if found.slice8 != expected_hash:
        return {**base, "result": "stale", "reason": "symbol-bytes-changed",
                "current_hash": found.slice8, "current_span": current_span}
    if expected_span and expected_span != current_span:
        return {**base, "result": "moved", "current_span": current_span}
    return {**base, "result": "ok", "current_span": current_span}
```

CLI wiring in `parse_args`:
```python
    verify = sub.add_parser("verify")
    verify.add_argument("file")
    verify.add_argument("--symbol", required=True)
    verify.add_argument("--hash", required=True)
    verify.add_argument("--span")
```
and in `main`, inside the file-command branch:
```python
            elif args.command == "verify":
                result = verify_symbol(root, paths, relative, source, args.symbol, args.hash, args.span)
```

- [ ] **Step 4: Run all tests**

Run: `python3 -m unittest discover -s tests -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/kern/scripts/kern_cache.py tests/test_verify.py
git commit -m "feat: verify verb traps stale symbol reads"
```

---

### Task 8: Token benchmark harness

**Files:**
- Create: `benchmarks/token_bench.py`
- Test: `tests/test_token_bench.py`

**Interfaces:**
- Consumes: `kern_compile.parse_python`, `emit_il`.
- Produces: `benchmarks/token_bench.py` CLI: `python3 benchmarks/token_bench.py FILE [FILE...] [--out results.json]`. Report schema: `{"schema": "kern-bench/0.2", "estimator": "chars/4", "files": [{"file", "source_tokens", "bucket", "tiers": {"L1": {"tokens", "ratio"}, ...}, "fidelity_missing": [...]}]}`. Importable functions `estimate_tokens(text)`, `bench_file(path)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_token_bench.py`:

```python
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills" / "kern" / "scripts"))

spec = importlib.util.spec_from_file_location("token_bench", REPO / "benchmarks" / "token_bench.py")
token_bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(token_bench)

BIG = '"""Doc."""\n\nimport json\n\n' + "\n\n".join(
    f'def fn_{i}(path):\n'
    f'    """Doc {i} with a somewhat longer explanatory sentence to add bulk."""\n'
    f'    # a comment line adding source-only weight\n'
    f'    data = path.read_bytes()\n'
    f'    if not data:\n'
    f'        raise ValueError(path)\n'
    f'    return json.loads(data)\n'
    for i in range(40)
)


class TestTokenBench(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.f = self.tmp / "big.py"
        self.f.write_text(BIG)

    def test_bench_file_shape(self):
        row = token_bench.bench_file(self.f)
        self.assertIn("source_tokens", row)
        self.assertEqual(set(row["tiers"]), {"L1", "L2", "L3"})
        for tier in row["tiers"].values():
            self.assertGreater(tier["ratio"], 1.0)

    def test_tier_ordering(self):
        row = token_bench.bench_file(self.f)
        self.assertGreater(row["tiers"]["L1"]["ratio"], row["tiers"]["L2"]["ratio"])
        self.assertGreater(row["tiers"]["L2"]["ratio"], row["tiers"]["L3"]["ratio"])

    def test_fidelity_no_missing_functions(self):
        row = token_bench.bench_file(self.f)
        self.assertEqual(row["fidelity_missing"], [])

    def test_parse_error_reported_not_raised(self):
        bad = self.tmp / "bad.py"
        bad.write_text("def broken(:\n")
        row = token_bench.bench_file(bad)
        self.assertIn("error", row)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_token_bench -v`
Expected: FAIL — `FileNotFoundError` for `benchmarks/token_bench.py`.

- [ ] **Step 3: Write implementation**

Create `benchmarks/token_bench.py`:

```python
#!/usr/bin/env python3
"""Token benchmark: source vs deterministic KERN-IL per tier, bucketed by size."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "kern" / "scripts"))
import kern_compile  # noqa: E402


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def bucket(tokens: int) -> str:
    if tokens < 2_000:
        return "small(<2k)"
    if tokens < 10_000:
        return "medium(2k-10k)"
    return "large(>10k)"


def fidelity_missing(module, il: str) -> list[str]:
    missing = []
    for symbol in module.symbols:
        if symbol.kind == "function" and symbol.name.split(".")[-1] not in il:
            missing.append(symbol.name)
    return missing


def bench_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    module = kern_compile.parse_python(text)
    if module.parse_error:
        return {"file": str(path), "error": module.parse_error}
    source_tokens = estimate_tokens(text)
    row = {"file": str(path), "source_tokens": source_tokens,
           "bucket": bucket(source_tokens), "tiers": {}, "fidelity_missing": []}
    for tier in ("L1", "L2", "L3"):
        il = kern_compile.emit_il(module, path.name, "0" * 64, "none", tier)
        il_tokens = estimate_tokens(il)
        row["tiers"][tier] = {"tokens": il_tokens, "ratio": round(source_tokens / il_tokens, 2)}
        if tier == "L2":
            row["fidelity_missing"] = fidelity_missing(module, il)
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, help="Write JSON report here as well as stdout")
    args = parser.parse_args()
    report = {"schema": "kern-bench/0.2", "estimator": "chars/4",
              "files": [bench_file(f) for f in args.files]}
    payload = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload + "\n")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests and a real benchmark**

Run: `python3 -m unittest tests.test_token_bench -v`
Expected: PASS.
Run: `python3 benchmarks/token_bench.py skills/kern/scripts/kern_cache.py --out benchmarks/results/python-det-v2.json`
Expected: JSON report; L2 ratio on `kern_cache.py` should be ≥ 3× (it is a medium file; the ≥6× acceptance target applies to the large bucket only).

- [ ] **Step 5: Commit**

```bash
git add benchmarks/token_bench.py benchmarks/results/python-det-v2.json tests/test_token_bench.py
git commit -m "feat: per-tier token benchmark with fidelity check"
```

---

### Task 9: Documentation and skill contract

**Files:**
- Modify: `skills/kern/SKILL.md`
- Modify: `docs/architecture.md`
- Modify: `README.md` (Development section + Language coverage sentence)
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: everything shipped in Tasks 1-8.
- Produces: user-facing contract for `verify`, tiers, and the deterministic pipeline.

- [ ] **Step 1: Update `skills/kern/SKILL.md`**

In the "JIT a requested file" section, after the `ensure` code block, add:

```markdown
`ensure` accepts `--tier L1|L2|L3` (default from config `default_tier`, `L2`).
L1 = signatures + calls + effects + raises; L2 = + control-flow skeleton;
L3 = + expressions and dataflow. Files below the `min_ir_tokens` floor get a
`mode=source-cheaper` stub — read exact source instead.
```

In the "Fault exact source before edits" section, add before the existing code block:

```markdown
Before editing any symbol read from IL or an image, verify its source-map handle:

    python3 <skill-root>/scripts/kern_cache.py --repo <repo> verify path/to/file \
      --symbol <qualified-name> --hash <slice-hash> [--span L<a>-L<b>]

`ok` — proceed. `moved` — same bytes at a new span; use the returned span.
`stale` — the symbol changed; the IL page is invalid, fault exact source.
Lines tagged `!FAULT(reason)` (regex, math, concurrency, elided-literal) may not
support a claim or an edit without an exact-source fault, regardless of verify.
```

- [ ] **Step 2: Update `docs/architecture.md`**

Add invariant `7. Every IL symbol carries a slice hash; a passing verify or fresh fault is required before that symbol is edited.` and change the Lifecycle line to `scan → hash → invalidate → compile(tiered) → render → page in → verify/fault → write → invalidate`.

- [ ] **Step 3: Update `README.md`**

In "Language and runtime coverage", replace the first sentence with:

```markdown
Python and (when tree-sitter is installed) JavaScript/TypeScript receive a deterministic
AST-based compiler with tiered detail, computed side effects, and exception propagation.
Other recognized source formats receive a deterministic generic baseline.
```

In "Development", replace the commands block with:

```bash
npm ci
npm run build
python3 -m py_compile skills/kern/scripts/kern_cache.py skills/kern/scripts/render_ir.py skills/kern/scripts/kern_compile.py
python3 -m unittest discover -s tests
python3 skills/kern/scripts/kern_cache.py --repo . scan
```

- [ ] **Step 4: Update `CHANGELOG.md`**

Add an entry at the top following the file's existing format: codec `kern-il/0.2`, deterministic tiered compiler, effects/raises, verify verb, size floor, token benchmark. Bump the minor version consistent with the file's convention.

- [ ] **Step 5: Full check**

Run: `python3 -m unittest discover -s tests -v && python3 -m py_compile skills/kern/scripts/kern_cache.py skills/kern/scripts/render_ir.py skills/kern/scripts/kern_compile.py`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add skills/kern/SKILL.md docs/architecture.md README.md CHANGELOG.md
git commit -m "docs: kern-il/0.2 tiers, verify contract, dev commands"
```

---

### Task 10: Enrichment is append-only (commit_file contract)

**Files:**
- Modify: `skills/kern/scripts/kern_cache.py:691-764` (`commit_file`)
- Modify: `skills/kern/references/compiler-worker.md`
- Test: `tests/test_enrichment.py`

**Interfaces:**
- Consumes: Task 6 `ensure_file`/`baseline_for` (deterministic IL on disk before enrichment).
- Produces: `commit_file` rejects any staging IL that does not consist of the current committed deterministic IL followed by an `ENRICHMENT model=<name>` section containing only `INTENT <symbol>: <text>` lines. Spec rule: model output never replaces deterministic facts.

- [ ] **Step 1: Write the failing test**

Create `tests/test_enrichment.py`:

```python
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "kern" / "scripts"))
import kern_cache  # noqa: E402

BIG_PY = '"""Doc."""\n\nimport json\n\n' + "\n\n".join(
    f'def fn_{i}(path):\n'
    f'    data = path.read_bytes()\n'
    f'    return json.loads(data)\n'
    for i in range(40)
)


class TestEnrichmentAppendOnly(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "mod.py").write_text(BIG_PY)
        self.paths, self.config = kern_cache.initialize(self.root)
        rel, src = kern_cache.normalize_rel(self.root, "mod.py")
        self.rel, self.src = rel, src
        ensured = kern_cache.ensure_file(self.root, self.paths, rel, src, self.config)
        self.digest = ensured["source_sha256"]
        self.baseline = Path(ensured["ir"]).read_text()

    def commit(self, staging_text):
        staging = self.root / "staging.kern-il.txt"
        staging.write_text(staging_text)
        return kern_cache.commit_file(self.root, self.paths, self.rel, self.src,
                                      staging, self.digest)

    def test_valid_append_accepted(self):
        staged = self.baseline + "\nENRICHMENT model=test-model\nINTENT fn_0: reads and parses a JSON file\n"
        result = self.commit(staged)
        self.assertEqual(result["status"], "ready")

    def test_replacement_rejected(self):
        rogue = self.baseline.replace("F fn_0(", "F totally_different(")
        with self.assertRaises(ValueError):
            self.commit(rogue + "\nENRICHMENT model=test-model\nINTENT fn_0: x\n")

    def test_missing_enrichment_header_rejected(self):
        with self.assertRaises(ValueError):
            self.commit(self.baseline + "\nINTENT fn_0: no header line\n")

    def test_non_intent_lines_rejected(self):
        staged = self.baseline + "\nENRICHMENT model=test-model\nF injected_fact() -> Any @L1-1 ^deadbeef ~L2\n"
        with self.assertRaises(ValueError):
            self.commit(staged)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_enrichment -v`
Expected: FAIL — `test_replacement_rejected` and the others fail because `commit_file` currently accepts any well-headed IL.

- [ ] **Step 3: Modify `commit_file`**

In `commit_file`, after the existing header validation (`generator` check) and before `atomic_write`, add:

```python
    baseline_path = artifact_paths(paths, relative)["ir"]
    if not baseline_path.is_file():
        raise ValueError("No deterministic baseline IL exists; run ensure before commit")
    baseline_text = baseline_path.read_text(encoding="utf-8")
    if not text.startswith(baseline_text.rstrip("\n")):
        raise ValueError("Enrichment must preserve the deterministic IL verbatim as a prefix")
    appended = text[len(baseline_text.rstrip("\n")):].strip("\n")
    if appended:
        appended_lines = appended.splitlines()
        if not appended_lines[0].startswith("ENRICHMENT model="):
            raise ValueError("Appended section must start with 'ENRICHMENT model=<name>'")
        for line in appended_lines[1:]:
            if line.strip() and not line.startswith("INTENT "):
                raise ValueError(f"Enrichment may only append INTENT lines, found: {line[:60]!r}")
```

Note: `artifact_paths` is already imported in scope; `text` is the decoded staging payload already available in `commit_file`.

- [ ] **Step 4: Rewrite `skills/kern/references/compiler-worker.md`**

Replace the enrichment contract with the new one (keep the file's existing tone/format):

```markdown
# Compiler worker contract (enrichment)

The deterministic IL is authoritative and already committed. Your job is ONLY to
append intent summaries. Output the baseline IL verbatim, then:

    ENRICHMENT model=<your-model-name>
    INTENT <qualified-symbol>: <one-line summary of purpose>

Rules:
- Never modify, reorder, or omit any deterministic line. The commit will be
  rejected if the baseline is not a byte-exact prefix of your output.
- Only `INTENT` lines may follow the `ENRICHMENT` header.
- One INTENT line per symbol, at most; skip symbols whose purpose is obvious.
- Never include secrets, credentials, or long literals.
```

- [ ] **Step 5: Run all tests**

Run: `python3 -m unittest discover -s tests -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add skills/kern/scripts/kern_cache.py skills/kern/references/compiler-worker.md tests/test_enrichment.py
git commit -m "feat: enrichment is append-only INTENT lines over deterministic IL"
```
