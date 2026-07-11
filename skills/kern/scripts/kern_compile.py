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
