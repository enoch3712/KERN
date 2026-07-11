#!/usr/bin/env python3
"""React semantic adapter: upgrades tree-sitter function symbols to KERN-IL components."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

JSX_TYPES = {"jsx_element", "jsx_self_closing_element", "jsx_fragment"}
HOOK_RE = re.compile(r"^use[A-Z]\w*$")
COMPONENT_NAME_RE = re.compile(r"^[A-Z]")
FN_TYPES = {"arrow_function", "function_expression", "function_declaration",
            "generator_function_declaration", "method_definition"}


@dataclass
class HookUse:
    kind: str            # STATE | CTX | REF | HOOK | EFFECT
    detail: str
    line: int
    risk: str = ""
    flow: list = field(default_factory=list)   # EFFECT body ops for L3


@dataclass
class EventUse:
    target: str          # "Card.onClick"
    action: str          # "set open=true" | callee name
    line: int


@dataclass
class RenderNode:
    tag: str             # "Card" | "IF open" | "FOR item in items" | "ELSE" | "{expr}" | text
    attrs: str = ""
    line: int = 0
    is_component: bool = False
    is_structure: bool = False   # IF/ELSE/FOR — survives L2 collapse
    risk: str = ""
    children: list = field(default_factory=list)


def _unwrap_parens(n):
    while n is not None and n.type == "parenthesized_expression":
        inner = n.named_children
        n = inner[0] if inner else None
    return n


def _returns_jsx(fn_node) -> bool:
    body = fn_node.child_by_field_name("body")
    if body is None:
        return False
    if body.type != "statement_block":
        u = _unwrap_parens(body)
        return u is not None and u.type in JSX_TYPES
    stack = list(body.named_children)
    while stack:
        n = stack.pop()
        if n.type in FN_TYPES:
            continue  # returns inside nested functions don't count
        if n.type == "return_statement":
            for ch in n.named_children:
                u = _unwrap_parens(ch)
                if u is not None and u.type in JSX_TYPES:
                    return True
        stack.extend(n.named_children)
    return False


def _extract_props(fn_node, ntext) -> list:
    params = fn_node.child_by_field_name("parameters")
    if params is None:
        single = fn_node.child_by_field_name("parameter")
        first = single
    else:
        named = params.named_children
        first = named[0] if named else None
    if first is None:
        return []
    if first.type in ("required_parameter", "optional_parameter"):
        pattern = first.child_by_field_name("pattern")
        if pattern is not None:
            first = pattern
    if first.type == "object_pattern":
        out = []
        for p in first.named_children:
            if p.type == "shorthand_property_identifier_pattern":
                out.append(ntext(p, 60))
            elif p.type == "object_assignment_pattern":
                left = p.child_by_field_name("left")
                right = p.child_by_field_name("right")
                out.append(f"{ntext(left, 60)}={ntext(right, 60)}")
            elif p.type == "pair_pattern":
                out.append(ntext(p, 60))
            elif p.type == "rest_pattern":
                out.append(ntext(p, 60))
        return out
    return [ntext(first, 60)]


EFFECT_HOOKS = {"useEffect", "useLayoutEffect"}
COND_EXPR_TYPES = {"binary_expression", "ternary_expression"}


def _call_parts(value, ntext):
    """(callee_text, args_nodes) for a call_expression, else (None, [])."""
    if value is None or value.type != "call_expression":
        return None, []
    callee = value.child_by_field_name("function")
    args = value.child_by_field_name("arguments")
    return (ntext(callee, 80) if callee is not None else None,
            list(args.named_children) if args is not None else [])


def _extract_hooks(body, react, ntext, flow_fn):
    hooks, setters, faults = react["hooks"], {}, react["faults"]
    for stmt in body.named_children:
        line = stmt.start_point[0] + 1
        if stmt.type in ("lexical_declaration", "variable_declaration"):
            for d in stmt.named_children:
                if d.type != "variable_declarator":
                    continue
                name_node = d.child_by_field_name("name")
                value = d.child_by_field_name("value")
                callee, args = _call_parts(value, ntext)
                if callee is None:
                    continue
                tail = callee.split(".")[-1]
                if not HOOK_RE.match(tail):
                    continue
                risk = "aliased-hook" if "." in callee else ""
                if risk:
                    faults.append((risk, line))
                name_txt = ntext(name_node, 60)
                if tail == "useState" and name_node.type == "array_pattern":
                    elems = [ntext(e, 40) for e in name_node.named_children]
                    state = elems[0] if elems else "?"
                    init = ntext(args[0], 80) if args else "undefined"
                    if len(elems) > 1:
                        setters[elems[1]] = state
                    hooks.append(HookUse("STATE", f"{state}={init}", line, risk))
                elif tail == "useReducer":
                    hooks.append(HookUse("STATE", f"{name_txt}={ntext(value, 120)}", line, risk))
                elif tail == "useContext":
                    hooks.append(HookUse("CTX", f"{name_txt}={ntext(value, 80)}", line, risk))
                elif tail == "useRef":
                    hooks.append(HookUse("REF", name_txt, line, risk))
                else:
                    hooks.append(HookUse("HOOK", f"{name_txt}={ntext(value, 120)}", line, risk))
        elif stmt.type == "expression_statement" and stmt.named_children:
            value = stmt.named_children[0]
            if value.type in COND_EXPR_TYPES:
                _fault_conditional_hooks(value, faults, ntext)
                continue
            callee, args = _call_parts(value, ntext)
            if callee is None:
                continue
            tail = callee.split(".")[-1]
            if tail in EFFECT_HOOKS:
                risk = "aliased-hook" if "." in callee else ""
                if risk:
                    faults.append((risk, line))
                deps = f"deps={ntext(args[1], 100)}" if len(args) >= 2 else "deps=EVERY-RENDER"
                ops = []
                if args and args[0].type in ("arrow_function", "function_expression"):
                    cb_body = args[0].child_by_field_name("body")
                    if cb_body is not None and cb_body.type == "statement_block":
                        ops = flow_fn(cb_body)
                hooks.append(HookUse("EFFECT", deps, line, risk, flow=ops))
            elif HOOK_RE.match(tail):
                risk = "aliased-hook" if "." in callee else ""
                if risk:
                    faults.append((risk, line))
                hooks.append(HookUse("HOOK", ntext(value, 120), line, risk))
        else:
            _fault_conditional_hooks(stmt, faults, ntext)
    react["setters"] = setters


def _fault_conditional_hooks(node, faults, ntext):
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type in FN_TYPES or n.type in JSX_TYPES:
            continue
        if n.type == "call_expression":
            callee = n.child_by_field_name("function")
            txt = ntext(callee, 80) if callee is not None else ""
            if HOOK_RE.match(txt.split(".")[-1] or ""):
                faults.append(("conditional-hook", n.start_point[0] + 1))
        stack.extend(n.named_children)


def lower_components(fn_nodes, ntext, flow_fn) -> bool:
    upgraded = False
    for sym, node in fn_nodes:
        short = sym.name.split(".")[-1]
        if not COMPONENT_NAME_RE.match(short) or not _returns_jsx(node):
            continue
        sym.kind = "component"
        sym.react = {
            "wrapper": sym.decorators[0] if sym.decorators else "",
            "props": _extract_props(node, ntext),
            "hooks": [],
            "events": [],
            "render": [],
            "faults": [],
        }
        body = node.child_by_field_name("body")
        if body is not None and body.type == "statement_block":
            _extract_hooks(body, sym.react, ntext, flow_fn)
        else:
            sym.react["setters"] = {}
        upgraded = True
    return upgraded
