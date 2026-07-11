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
        upgraded = True
    return upgraded
