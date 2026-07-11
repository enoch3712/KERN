#!/usr/bin/env python3
"""Deterministic KERN-IL/0.2 compiler: language frontends, effect engine, tiered emitter."""

from __future__ import annotations

import ast
import copy
import functools
import hashlib
import importlib
import importlib.metadata
import json
import re
from dataclasses import dataclass, field

CODEC_VERSION = "kern-il/0.2"
GENERATOR = "kern-det/0.2"
HANDLE_HEX_LENGTH = 16

SECRET_NAME = re.compile(
    r"(?i)(api[_-]?key|access[_-]?key|auth|bearer|credential|passwd|password|private[_-]?key|secret|token)"
)
SECRET_VALUE = re.compile(
    r"(?i)(?:sk|rk|pk|s2)[_-][A-Za-z0-9_-]{12,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"(?:aws|ghp|github_pat)_[A-Za-z0-9_-]{12,}"
)
SPACE = re.compile(r"\s+")
_PHYSICAL_LINE_ESCAPES = {
    "\v": "\\x0b",
    "\f": "\\x0c",
    "\x1c": "\\x1c",
    "\x1d": "\\x1d",
    "\x1e": "\\x1e",
    "\x85": "\\x85",
    "\u2028": "\\u2028",
    "\u2029": "\\u2029",
}


def one_line_text(value: str) -> str:
    """Collapse layout whitespace without changing whitespace inside literals.

    IL records must remain one physical line, but a global ``\\s+`` replacement
    changes the meaning of strings such as ``"two  spaces"``.  This small
    scanner keeps quoted/template literal contents byte-for-byte (apart from
    escaping physical control characters), preserves ordinary spaces globally,
    and replaces only physical layout controls outside literals.  It intentionally
    does not try to parse the host language; the real
    parser has already established the node boundaries passed to this helper.
    """
    out: list[str] = []
    quote = ""
    escaped = False
    for ch in value:
        if quote:
            if escaped:
                if ch == "\n":
                    out.append("\\n")
                elif ch == "\r":
                    out.append("\\r")
                elif ch == "\t":
                    out.append("\\t")
                elif ch in _PHYSICAL_LINE_ESCAPES:
                    out.append(_PHYSICAL_LINE_ESCAPES[ch])
                else:
                    out.append(ch)
                escaped = False
                continue
            if ch == "\\":
                out.append(ch)
                escaped = True
                continue
            if ch == quote:
                out.append(ch)
                quote = ""
                continue
            if ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                out.append("\\r")
            elif ch == "\t":
                out.append("\\t")
            elif ch in _PHYSICAL_LINE_ESCAPES:
                out.append(_PHYSICAL_LINE_ESCAPES[ch])
            else:
                out.append(ch)
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            out.append(ch)
        elif ch == " ":
            # Preserve ordinary spaces globally.  In particular, a slash
            # delimited JavaScript regex is not a quoted string, and changing
            # ``/a  b/`` to ``/a b/`` changes its language.
            out.append(ch)
        elif ch == "\t":
            out.append("\\x09")
        elif ch in _PHYSICAL_LINE_ESCAPES:
            out.append(_PHYSICAL_LINE_ESCAPES[ch])
        elif ch in ("\n", "\r"):
            # Layout newlines outside literals are not semantic to the parsed
            # node; keep output on one physical line with one separator.
            if out and out[-1] != " ":
                out.append(" ")
        else:
            out.append(ch)
    return "".join(out).strip()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def slice_sha8(source_text: str, start: int, end: int) -> str:
    # Split on "\n" only: ast/tree-sitter line numbers count "\n" exclusively,
    # while str.splitlines() also breaks on \v, \f, \x1c-\x1e, \x85, U+2028,
    # U+2029, etc. A source string containing one of those characters would
    # silently shift the hashed window relative to the true line numbers.
    lines = source_text.split("\n")
    segment = "\n".join(lines[start - 1:end])
    # Preserve whether the selected final line actually has a terminator.  A
    # symbol at EOF without a trailing newline must not share an "exact" slice
    # digest with the same bytes followed by ``\n``.
    if end < len(lines):
        segment += "\n"
    return sha256_hex(segment.encode("utf-8", "surrogatepass"))[:HANDLE_HEX_LENGTH]


def sanitize_string(value: str, secret_hint: bool = False) -> str:
    digest = sha256_hex(value.encode("utf-8", "surrogatepass"))[:12]
    if secret_hint or SECRET_VALUE.search(value):
        return f"<REDACTED len={len(value)} sha256={digest}>"
    if len(value) > 160:
        return f"<STR len={len(value)} sha256={digest}>"
    return value


def _redacted_ast_value(node: ast.AST) -> ast.Constant:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        value = sanitize_string(node.value, secret_hint=True)
    else:
        try:
            raw = ast.unparse(node)
        except Exception:
            raw = node.__class__.__name__
        digest = sha256_hex(raw.encode("utf-8", "surrogatepass"))[:12]
        value = f"<REDACTED_EXPR len={len(raw)} sha256={digest}>"
    return ast.copy_location(ast.Constant(value), node)


class _LiteralSanitizer(ast.NodeTransformer):
    def visit_Constant(self, node: ast.Constant):
        if isinstance(node.value, str):
            return ast.copy_location(ast.Constant(sanitize_string(node.value)), node)
        return node

    def visit_Dict(self, node: ast.Dict):
        node = self.generic_visit(node)
        for index, key in enumerate(node.keys):
            if isinstance(key, ast.Constant) and isinstance(key.value, str) and SECRET_NAME.search(key.value):
                node.values[index] = _redacted_ast_value(node.values[index])
        return node

    def visit_Call(self, node: ast.Call):
        node = self.generic_visit(node)
        try:
            callee = ast.unparse(node.func)
        except Exception:
            callee = ""
        secret_call = bool(SECRET_NAME.search(callee))
        if secret_call:
            node.args = [_redacted_ast_value(arg) for arg in node.args]
        for keyword in node.keywords:
            if secret_call or (keyword.arg is not None and SECRET_NAME.search(keyword.arg)):
                keyword.value = _redacted_ast_value(keyword.value)
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
    rendered = one_line_text(rendered)
    if secret_hint and not rendered.startswith("'<REDACTED"):
        digest = sha256_hex(rendered.encode())[:12]
        rendered = f"<REDACTED_EXPR len={len(rendered)} sha256={digest}>"
    if len(rendered) > max_length:
        digest = sha256_hex(rendered.encode())[:12]
        rendered = rendered[: max_length - 24] + f"…<sha256={digest}>"
    return rendered


def _redact_secret_defaults(args_node: ast.arguments) -> ast.arguments:
    """Deep-copy a function's parameter list and replace the default value of
    any secret-named parameter (e.g. password="hunter2") with a redacted
    placeholder, so literal secrets never appear in an emitted signature."""
    clone = copy.deepcopy(args_node)

    positional = clone.posonlyargs + clone.args
    offset = len(positional) - len(clone.defaults)
    for i, default in enumerate(clone.defaults):
        if default is None:
            continue
        arg = positional[offset + i]
        if arg is not None and SECRET_NAME.search(arg.arg or ""):
            clone.defaults[i] = _redacted_ast_value(default)
    for i, default in enumerate(clone.kw_defaults):
        if default is None:
            continue
        arg = clone.kwonlyargs[i]
        if arg is not None and SECRET_NAME.search(arg.arg or ""):
            clone.kw_defaults[i] = _redacted_ast_value(default)
    return clone


def _target(node: ast.AST) -> str:
    try:
        return one_line_text(ast.unparse(node))
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
    kind: str  # function | class | const | import | type | enum | namespace | module
    name: str
    span: tuple = (0, 0)
    signature: str = ""
    returns: str = ""
    decorators: list = field(default_factory=list)
    slice8: str = ""
    semantic8: str = ""
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


def _semantic_symbol_record(symbol: Symbol) -> dict:
    """Return the position-independent facts that define a module context."""
    return {
        "kind": symbol.kind,
        "name": symbol.name,
        "signature": symbol.signature,
        "returns": symbol.returns,
        "decorators": list(symbol.decorators),
        "slice8": symbol.slice8,
        "calls": list(symbol.calls),
        "raises": list(symbol.raises),
        "flow": [
            {
                "op": op.op,
                "detail": op.detail,
                "binds": op.binds,
                "depth": op.depth,
                "risk": op.risk,
            }
            for op in symbol.flow
        ],
        "async": symbol.is_async,
        "bases": symbol.bases,
        "detail": symbol.detail,
        "risk": symbol.risk,
    }


def apply_semantic_handles(module: ModuleIR) -> ModuleIR:
    """Fill each symbol's contextual source handle and return ``module``.

    A handle combines the symbol's own exact line-slice hash with a stable
    module context.  Source line numbers are deliberately absent: inserting a
    comment before a function keeps its handle and lets verification report
    ``moved``.  Any import, constant, decorator, class, or function slice change
    changes the context and therefore invalidates callers compiled from stale
    same-file facts.
    """
    records = [_semantic_symbol_record(symbol) for symbol in module.symbols]
    context = json.dumps(
        {"lang": module.lang, "frontend": module.frontend, "symbols": records},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8", "surrogatepass")
    context8 = sha256_hex(context)[:32]
    for symbol in module.symbols:
        payload = f"{symbol.kind}\0{symbol.name}\0{symbol.slice8}\0{context8}".encode(
            "utf-8", "surrogatepass"
        )
        symbol.semantic8 = sha256_hex(payload)[:HANDLE_HEX_LENGTH]
    return module


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


_RISK_CALL = [
    ("regex", re.compile(r"^re\.(compile|match|search|sub|split|fullmatch|findall|finditer)$")),
    ("crypto", re.compile(r"^(hashlib|hmac|secrets)\.")),
    ("concurrency", re.compile(r"^(threading|asyncio|multiprocessing|concurrent)\.")),
]
_TRY_TYPES = (ast.Try, getattr(ast, "TryStar", ast.Try))

PYTHON_EFFECT_RULES = [
    ("fs:read", re.compile(
        r"^(open|fitz\.open|os\.(walk|listdir|stat|scandir)|os\.path\.(exists|isfile|isdir|getsize|getmtime|basename)"
        r")$|\.(read|readline|readlines|read_text|read_bytes|exists|is_file|is_dir|stat|iterdir|glob)$")),
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


JS_EFFECT_RULES = [
    ("fs:read", re.compile(
        r"^(readFile|readFileSync|readdir|readdirSync|stat|statSync|lstat|lstatSync|access|accessSync)$"
        r"|(?:^|\.)(?:readFile|readFileSync|readdir|readdirSync|stat|statSync|lstat|lstatSync|access|accessSync)$"
        r"|^Deno\.(?:readTextFile|readFile|readDir|stat|lstat)$|^Bun\.file$")),
    ("fs:write", re.compile(
        r"^(writeFile|writeFileSync|appendFile|appendFileSync|mkdir|mkdirSync|rm|rmSync|unlink|unlinkSync|rename|renameSync)$"
        r"|(?:^|\.)(?:writeFile|writeFileSync|appendFile|appendFileSync|mkdir|mkdirSync|rm|rmSync|unlink|unlinkSync|rename|renameSync)$"
        r"|^Deno\.(?:writeTextFile|writeFile|mkdir|remove|rename)$|^Bun\.write$")),
    ("net", re.compile(
        r"^(fetch|WebSocket|EventSource)$|^(?:axios|got|ky)(?:\.|$)"
        r"|^(?:https?|http2|net|tls)\.(?:get|request|connect|createConnection)$")),
    ("proc", re.compile(
        r"^(?:child_process\.)?(?:exec|execFile|execSync|execFileSync|spawn|spawnSync|fork)$"
        r"|^Deno\.Command$|^Bun\.spawn")),
    ("env", re.compile(r"^(?:process\.env|Deno\.env\.(?:get|set|delete))$")),
    ("time", re.compile(r"^(?:Date\.now|setTimeout|setInterval|timers\.)")),
    ("random", re.compile(r"^(?:Math\.random|crypto\.getRandomValues|crypto\.randomUUID)$")),
    ("console", re.compile(r"^console\.")),
    ("thread", re.compile(r"^(?:Worker|SharedWorker|worker_threads\.|cluster\.)")),
]

# Compatibility alias for callers that imported the old table directly.
EFFECT_RULES = PYTHON_EFFECT_RULES


def _call_head(call_text: str) -> str:
    text = one_line_text(call_text).strip()
    # Remove only a final argument list.  Earlier call pairs are preserved so
    # ``Path("x").read_text()`` still classifies by its final member name.
    if text.endswith(")"):
        depth = 0
        quote = ""
        escaped = False
        for index in range(len(text) - 1, -1, -1):
            ch = text[index]
            if quote:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == quote:
                    quote = ""
                continue
            if ch in ("'", '"', "`"):
                quote = ch
            elif ch == ")":
                depth += 1
            elif ch == "(":
                depth -= 1
                if depth == 0:
                    head = text[:index].strip()
                    return head.removeprefix("new ").strip()
    return text.removeprefix("new ").strip()


def _python_open_effects(call_text: str) -> list[str] | None:
    text = one_line_text(call_text)
    head = _call_head(text)
    if head != "open" and not head.endswith(".open"):
        return None
    mode = "r"
    try:
        expression = ast.parse(text, mode="eval").body
        if isinstance(expression, ast.Call):
            mode_node = next((kw.value for kw in expression.keywords if kw.arg == "mode"), None)
            if mode_node is None:
                index = 1 if head in {"open", "io.open", "builtins.open"} else 0
                if len(expression.args) > index:
                    mode_node = expression.args[index]
            if isinstance(mode_node, ast.Constant) and isinstance(mode_node.value, str):
                mode = mode_node.value
    except (SyntaxError, ValueError):
        pass
    effects: list[str] = []
    if "r" in mode or "+" in mode:
        effects.append("fs:read")
    if any(flag in mode for flag in ("w", "a", "x", "+")):
        effects.append("fs:write")
    return effects or ["fs:read"]


def classify_call(call_name: str, lang: str = "python") -> list:
    text = one_line_text(call_name)
    head = _call_head(text)
    if lang == "python":
        open_effects = _python_open_effects(text)
        if open_effects is not None:
            return open_effects
        rules = PYTHON_EFFECT_RULES
    elif lang in {"javascript", "typescript", "tsx"}:
        rules = JS_EFFECT_RULES
    else:
        rules = []
    return [effect for effect, rx in rules if rx.search(head)]


def _resolved_local(module: ModuleIR, caller: Symbol, call_name: str,
                    by_name: dict[str, list[Symbol]]) -> Symbol | None:
    """Resolve only names whose target is mechanically unambiguous."""
    name = _call_head(call_name)
    candidates: list[Symbol] = []
    if re.fullmatch(r"[A-Za-z_$][\w$]*", name):
        candidates = by_name.get(name, [])
    elif name.startswith(("self.", "cls.", "this.")) and "." in caller.name:
        owner = caller.name.rsplit(".", 1)[0]
        candidates = by_name.get(owner + "." + name.rsplit(".", 1)[-1], [])
    elif re.fullmatch(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+", name):
        candidates = by_name.get(name, [])
    if len(candidates) == 1 and candidates[0] is not caller:
        return candidates[0]
    return None


def propagate(module: ModuleIR) -> None:
    funcs = [s for s in module.symbols if s.kind == "function"]
    by_name: dict[str, list[Symbol]] = {}
    for s in funcs:
        by_name.setdefault(s.name, []).append(s)
    for s in funcs:
        if not s.effects:
            s.effects = {e: [] for c in s.calls for e in classify_call(c, module.lang)}
        if not s.raises_all:
            s.raises_all = {r: [] for r in s.raises}
        unknown = 0
        for c in s.calls:
            if not classify_call(c, module.lang) and _resolved_local(module, s, c, by_name) is None:
                unknown += 1
        s.unknown_calls = unknown
    changed, rounds = True, 0
    while changed and rounds < 32:
        changed, rounds = False, rounds + 1
        for s in funcs:
            for c in s.calls:
                callee = _resolved_local(module, s, c, by_name)
                if callee is None:
                    continue
                via = callee.name
                for eff in callee.effects:
                    if eff not in s.effects:
                        s.effects[eff] = [via]
                        changed = True
                    elif s.effects[eff] and via not in s.effects[eff]:
                        s.effects[eff] = sorted(s.effects[eff] + [via])
                for exc in callee.raises_all:
                    if exc not in s.raises_all:
                        s.raises_all[exc] = [via]
                        changed = True
                    elif s.raises_all[exc] and via not in s.raises_all[exc]:
                        s.raises_all[exc] = sorted(s.raises_all[exc] + [via])


def expr_risk(node: ast.AST | None) -> str:
    if node is None:
        return ""

    # First pass: check for call-based risks (regex, crypto, concurrency)
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

    # Second pass: check for math risks (only if no call-based risks found)
    for ch in ast.walk(node):
        if isinstance(ch, ast.BinOp) and isinstance(ch.op, (ast.Pow, ast.LShift, ast.RShift)):
            return "math"

    return ""


def flow_ops(statements: list, depth: int = 0, budget: int = 200) -> list:
    ops: list[FlowOp] = []

    def add(node, op, detail="", binds="", risk="", line=None):
        if len(ops) < budget:
            ops.append(FlowOp(op=op, detail=detail, binds=binds, depth=depth,
                              line=line if line is not None else getattr(node, "lineno", 0), risk=risk))

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
                add(s, "ELSE", line=s.orelse[0].lineno)
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
                add(s, "FINALLY", line=s.finalbody[0].lineno)
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


def _walk_python_scope(nodes: list[ast.AST]):
    """Yield nodes in one executable scope, excluding nested definitions."""
    stack = list(reversed(nodes))
    while stack:
        current = stack.pop()
        yield current
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(current))))


def _function_symbol(node, qualified: str, text: str) -> Symbol:
    calls: list[str] = []
    raises: list[str] = []
    direct_effects: dict[str, list] = {}
    for ch in _walk_python_scope(node.body):
        if isinstance(ch, ast.Call):
            try:
                name = expr_text(ch.func, 80)
            except Exception:
                name = "<call>"
            if name not in calls:
                calls.append(name)
            for effect in classify_call(expr_text(ch, 240), "python"):
                direct_effects.setdefault(effect, [])
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
        signature=expr_text(_redact_secret_defaults(node.args), 200),
        returns=expr_text(node.returns, 60) if node.returns else "",
        decorators=[expr_text(d, 60) for d in node.decorator_list],
        slice8=slice_sha8(text, start, end),
        calls=calls,
        raises=raises,
        flow=flow_ops(node.body),
        is_async=isinstance(node, ast.AsyncFunctionDef),
        effects=direct_effects,
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
                                  span=(node.lineno, node.end_lineno or node.lineno),
                                  slice8=slice_sha8(text, node.lineno, node.end_lineno or node.lineno)))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = ",".join(_target(t) for t in targets)
            hint = bool(SECRET_NAME.search(names))
            if isinstance(node, ast.AnnAssign) and node.value is None:
                # Annotation-only declaration (e.g. `count: int`) has no value;
                # emit the annotation, not a fabricated "=None".
                detail = ": " + expr_text(node.annotation, 60)
                risk_value = None
            else:
                detail = "=" + expr_text(node.value, 100, hint)
                risk_value = node.value
            symbol = Symbol(kind="const", name=names, detail=detail,
                           span=(node.lineno, node.end_lineno or node.lineno),
                           slice8=slice_sha8(text, node.lineno, node.end_lineno or node.lineno))
            symbol.risk = expr_risk(risk_value)
            symbols.append(symbol)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(_function_symbol(node, node.name, text))
        elif isinstance(node, ast.ClassDef):
            start = min([d.lineno for d in node.decorator_list] + [node.lineno])
            end = node.end_lineno or node.lineno
            symbols.append(Symbol(kind="class", name=node.name,
                                  bases=",".join(expr_text(b, 60) for b in node.bases),
                                  span=(start, end), slice8=slice_sha8(text, start, end),
                                  decorators=[expr_text(d, 60) for d in node.decorator_list]))
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.append(_function_symbol(member, f"{node.name}.{member.name}", text))
    return ModuleIR("python", "pyast", symbols, _omit_counts(text, tree))


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
    handle = s.semantic8 or s.slice8
    lines = [f"{head} {s.name}({s.signature}) -> {s.returns or 'Any'} "
             f"@L{s.span[0]}-{s.span[1]} ^{handle} ~{tier}"]
    if s.decorators:
        lines.append("  DECORATORS " + ", ".join(s.decorators))
    if level <= 2:
        if s.calls:
            # Calls are part of the L1/L2 fidelity contract. If the complete
            # deduplicated set is no longer economical, the file-level size
            # policy should select source instead of silently discarding facts.
            lines.append("  CALLS " + ", ".join(s.calls))
    else:
        covered = "\n".join(op.detail for op in s.flow)
        leftover = [
            c for c in s.calls
            if not re.search(rf"(?<![\w.]){re.escape(c)}\s*\(", covered)
        ]
        if leftover:
            lines.append("  CALLS " + ", ".join(leftover))
    effects = _render_provenanced(s.effects, s.unknown_calls)
    if effects:
        lines.append("  EFFECTS " + effects)
    raises = _render_provenanced(s.raises_all, 0)
    if raises:
        lines.append("  RAISES " + raises)
    if level >= 2:
        for op in s.flow:
            # L2 is structure-only: bare CALL carries no name (CALLS has them),
            # so only risk-tagged calls earn a line at this tier.
            if level == 2 and op.op == "CALL" and not op.risk:
                continue
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


def _distribution_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "missing"


def _tsjs_language(kind: str):
    from tree_sitter import Language
    if kind == "javascript":
        grammar = importlib.import_module("tree_sitter_javascript").language()
    else:
        grammar_module = importlib.import_module("tree_sitter_typescript")
        grammar = (
            grammar_module.language_tsx()
            if kind == "tsx"
            else grammar_module.language_typescript()
        )
    if isinstance(grammar, Language):
        return grammar
    return Language(grammar)


def _tsjs_parser(language):
    from tree_sitter import Parser
    try:
        return Parser(language)
    except TypeError:
        # tree-sitter <0.22 constructed an empty parser and assigned the
        # language afterwards.  Supporting both APIs avoids coupling a skill
        # installation to whichever wheel the host already has.
        parser = Parser()
        if hasattr(parser, "set_language"):
            parser.set_language(language)
        else:
            parser.language = language
        return parser


@functools.lru_cache(maxsize=1)
def _cached_tsjs_capabilities() -> tuple[tuple[str, bool], ...]:
    capabilities: dict[str, bool] = {}
    for kind in ("javascript", "typescript", "tsx"):
        try:
            _tsjs_parser(_tsjs_language(kind))
            capabilities[kind] = True
        except (ImportError, TypeError, ValueError, AttributeError):
            capabilities[kind] = False
    return tuple(capabilities.items())


def tsjs_capabilities() -> dict[str, bool]:
    return dict(_cached_tsjs_capabilities())


def tsjs_capability_fingerprint() -> str:
    caps = tsjs_capabilities()
    enabled = ",".join(kind for kind in ("javascript", "typescript", "tsx") if caps[kind]) or "none"
    return ";".join([
        f"tree-sitter={_distribution_version('tree-sitter')}",
        f"javascript={_distribution_version('tree-sitter-javascript')}",
        f"typescript={_distribution_version('tree-sitter-typescript')}",
        f"caps={enabled}",
    ])


def tsjs_available(typescript: bool = False, tsx: bool = False) -> bool:
    kind = "tsx" if tsx else ("typescript" if typescript else "javascript")
    return tsjs_capabilities()[kind]


_TS_FLOW = {
    "if_statement": "IF", "for_statement": "LOOP", "for_in_statement": "LOOP",
    "while_statement": "WHILE", "do_statement": "WHILE", "try_statement": "TRY",
    "return_statement": "RET", "throw_statement": "RAISE", "switch_statement": "MATCH",
}
_TS_FUNC_NODES = {
    "function_declaration", "generator_function_declaration", "function_expression",
    "generator_function", "arrow_function", "method_definition", "method_signature",
    "abstract_method_signature", "function_signature",
}
_TS_NESTED_SCOPES = _TS_FUNC_NODES | {"class_declaration", "abstract_class_declaration", "class"}


def _redaction_marker(data: bytes) -> str:
    return f"<REDACTED len={len(data)} sha256={sha256_hex(data)[:12]}>"


def parse_tsjs(text: str, typescript: bool = False, tsx: bool = False) -> ModuleIR:
    kind = "tsx" if tsx else ("typescript" if typescript else "javascript")
    language = _tsjs_language(kind)
    lang_name = "typescript" if kind in {"typescript", "tsx"} else "javascript"
    parser = _tsjs_parser(language)
    tree = parser.parse(text.encode("utf-8"))
    raw = text.encode("utf-8")

    def raw_text(node) -> str:
        return raw[node.start_byte:node.end_byte].decode("utf-8", "replace")

    def field_node(node, name):
        return node.child_by_field_name(name)

    def secret_ranges(root, redact_literals=False) -> list[tuple[int, int]]:
        ranges: list[tuple[int, int]] = []

        def add(node):
            if node is not None:
                ranges.append((node.start_byte, node.end_byte))

        def named_secret(node) -> bool:
            return node is not None and bool(SECRET_NAME.search(raw_text(node)))

        def add_type_literals(node):
            if node is None:
                return
            if node.type == "literal_type":
                add(node)
                return
            for child in node.named_children:
                add_type_literals(child)

        def walk(node):
            node_type = node.type
            if node_type in {"required_parameter", "optional_parameter", "variable_declarator",
                             "public_field_definition", "field_definition"}:
                name = field_node(node, "pattern") or field_node(node, "name")
                value = field_node(node, "value")
                if named_secret(name) and value is not None:
                    add(value)
                elif named_secret(name):
                    add_type_literals(field_node(node, "type"))
            elif node_type in {"assignment_expression", "augmented_assignment_expression", "assignment_pattern"}:
                left = field_node(node, "left")
                right = field_node(node, "right")
                if named_secret(left) and right is not None:
                    add(right)
            elif node_type == "pair":
                key = field_node(node, "key")
                value = field_node(node, "value")
                if named_secret(key) and value is not None:
                    add(value)
            elif node_type == "enum_assignment":
                name = field_node(node, "name")
                value = field_node(node, "value")
                if named_secret(name) and value is not None:
                    add(value)
            elif node_type == "property_signature":
                name = field_node(node, "name")
                type_node = field_node(node, "type")
                if named_secret(name) and type_node is not None:
                    add_type_literals(type_node)
            elif node_type in {"call_expression", "new_expression"}:
                callee = field_node(node, "function") or field_node(node, "constructor")
                if named_secret(callee):
                    arguments = field_node(node, "arguments")
                    if arguments is not None:
                        for argument in arguments.named_children:
                            add(argument)
            for child in node.named_children:
                walk(child)

        walk(root)
        if redact_literals:
            add_type_literals(root)
        selected: list[tuple[int, int]] = []
        for start, end in sorted(ranges, key=lambda item: (item[0], -(item[1] - item[0]))):
            if selected and start >= selected[-1][0] and end <= selected[-1][1]:
                continue
            if selected and start < selected[-1][1]:
                continue
            selected.append((start, end))
        return selected

    def ntext(node, cap=120, secret_hint=False, redact_literals=False):
        if node is None:
            return ""
        original = raw[node.start_byte:node.end_byte]
        if secret_hint:
            return _redaction_marker(original)
        parts: list[bytes] = []
        cursor = node.start_byte
        for start, end in secret_ranges(node, redact_literals=redact_literals):
            if start < node.start_byte or end > node.end_byte:
                continue
            parts.append(raw[cursor:start])
            parts.append(_redaction_marker(raw[start:end]).encode("utf-8"))
            cursor = end
        parts.append(raw[cursor:node.end_byte])
        piece = one_line_text(b"".join(parts).decode("utf-8", "replace"))
        if SECRET_VALUE.search(piece):
            return _redaction_marker(original)
        if len(piece) > cap:
            digest = sha256_hex(piece.encode("utf-8", "surrogatepass"))[:12]
            keep = max(0, cap - 24)
            return piece[:keep] + f"…<sha256={digest}>"
        return piece

    def span(n):
        return (n.start_point[0] + 1, n.end_point[0] + 1)

    def span_with_decorators(node, decorators):
        start, end = span(node)
        if decorators:
            start = min(start, *(span(decorator)[0] for decorator in decorators))
        return start, end

    def walk_nodes(node):
        yield node
        for child in node.named_children:
            yield from walk_nodes(child)

    def ts_risk(node) -> str:
        if node is None:
            return ""
        for child in walk_nodes(node):
            if child.type == "regex":
                return "regex"
            if child.type in {"call_expression", "new_expression"}:
                callee = field_node(child, "function") or field_node(child, "constructor")
                name = ntext(callee, 100)
                if re.search(r"(?:^|\.)(?:createHash|createHmac|subtle|randomBytes)$", name):
                    return "crypto"
                if re.search(r"(?:^|\.)(?:Worker|Atomics|SharedArrayBuffer)$", name):
                    return "concurrency"
                if re.search(r"(?:^|\.)(?:RegExp|match|replace|search|split)$", name):
                    return "regex"
            if child.type == "binary_expression":
                if any(token.type in {"**", "<<", ">>", ">>>"} for token in child.children):
                    return "math"
        return ""

    def collect_calls(node, acc, effects, root=True):
        if not root and node.type in _TS_NESTED_SCOPES:
            return
        if node.type in {"call_expression", "new_expression"}:
            fn = field_node(node, "function") or field_node(node, "constructor")
            if fn is not None:
                name = ntext(fn, 80)
                if name not in acc:
                    acc.append(name)
                for effect in classify_call(ntext(node, 240), lang_name):
                    effects.setdefault(effect, [])
        for child in node.named_children:
            collect_calls(child, acc, effects, root=False)

    def collect_raises(node, acc, root=True):
        if not root and node.type in _TS_NESTED_SCOPES:
            return
        if node.type == "throw_statement":
            body = ntext(node, 80)
            name = body.removeprefix("throw").strip().removeprefix("new").strip()
            name = name.split("(")[0].rstrip(";").strip()
            if name and name not in acc:
                acc.append(name)
        for child in node.named_children:
            collect_raises(child, acc, root=False)

    def flow(node, depth=0, budget=200):
        ops = []

        def add(current, op, detail="", binds="", risk=""):
            if len(ops) < budget:
                ops.append(FlowOp(op=op, detail=detail, binds=binds, depth=depth,
                                  line=current.start_point[0] + 1, risk=risk))

        for c in node.named_children:
            if len(ops) >= budget:
                break
            t = c.type
            if t in _TS_NESTED_SCOPES:
                name = field_node(c, "name")
                add(c, "NESTED", ntext(name, 60) if name is not None else "<anonymous>")
                continue
            if t in _TS_FLOW:
                cond = field_node(c, "condition")
                detail = ntext(cond, 100).strip("()") if cond is not None else ""
                if t == "return_statement":
                    detail = ntext(c, 100).removeprefix("return").strip().rstrip(";")
                if t == "throw_statement":
                    detail = ntext(c, 80).removeprefix("throw").strip().rstrip(";")
                risk_node = c if t in {"return_statement", "throw_statement"} else cond
                add(c, _TS_FLOW[t], detail, risk=ts_risk(risk_node))
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
                    param = field_node(handler, "parameter")
                    if param is None:
                        param = field_node(handler, "parameters")
                    add(handler, "CATCH", ntext(param or handler, 40))
                    hbody = field_node(handler, "body")
                    if hbody is not None:
                        ops.extend(flow(hbody, depth + 1, budget - len(ops)))
                finalizer = field_node(c, "finalizer")
                if finalizer is not None:
                    add(finalizer, "FINALLY")
                    fbody = field_node(finalizer, "body")
                    ops.extend(flow(fbody or finalizer, depth + 1, budget - len(ops)))
            elif t in {"switch_case", "switch_default"}:
                value = field_node(c, "value")
                add(c, "CASE", ntext(value, 80) if value is not None else "default")
                ops.extend(flow(c, depth + 1, budget - len(ops)))
            elif t == "expression_statement" and c.named_children:
                expression = c.named_children[0]
                if expression.type in ("call_expression", "await_expression", "new_expression"):
                    add(c, "CALL", ntext(expression, 120), risk=ts_risk(expression))
                elif expression.type == "assignment_expression":
                    value = field_node(expression, "right")
                    target = field_node(expression, "left")
                    if value is not None and value.type in ("call_expression", "await_expression", "new_expression"):
                        add(c, "CALL", ntext(value, 120), ntext(target, 40), ts_risk(value))
            elif t in ("lexical_declaration", "variable_declaration"):
                for d in c.named_children:
                    if d.type == "variable_declarator":
                        value = field_node(d, "value")
                        if value is not None and value.type in ("call_expression", "await_expression"):
                            name = field_node(d, "name")
                            add(d, "CALL", ntext(value, 120), binds=ntext(name, 40) if name is not None else "",
                                risk=ts_risk(value))
            elif t in {"break_statement", "continue_statement"}:
                add(c, "BREAK" if t.startswith("break") else "CONTINUE")
            else:
                ops.extend(flow(c, depth, budget - len(ops)))
        return ops[:budget]

    def function_symbol(node, qualified, decorators=None):
        decorators = list(decorators or [])
        calls, raises, effects = [], [], {}
        body = field_node(node, "body")
        if body is not None:
            collect_calls(body, calls, effects)
            collect_raises(body, raises)
        params = field_node(node, "parameters") or field_node(node, "parameter")
        rtype = field_node(node, "return_type")
        a, b = span_with_decorators(node, decorators)
        is_async = any(ch.type == "async" for ch in node.children)
        signature = ntext(params, 240) if params is not None else ""
        if signature.startswith("(") and signature.endswith(")"):
            signature = signature[1:-1]
        if body is None:
            body_flow = []
        elif body.type in {"statement_block", "function_body"}:
            body_flow = flow(body)
        else:
            body_flow = [FlowOp("RET", ntext(body, 140), depth=0,
                                line=body.start_point[0] + 1, risk=ts_risk(body))]
        return Symbol(
            kind="function", name=qualified, span=(a, b),
            signature=signature,
            returns=ntext(rtype, 60).lstrip(": ") if rtype is not None else "",
            slice8=slice_sha8(text, a, b), calls=calls, raises=raises,
            flow=body_flow, is_async=is_async, effects=effects,
            decorators=[ntext(decorator, 100) for decorator in decorators],
        )

    symbols: list[Symbol] = []
    unsupported_runtime: list[tuple[str, int]] = []

    def add_simple(kind_name, name, node, detail="", decorators=None, bases=""):
        decorators = list(decorators or [])
        a, b = span_with_decorators(node, decorators)
        symbols.append(Symbol(
            kind=kind_name, name=name, detail=detail, bases=bases, span=(a, b),
            slice8=slice_sha8(text, a, b),
            decorators=[ntext(decorator, 100) for decorator in decorators],
        ))

    def commonjs_assignment(node, prefix) -> bool:
        left = field_node(node, "left")
        right = field_node(node, "right")
        if left is None or right is None:
            return False
        target = ntext(left, 100)
        if not (target == "module.exports" or target.startswith("module.exports.") or target.startswith("exports.")):
            return False
        if right.type in {"function_expression", "arrow_function", "generator_function"}:
            symbols.append(function_symbol(right, prefix + target))
        elif right.type == "object":
            for member in right.named_children:
                if member.type == "method_definition":
                    name = field_node(member, "name")
                    symbols.append(function_symbol(member, prefix + target + "." + ntext(name, 60)))
                elif member.type == "pair":
                    key = field_node(member, "key")
                    value = field_node(member, "value")
                    if value is not None and value.type in {"function_expression", "arrow_function"}:
                        symbols.append(function_symbol(value, prefix + target + "." + ntext(key, 60)))
                    else:
                        key_text = ntext(key, 60)
                        add_simple("const", prefix + target + "." + key_text, member,
                                   "=" + ntext(value, 120,
                                                secret_hint=bool(SECRET_NAME.search(key_text))))
        else:
            add_simple("const", prefix + target, node, "=" + ntext(right, 160,
                       secret_hint=bool(SECRET_NAME.search(target))))
        return True

    def visit_container(node, prefix="", in_class=False):
        pending_decorators = []
        for child in node.named_children:
            if child.type == "decorator":
                pending_decorators.append(child)
                continue
            visit(child, prefix, in_class, pending_decorators)
            pending_decorators = []

    def visit(node, prefix="", in_class=False, decorators=None):
        decorators = list(decorators or [])
        node_type = node.type
        embedded_decorators = [child for child in node.named_children if child.type == "decorator"]
        all_decorators = decorators + embedded_decorators
        if node_type in {"program", "statement_block", "class_body", "interface_body"}:
            visit_container(node, prefix, in_class)
        elif node_type == "import_statement":
            add_simple("import", "", node, ntext(node, 180))
        elif node_type in {"lexical_declaration", "variable_declaration"}:
            for declaration in node.named_children:
                if declaration.type != "variable_declarator":
                    continue
                name_node = field_node(declaration, "name")
                value = field_node(declaration, "value")
                name = prefix + ntext(name_node, 80)
                if value is not None and value.type in {"arrow_function", "function_expression", "generator_function"}:
                    symbols.append(function_symbol(value, name))
                else:
                    detail = "=" + ntext(value, 160, secret_hint=bool(SECRET_NAME.search(name))) if value is not None else ""
                    add_simple("const", name, declaration, detail)
        elif node_type in _TS_FUNC_NODES:
            name_node = field_node(node, "name")
            name = ntext(name_node, 80) if name_node is not None else "<anonymous>"
            symbols.append(function_symbol(node, prefix + name, all_decorators))
        elif node_type in {"class_declaration", "abstract_class_declaration"}:
            name_node = field_node(node, "name")
            local_name = ntext(name_node, 80) if name_node is not None else "<anonymous>"
            class_name = prefix + local_name
            heritage = next((child for child in node.named_children if child.type == "class_heritage"), None)
            add_simple("class", class_name, node, decorators=all_decorators,
                       bases=ntext(heritage, 160) if heritage is not None else "")
            body = field_node(node, "body")
            if body is not None:
                visit_container(body, class_name + ".", True)
        elif node_type == "interface_declaration":
            name_node = field_node(node, "name")
            name = prefix + ntext(name_node, 80)
            add_simple("type", name, node, ntext(node, 300))
            body = field_node(node, "body")
            if body is not None:
                visit_container(body, name + ".", True)
        elif node_type == "type_alias_declaration":
            name_node = field_node(node, "name")
            add_simple("type", prefix + ntext(name_node, 80), node, ntext(node, 300))
        elif node_type == "enum_declaration":
            name_node = field_node(node, "name")
            add_simple("enum", prefix + ntext(name_node, 80), node, ntext(node, 300))
        elif node_type == "internal_module":
            name_node = field_node(node, "name")
            namespace = prefix + ntext(name_node, 80)
            add_simple("namespace", namespace, node)
            body = field_node(node, "body")
            if body is not None:
                visit_container(body, namespace + ".", False)
        elif node_type == "ambient_declaration":
            visit_container(node, prefix, in_class)
        elif node_type == "export_statement":
            declaration = field_node(node, "declaration")
            value = field_node(node, "value")
            if declaration is not None:
                visit(declaration, prefix, in_class, all_decorators)
            elif value is not None and value.type in {"arrow_function", "function_expression"}:
                symbols.append(function_symbol(value, prefix + "default", all_decorators))
            elif value is not None:
                add_simple("export", prefix + "default", node, ntext(value, 180))
            else:
                add_simple("export", "", node, ntext(node, 200))
        elif node_type == "expression_statement" and node.named_children:
            expression = node.named_children[0]
            if expression.type == "internal_module":
                visit(expression, prefix, in_class)
            elif expression.type == "assignment_expression" and commonjs_assignment(expression, prefix):
                return
            else:
                add_simple("module", prefix + "<module-op>", node, ntext(node, 220))
        elif node_type in {"public_field_definition", "field_definition", "property_signature"}:
            name_node = field_node(node, "name")
            value = field_node(node, "value")
            name = prefix + ntext(name_node, 80)
            if value is not None:
                detail = "=" + ntext(value, 140, secret_hint=bool(SECRET_NAME.search(name)))
            elif node_type == "property_signature":
                type_node = field_node(node, "type")
                optional = "?" if any(child.type == "?" for child in node.children) else ""
                detail = optional + ntext(
                    type_node, 160, redact_literals=bool(SECRET_NAME.search(name))
                )
            else:
                type_node = field_node(node, "type")
                detail = ntext(type_node, 160) if type_node is not None else ""
            add_simple("const", name, node, detail)
        elif node_type in {"comment", "empty_statement"}:
            return
        else:
            # Keep valid but unmodelled runtime syntax visible rather than
            # silently producing an authoritative-looking empty page.
            add_simple("module", prefix + f"<unsupported:{node_type}>", node, ntext(node, 220))
            unsupported_runtime.append((node_type, span(node)[0]))

    visit(tree.root_node)

    all_nodes = list(walk_nodes(tree.root_node))
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    omit = {
        "docstrings": 0,
        "comments": sum(1 for node in all_nodes if node.type == "comment"),
        "blank": sum(1 for l in lines if not l.strip()),
        "assignments": sum(1 for node in all_nodes if node.type in {
            "variable_declarator", "assignment_expression", "augmented_assignment_expression",
            "assignment_pattern", "enum_assignment", "public_field_definition", "field_definition",
        }),
        "unsupported": len(unsupported_runtime),
    }

    parse_error = ""
    if tree.root_node.has_error:
        def first_error_line(n):
            if n.type == "ERROR" or n.is_missing:
                return n.start_point[0] + 1
            for c in n.children:
                found = first_error_line(c)
                if found is not None:
                    return found
            return None
        line = first_error_line(tree.root_node)
        if line is None:
            line = 1
        parse_error = f"tree-sitter reported syntax errors (first at L{line})"

    return ModuleIR(lang_name, "tree-sitter", symbols, omit, parse_error=parse_error)


def emit_il(module: ModuleIR, source_rel: str, source_sha256: str,
            repo_revision: str = "none", tier: str = "L2") -> str:
    level = _TIER_LEVEL[tier]
    propagate(module)
    apply_semantic_handles(module)
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
        # Group imports into contiguous runs
        sorted_imports = sorted(imports, key=lambda s: s.span[0])
        runs = []
        current_run = [sorted_imports[0]]
        for imp in sorted_imports[1:]:
            # Start new run if gap > 1 line after previous import's end
            if imp.span[0] - current_run[-1].span[1] > 1:
                runs.append(current_run)
                current_run = [imp]
            else:
                current_run.append(imp)
        runs.append(current_run)

        # Emit one IMPORTS line per run
        for run in runs:
            lo = min(s.span[0] for s in run)
            hi = max(s.span[1] for s in run)
            out.append(f"IMPORTS {'; '.join(s.detail for s in run)} @L{lo}-{hi}")
    for s in module.symbols:
        if s.kind == "const":
            tag = ""
            if s.risk:
                tag = f" !FAULT({s.risk})"
                faults.append(f"{s.risk}(L{s.span[0]})")
            out.append(f"C {s.name}{s.detail} @L{s.span[0]}{tag}")
        elif s.kind in {"type", "enum", "namespace", "export", "module"}:
            label = {
                "type": "TYPE", "enum": "ENUM", "namespace": "NAMESPACE",
                "export": "EXPORT", "module": "MODULE",
            }[s.kind]
            handle = s.semantic8 or s.slice8
            detail = (" " + s.detail) if s.detail else ""
            out.append(f"{label} {s.name}{detail} @L{s.span[0]}-{s.span[1]} ^{handle}")
    for s in module.symbols:
        if s.kind == "class":
            handle = s.semantic8 or s.slice8
            out.extend(["", f"CLASS {s.name}({s.bases}) @L{s.span[0]}-{s.span[1]} ^{handle}"])
            if s.decorators:
                out.append("  DECORATORS " + ", ".join(s.decorators))
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
