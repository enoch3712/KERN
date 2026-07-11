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


_RISK_CALL = [
    ("regex", re.compile(r"^re\.(compile|match|search|sub|split|fullmatch|findall|finditer)$")),
    ("crypto", re.compile(r"^(hashlib|hmac|secrets)\.")),
    ("concurrency", re.compile(r"^(threading|asyncio|multiprocessing|concurrent)\.")),
]
_TRY_TYPES = (ast.Try, getattr(ast, "TryStar", ast.Try))

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
        flow=flow_ops(node.body),
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
            symbol = Symbol(kind="const", name=names,
                           detail=expr_text(node.value, 100, hint),
                           span=(node.lineno, node.end_lineno or node.lineno))
            symbol.risk = expr_risk(node.value)
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
                    param = field_node(handler, "parameter")
                    if param is None:
                        param = field_node(handler, "parameters")
                    add(handler, "CATCH", ntext(param or handler, 40))
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
