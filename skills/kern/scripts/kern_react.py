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


RENDER_BUDGET = 200


def _jsx_name(el, ntext):
    """(name_text, is_component, risk) for a jsx element node."""
    opening = el
    if el.type == "jsx_element":
        opening = el.named_children[0] if el.named_children else el
    name = opening.child_by_field_name("name")
    if name is None:
        return ("<>", False, "")
    txt = ntext(name, 60)
    if name.type in ("member_expression", "jsx_namespace_name"):
        return (txt, True, "dynamic-component")
    if COMPONENT_NAME_RE.match(txt):
        return (txt, True, "")
    return (txt, False, "")


def _jsx_attrs(el, ntext):
    """(attrs_text, spread_only, event_attrs) from an element's opening tag.
    event_attrs: list of (attr_name, expr_node, element_name_text)."""
    opening = el
    if el.type == "jsx_element":
        opening = el.named_children[0] if el.named_children else el
    parts, named, spread, events = [], 0, 0, []
    for a in opening.named_children:
        if a.type == "jsx_attribute":
            aname_node = a.named_children[0] if a.named_children else None
            aname = ntext(aname_node, 40) if aname_node is not None else ""
            value = a.named_children[1] if len(a.named_children) > 1 else None
            if re.match(r"^on[A-Z]", aname) and value is not None:
                events.append((aname, value))
                continue
            named += 1
            parts.append(ntext(a, 60))
        elif a.type == "jsx_expression":  # {...spread}
            spread += 1
            parts.append(ntext(a, 40).strip("{}"))
    return " ".join(parts), (spread > 0 and named == 0), events


def _lower_jsx(node, ntext, counter, events_out, element_name=""):
    """Lower one JSX node (or jsx child) to list[RenderNode]."""
    if counter["n"] >= RENDER_BUDGET:
        counter["dropped"] = True
        return []
    n = _unwrap_parens(node)
    if n is None:
        return []
    out = []

    def make(tag, **kw):
        if counter["n"] >= RENDER_BUDGET:
            counter["dropped"] = True
            return None
        counter["n"] += 1
        return RenderNode(tag=tag, line=n.start_point[0] + 1, **kw)

    if n.type in ("jsx_element", "jsx_self_closing_element"):
        name, is_comp, risk = _jsx_name(n, ntext)
        attrs, spread_only, ev = _jsx_attrs(n, ntext)
        if spread_only:
            risk = f"{risk}+spread-props" if risk else "spread-props"
        rn = make(name, attrs=attrs, is_component=is_comp, risk=risk)
        if rn is None:
            return []
        for aname, value in ev:
            events_out.append((name, aname, value))
        if n.type == "jsx_element":
            for ch in n.named_children[1:-1]:
                rn.children.extend(_lower_jsx(ch, ntext, counter, events_out, name))
        out.append(rn)
    elif n.type == "jsx_fragment":
        for ch in n.named_children:
            out.extend(_lower_jsx(ch, ntext, counter, events_out, element_name))
    elif n.type == "jsx_text":
        txt = ntext(n, 60)
        if txt:
            rn = make(txt)
            if rn is not None:
                out.append(rn)
    elif n.type == "jsx_expression":
        inner = n.named_children[0] if n.named_children else None
        out.extend(_lower_expr(inner, ntext, counter, events_out, element_name))
    elif n.type in ("binary_expression", "ternary_expression", "call_expression",
                    "arrow_function", "function_expression"):
        out.extend(_lower_expr(n, ntext, counter, events_out, element_name))
    return out


def _lower_expr(n, ntext, counter, events_out, element_name):
    n = _unwrap_parens(n)
    if n is None:
        return []
    if counter["n"] >= RENDER_BUDGET:
        counter["dropped"] = True
        return []

    def make(tag, **kw):
        if counter["n"] >= RENDER_BUDGET:
            counter["dropped"] = True
            return None
        counter["n"] += 1
        return RenderNode(tag=tag, line=n.start_point[0] + 1, **kw)

    if n.type in JSX_TYPES:
        return _lower_jsx(n, ntext, counter, events_out, element_name)
    if n.type == "binary_expression":
        op = n.child_by_field_name("operator")
        right = _unwrap_parens(n.child_by_field_name("right"))
        if op is not None and op.type == "&&" and right is not None and right.type in JSX_TYPES:
            left = n.child_by_field_name("left")
            rn = make(f"IF {ntext(left, 80)}", is_structure=True)
            if rn is None:
                return []
            rn.children = _lower_jsx(right, ntext, counter, events_out, element_name)
            return [rn]
    elif n.type == "ternary_expression":
        cond = n.child_by_field_name("condition")
        cons = _unwrap_parens(n.child_by_field_name("consequence"))
        alt = _unwrap_parens(n.child_by_field_name("alternative"))
        branches = []
        if cons is not None and cons.type in JSX_TYPES:
            rn = make(f"IF {ntext(cond, 80)}", is_structure=True)
            if rn is not None:
                rn.children = _lower_jsx(cons, ntext, counter, events_out, element_name)
                branches.append(rn)
        if alt is not None and alt.type in JSX_TYPES:
            el = make("ELSE", is_structure=True)
            if el is not None:
                el.children = _lower_jsx(alt, ntext, counter, events_out, element_name)
                branches.append(el)
        if branches:
            return branches
    elif n.type == "call_expression":
        callee = n.child_by_field_name("function")
        if callee is not None and callee.type == "member_expression":
            prop = callee.child_by_field_name("property")
            if prop is not None and ntext(prop, 20) == "map":
                receiver = callee.child_by_field_name("object")
                args = n.child_by_field_name("arguments")
                cb = next((a for a in args.named_children
                           if a.type in ("arrow_function", "function_expression")), None) if args is not None else None
                if cb is not None:
                    params = cb.child_by_field_name("parameters")
                    single = cb.child_by_field_name("parameter")
                    if single is not None:
                        param_txt = ntext(single, 40)
                    elif params is not None and params.named_children:
                        param_txt = ntext(params.named_children[0], 40)
                    else:
                        param_txt = "_"
                    rn = make(f"FOR {param_txt} in {ntext(receiver, 60)}", is_structure=True)
                    if rn is None:
                        return []
                    body = cb.child_by_field_name("body")
                    rn.children = _lower_jsx(body, ntext, counter, events_out, element_name) if body is not None else []
                    return [rn]
    elif n.type in ("arrow_function", "function_expression"):
        rn = make("{" + ntext(n, 60) + "}", risk="render-prop")
        return [rn] if rn is not None else []
    rn = make("{" + ntext(n, 80) + "}")
    return [rn] if rn is not None else []


def _extract_render(node, react, ntext, events_out):
    body = node.child_by_field_name("body")
    if body is None:
        return
    counter = {"n": 0, "dropped": False}
    jsx_root = None
    if body.type != "statement_block":
        jsx_root = _unwrap_parens(body)
        if jsx_root is not None and jsx_root.type not in JSX_TYPES:
            jsx_root = None
    else:
        best_line = -1
        stack = list(body.named_children)
        while stack:
            s = stack.pop()
            if s.type in FN_TYPES:
                continue
            if s.type == "return_statement":
                for ch in s.named_children:
                    u = _unwrap_parens(ch)
                    if u is not None and u.type in JSX_TYPES and s.start_point[0] > best_line:
                        best_line = s.start_point[0]
                        jsx_root = u   # textually last JSX-bearing return wins
            else:
                stack.extend(s.named_children)
    if jsx_root is None:
        return
    react["render"] = _lower_jsx(jsx_root, ntext, counter, events_out)
    if counter.get("dropped"):
        react["render"].append(RenderNode(tag="…", risk="render-truncated",
                                          line=jsx_root.start_point[0] + 1))


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
        raw_events = []
        _extract_render(node, sym.react, ntext, raw_events)
        sym.react["_raw_events"] = raw_events   # consumed by Task 5
        upgraded = True
    return upgraded
