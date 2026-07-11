#!/usr/bin/env python3
"""React semantic adapter: upgrades tree-sitter function symbols to KERN-IL components."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

# Safe: kern_compile imports kern_react only lazily inside functions, so there
# is no cycle at module-exec time.
from kern_compile import (
    SECRET_NAME,
    SECRET_VALUE,
    FlowOp,
    _render_provenanced,
    flow_lines,
    one_line_text,
    sanitize_string,
    sha256_hex,
)

JSX_TYPES = {"jsx_element", "jsx_self_closing_element", "jsx_fragment"}
HOOK_RE = re.compile(r"^use[A-Z]\w*$")
COMPONENT_NAME_RE = re.compile(r"^[A-Z]")
COMPONENT_FN_TYPES = {"arrow_function", "function_expression", "function_declaration"}
FN_TYPES = {"arrow_function", "function_expression", "function_declaration",
            "generator_function", "generator_function_declaration", "method_definition"}
NESTED_SCOPE_TYPES = FN_TYPES | {
    "class", "class_declaration", "abstract_class_declaration",
}
_STR_ASSIGN = re.compile(
    r"(\w+)(\s*=\s*)(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|`[^`]*`)")


def _scrub_named_secrets(text: str) -> str:
    """Redact string literals assigned to secret-named identifiers or JSX
    attributes inside already-rendered text (signatures, flow-op details)."""
    def sub(m):
        if SECRET_NAME.search(m.group(1)):
            return m.group(1) + m.group(2) + sanitize_string(m.group(3), secret_hint=True)
        return m.group(0)
    return _STR_ASSIGN.sub(sub, text)


def _raw_text(node) -> str:
    if node is None:
        return ""
    value = getattr(node, "text", b"")
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _cap_text(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    digest = sha256_hex(text.encode("utf-8", "surrogatepass"))[:12]
    return text[:max(0, cap - 24)] + f"…<sha256={digest}>"


def _secret_pattern_values(root, inherited_secret=False) -> list:
    """Return default-value nodes whose binding/property name is secret.

    Tree-sitter's generic text sanitizer does not treat
    ``object_assignment_pattern`` as an assignment, and a secret property may
    bind to a differently named local (``password: value = make(...)``).  Walk
    the pattern itself so compound defaults and nested destructuring are
    handled by syntax rather than by rendered-text regexes.
    """
    if root is None:
        return []
    out = []

    def walk(node, secret=False):
        if node.type in {"string", "template_string"}:
            if SECRET_VALUE.search(_raw_text(node)):
                out.append(node)
            return
        if node.type in {"call_expression", "new_expression"}:
            callee = (node.child_by_field_name("function")
                      or node.child_by_field_name("constructor"))
            if callee is not None and SECRET_NAME.search(_raw_text(callee)):
                arguments = node.child_by_field_name("arguments")
                if arguments is not None:
                    out.extend(arguments.named_children)
                    return
        if node.type in {"object_assignment_pattern", "assignment_pattern"}:
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            named = bool(left is not None and SECRET_NAME.search(_raw_text(left)))
            if right is not None and (secret or named):
                out.append(right)
                return
            if left is not None:
                walk(left, secret)
            if right is not None:
                walk(right, secret)
            return
        if node.type == "pair_pattern":
            key = node.child_by_field_name("key")
            value = node.child_by_field_name("value")
            named = bool(key is not None and SECRET_NAME.search(_raw_text(key)))
            if value is not None:
                walk(value, secret or named)
            return
        for child in node.named_children:
            walk(child, secret)

    walk(root, inherited_secret)
    return out


def _render_with_redactions(root, redactions, cap: int) -> str:
    """Render *root* while replacing selected descendant nodes atomically."""
    if root is None:
        return ""
    raw = getattr(root, "text", b"")
    if not isinstance(raw, bytes):
        raw = str(raw).encode("utf-8", "surrogatepass")
    ranges = []
    for node in redactions:
        if node.start_byte < root.start_byte or node.end_byte > root.end_byte:
            continue
        ranges.append((node.start_byte - root.start_byte,
                       node.end_byte - root.start_byte))
    selected = []
    for start, end in sorted(ranges, key=lambda item: (item[0], -(item[1] - item[0]))):
        if selected and start >= selected[-1][0] and end <= selected[-1][1]:
            continue
        if selected and start < selected[-1][1]:
            continue
        selected.append((start, end))
    pieces, cursor = [], 0
    for start, end in selected:
        pieces.append(raw[cursor:start])
        value = raw[start:end].decode("utf-8", "replace")
        pieces.append(sanitize_string(value, secret_hint=True).encode("utf-8"))
        cursor = end
    pieces.append(raw[cursor:])
    return _cap_text(one_line_text(b"".join(pieces).decode("utf-8", "replace")), cap)


def _append_fault(faults, risk: str, line: int):
    item = (risk, line)
    if item not in faults:
        faults.append(item)


@dataclass
class HookUse:
    kind: str            # STATE | CTX | REF | HOOK | EFFECT
    detail: str
    line: int
    risk: str = ""
    inherited_risk: str = ""
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
    while n is not None and n.type in {
        "parenthesized_expression", "as_expression", "satisfies_expression",
        "non_null_expression",
    }:
        inner = n.named_children
        n = inner[0] if inner else None
    return n


def _function_argument(arguments):
    """Return a function-valued first argument after TS expression wrappers."""
    if arguments is None or not arguments.named_children:
        return None
    candidate = _unwrap_parens(arguments.named_children[0])
    if (candidate is not None
            and candidate.type in {"arrow_function", "function_expression"}):
        return candidate
    return None


def _jsx_bearing(u) -> bool:
    """True for a JSX node, or a one-level conditional expression yielding JSX:
    `cond ? <X/> : <Y/>` or `cond && <X/>`. One level only — deterministic."""
    if u is None:
        return False
    if u.type in JSX_TYPES:
        return True
    if u.type == "ternary_expression":
        cons = _unwrap_parens(u.child_by_field_name("consequence"))
        alt = _unwrap_parens(u.child_by_field_name("alternative"))
        return ((cons is not None and cons.type in JSX_TYPES)
                or (alt is not None and alt.type in JSX_TYPES))
    if u.type == "binary_expression":
        op = u.child_by_field_name("operator")
        right = _unwrap_parens(u.child_by_field_name("right"))
        return (op is not None and op.type == "&&"
                and right is not None and right.type in JSX_TYPES)
    return False


def _returns_jsx(fn_node) -> bool:
    body = fn_node.child_by_field_name("body")
    if body is None:
        return False
    if body.type != "statement_block":
        return _jsx_bearing(_unwrap_parens(body))
    stack = list(body.named_children)
    while stack:
        n = stack.pop()
        if n.type in NESTED_SCOPE_TYPES:
            continue  # returns inside nested functions don't count
        if n.type == "return_statement":
            for ch in n.named_children:
                if _jsx_bearing(_unwrap_parens(ch)):
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
                left_txt = ntext(left, 60)
                values = _secret_pattern_values(p)
                right_txt = (_render_with_redactions(right, [right], 60)
                             if right in values else ntext(right, 60))
                out.append(f"{left_txt}={right_txt}")
            elif p.type == "pair_pattern":
                values = _secret_pattern_values(p)
                out.append(_render_with_redactions(p, values, 60)
                           if values else ntext(p, 60))
            elif p.type == "rest_pattern":
                out.append(ntext(p, 60))
        return out
    return [ntext(first, 60)]


EFFECT_HOOKS = {"useEffect", "useLayoutEffect"}


def _optional_chain_reaches_result(node) -> bool:
    """Whether an optional-chain segment can short-circuit this expression.

    Follow only the receiver/call chain. Optional expressions inside computed
    property keys are evaluated values; they do not make the outer call
    conditional.
    """
    # Parentheses terminate optional-chain short-circuiting. Type-only TS
    # wrappers are runtime-transparent, so follow those but never cross an
    # explicit grouping boundary.
    while node is not None and node.type in {
        "as_expression", "satisfies_expression", "non_null_expression",
    }:
        node = node.named_children[0] if node.named_children else None
    if node is None:
        return False
    if node.type == "parenthesized_expression":
        return False
    if node.type == "call_expression":
        return _is_optional_call(node)
    if node.type not in {"member_expression", "subscript_expression"}:
        return False
    if any(child.type in {"optional_chain", "?."} for child in node.children):
        return True
    return _optional_chain_reaches_result(node.child_by_field_name("object"))


def _is_optional_call(node) -> bool:
    if node is None or node.type != "call_expression":
        return False
    if any(child.type == "?." for child in node.children):
        return True
    return _optional_chain_reaches_result(node.child_by_field_name("function"))


def _is_conditional_expression(node) -> bool:
    if node is None:
        return False
    if node.type == "ternary_expression" or _is_optional_call(node):
        return True
    if node.type in {"binary_expression", "augmented_assignment_expression"}:
        operator = node.child_by_field_name("operator")
        return operator is not None and operator.type in {
            "&&", "||", "??", "&&=", "||=", "??=",
        }
    return False


def _call_parts(value, ntext):
    """(callee_text, args_nodes) for a call_expression, else (None, [])."""
    value = _unwrap_parens(value)
    if value is None or value.type != "call_expression":
        return None, []
    callee = value.child_by_field_name("function")
    args = value.child_by_field_name("arguments")
    return (ntext(callee, 80) if callee is not None else None,
            list(args.named_children) if args is not None else [])


def _hook_info(callee: str, aliases: dict, shadowed=()) -> tuple[str, bool, bool]:
    tail = callee.split(".")[-1]
    if "." not in callee:
        if callee in shadowed:
            return callee, False, False
        canonical = aliases.get(callee, callee)
        return canonical, canonical != callee, bool(HOOK_RE.match(canonical))
    # A renamed import is a local binding, never a property on an unrelated
    # object.  Keep namespace-style canonical hook calls conservative, but do
    # not let `obj.useS()` inherit `import { useState as useS }` semantics.
    if tail in aliases:
        return tail, False, False
    return tail, True, bool(HOOK_RE.match(tail))


def _react_hook_aliases(fn_nodes, ntext) -> dict:
    """Map renamed named imports from React back to their canonical hook."""
    if not fn_nodes:
        return {}
    root = fn_nodes[0][1]
    while root.parent is not None:
        root = root.parent
    aliases = {}
    for statement in root.named_children:
        if statement.type != "import_statement":
            continue
        source = next((child for child in reversed(statement.named_children)
                       if child.type == "string"), None)
        if source is None or _raw_text(source).strip("'\"") != "react":
            continue
        stack = list(statement.named_children)
        while stack:
            node = stack.pop()
            if node.type == "import_specifier":
                original = node.child_by_field_name("name")
                alias = node.child_by_field_name("alias")
                original_txt = ntext(original, 60)
                local_txt = ntext(alias, 60) if alias is not None else original_txt
                if HOOK_RE.match(original_txt) and local_txt != original_txt:
                    aliases[local_txt] = original_txt
                continue
            stack.extend(node.named_children)
    return aliases


def _shadowed_hook_aliases(fn_node, aliases: dict) -> set[str]:
    """Return imported aliases rebound by this component's function scope."""
    if not aliases:
        return set()
    names = set()

    def bindings(node):
        if node is None or node.type == "type_annotation":
            return
        if node.type in {"identifier", "shorthand_property_identifier_pattern"}:
            text = _raw_text(node)
            if text in aliases:
                names.add(text)
            return
        if node.type in {"required_parameter", "optional_parameter"}:
            bindings(node.child_by_field_name("pattern"))
            return
        if node.type in {"assignment_pattern", "object_assignment_pattern"}:
            bindings(node.child_by_field_name("left"))
            return
        if node.type == "pair_pattern":
            bindings(node.child_by_field_name("value"))
            return
        for child in node.named_children:
            bindings(child)

    bindings(fn_node.child_by_field_name("parameters")
             or fn_node.child_by_field_name("parameter"))
    body = fn_node.child_by_field_name("body")
    if body is not None and body.type == "statement_block":
        for statement in body.named_children:
            if statement.type in {"lexical_declaration", "variable_declaration"}:
                for declaration in statement.named_children:
                    if declaration.type == "variable_declarator":
                        bindings(declaration.child_by_field_name("name"))
            elif statement.type in {"function_declaration", "class_declaration"}:
                bindings(statement.child_by_field_name("name"))
    return names


def _node_risk(node, ntext) -> str:
    stack = [node]
    while stack:
        child = stack.pop()
        if child.type == "regex":
            return "regex"
        if child.type in {"call_expression", "new_expression"}:
            callee = (child.child_by_field_name("function")
                      or child.child_by_field_name("constructor"))
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
        stack.extend(child.named_children)
    return ""


def _expression_flow(node, ntext, flow_fn=None) -> list:
    node = _unwrap_parens(node)
    if node is None:
        return []
    if node.type in {"arrow_function", "function_expression"}:
        body = node.child_by_field_name("body")
        if body is None:
            return []
        if body.type == "statement_block" and flow_fn is not None:
            return flow_fn(body)
        return _expression_flow(body, ntext, flow_fn)
    if node.type in {"call_expression", "new_expression", "await_expression"}:
        return [FlowOp("CALL", ntext(node, 120), line=node.start_point[0] + 1,
                       risk=_node_risk(node, ntext))]
    return [FlowOp("RET", ntext(node, 120), line=node.start_point[0] + 1,
                   risk=_node_risk(node, ntext))]


def _extract_hooks(body, react, ntext, flow_fn, aliases=None, shadowed=None):
    aliases = aliases or {}
    shadowed = shadowed or set()
    hooks, setters, faults = react["hooks"], {}, react["faults"]
    for stmt in body.named_children:
        line = stmt.start_point[0] + 1
        if stmt.type in ("lexical_declaration", "variable_declaration"):
            for d in stmt.named_children:
                if d.type != "variable_declarator":
                    continue
                name_node = d.child_by_field_name("name")
                value = d.child_by_field_name("value")
                _fault_nested_conditional_hooks(
                    value, faults, ntext, aliases, shadowed)
                callee, args = _call_parts(value, ntext)
                if callee is None:
                    continue
                tail, is_alias, is_hook = _hook_info(callee, aliases, shadowed)
                if not is_hook:
                    continue
                risk = "aliased-hook" if is_alias else ""
                inherited_risk = _node_risk(value, ntext)
                if risk:
                    _append_fault(faults, risk, line)
                name_txt = ntext(name_node, 60)

                def bound(value_txt, name=None):
                    # Name-hinted secret: redact the value bound to it.
                    if SECRET_NAME.search(name if name is not None else name_txt):
                        return sanitize_string(value_txt, secret_hint=True)
                    return value_txt

                if tail == "useState" and name_node.type == "array_pattern":
                    elems = [ntext(e, 40) for e in name_node.named_children]
                    state = elems[0] if elems else "?"
                    init = ntext(args[0], 80) if args else "undefined"
                    if len(elems) > 1:
                        setters[elems[1]] = state
                    hooks.append(HookUse("STATE", f"{state}={bound(init, state)}",
                                         line, risk, inherited_risk))
                elif tail == "useReducer":
                    hooks.append(HookUse("STATE", f"{name_txt}={bound(ntext(value, 120))}",
                                         line, risk, inherited_risk))
                elif tail == "useContext":
                    hooks.append(HookUse("CTX", f"{name_txt}={bound(ntext(value, 80))}",
                                         line, risk, inherited_risk))
                elif tail == "useRef":
                    hooks.append(HookUse("REF", name_txt, line, risk, inherited_risk))
                else:
                    hooks.append(HookUse("HOOK", f"{name_txt}={bound(ntext(value, 120))}",
                                         line, risk, inherited_risk))
        elif stmt.type == "expression_statement" and stmt.named_children:
            value = _unwrap_parens(stmt.named_children[0])
            if value is None:
                continue
            _fault_nested_conditional_hooks(
                value, faults, ntext, aliases, shadowed)
            if _is_conditional_expression(value):
                continue
            callee, args = _call_parts(value, ntext)
            if callee is None:
                continue
            tail, is_alias, is_hook = _hook_info(callee, aliases, shadowed)
            if not is_hook:
                continue
            if tail in EFFECT_HOOKS:
                risk = "aliased-hook" if is_alias else ""
                inherited_risk = _node_risk(value, ntext)
                if risk:
                    _append_fault(faults, risk, line)
                deps = f"deps={ntext(args[1], 100)}" if len(args) >= 2 else "deps=EVERY-RENDER"
                ops = []
                callback = _unwrap_parens(args[0]) if args else None
                if callback is not None and callback.type in ("arrow_function", "function_expression"):
                    cb_body = callback.child_by_field_name("body")
                    if cb_body is not None:
                        ops = (flow_fn(cb_body) if cb_body.type == "statement_block"
                               else _expression_flow(cb_body, ntext, flow_fn))
                hooks.append(HookUse("EFFECT", deps, line, risk,
                                     inherited_risk, flow=ops))
            else:
                risk = "aliased-hook" if is_alias else ""
                inherited_risk = _node_risk(value, ntext)
                if risk:
                    _append_fault(faults, risk, line)
                hooks.append(HookUse("HOOK", ntext(value, 120), line,
                                     risk, inherited_risk))
        else:
            if stmt.type in {
                "if_statement", "for_statement", "for_in_statement",
                "while_statement", "do_statement", "switch_statement",
                "try_statement",
            }:
                _fault_conditional_hooks(stmt, faults, ntext, aliases, shadowed)
            _fault_nested_conditional_hooks(
                stmt, faults, ntext, aliases, shadowed)
    react["setters"] = setters


def _fault_conditional_hooks(node, faults, ntext, aliases=None, shadowed=None):
    if node is None:
        return
    aliases = aliases or {}
    shadowed = shadowed or set()
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type in NESTED_SCOPE_TYPES:
            continue
        if n.type == "call_expression":
            _fault_hook_call(n, faults, ntext, aliases, shadowed)
        stack.extend(n.named_children)


def _fault_hook_call(node, faults, ntext, aliases=None, shadowed=None):
    """Fault *node* itself when it is a hook call, without scanning children."""
    if node is None or node.type != "call_expression":
        return
    aliases = aliases or {}
    shadowed = shadowed or set()
    callee = node.child_by_field_name("function")
    txt = ntext(callee, 80) if callee is not None else ""
    _, is_alias, is_hook = _hook_info(txt, aliases, shadowed)
    if is_hook:
        line = node.start_point[0] + 1
        _append_fault(faults, "conditional-hook", line)
        if is_alias:
            _append_fault(faults, "aliased-hook", line)


def _fault_nested_conditional_hooks(node, faults, ntext, aliases=None, shadowed=None):
    """Find guarded hook calls even when the conditional is nested in a call,
    assignment, or JSX expression, while leaving unguarded operands alone."""
    if node is None:
        return
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in NESTED_SCOPE_TYPES:
            continue
        if current.type == "ternary_expression":
            condition = current.child_by_field_name("condition")
            consequence = current.child_by_field_name("consequence")
            alternative = current.child_by_field_name("alternative")
            _fault_conditional_hooks(
                consequence, faults, ntext, aliases, shadowed)
            _fault_conditional_hooks(
                alternative, faults, ntext, aliases, shadowed)
            if condition is not None:
                stack.append(condition)
            continue
        if current.type in {"binary_expression", "augmented_assignment_expression"}:
            operator = current.child_by_field_name("operator")
            if operator is not None and operator.type in {
                "&&", "||", "??", "&&=", "||=", "??=",
            }:
                left = current.child_by_field_name("left")
                right = current.child_by_field_name("right")
                _fault_conditional_hooks(
                    right, faults, ntext, aliases, shadowed)
                if left is not None:
                    stack.append(left)
                continue
        if _is_optional_call(current):
            # The optional invocation and its arguments are guarded, while
            # evaluating the callee expression itself is not.
            _fault_hook_call(current, faults, ntext, aliases, shadowed)
            arguments = current.child_by_field_name("arguments")
            _fault_conditional_hooks(
                arguments, faults, ntext, aliases, shadowed)
            callee = current.child_by_field_name("function")
            if callee is not None:
                stack.append(callee)
            continue
        stack.extend(current.named_children)


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
    """(attrs_text, spread_only, event_attrs, risk) from an opening tag.
    event_attrs: list of (attr_name, value_node, secret_name_hint) triples."""
    opening = el
    if el.type == "jsx_element":
        opening = el.named_children[0] if el.named_children else el
    parts, named, spread, events, risk = [], 0, 0, [], ""
    for a in opening.named_children:
        if a.type == "jsx_attribute":
            aname_node = a.named_children[0] if a.named_children else None
            aname = ntext(aname_node, 40) if aname_node is not None else ""
            value = a.named_children[1] if len(a.named_children) > 1 else None
            if re.match(r"^on[A-Z]", aname) and value is not None:
                events.append((aname, value, bool(SECRET_NAME.search(aname))))
                continue
            callback = value
            if callback is not None and callback.type == "jsx_expression":
                callback = callback.named_children[0] if callback.named_children else None
            callback = _unwrap_parens(callback)
            if (callback is not None
                    and callback.type in {"arrow_function", "function_expression"}
                    and (aname == "children" or aname.startswith("render")
                         or _returns_jsx(callback))):
                risk = "render-prop"
            named += 1
            if SECRET_NAME.search(aname) and value is not None:
                parts.append(f"{aname}={sanitize_string(ntext(value, 60), secret_hint=True)}")
            else:
                parts.append(ntext(a, 60))
        elif a.type == "jsx_expression":  # {...spread}
            spread += 1
            parts.append(ntext(a, 40).strip("{}"))
    return " ".join(parts), (spread > 0 and named == 0), events, risk


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
        attrs, spread_only, ev, attr_risk = _jsx_attrs(n, ntext)
        if spread_only:
            risk = f"{risk}+spread-props" if risk else "spread-props"
        if attr_risk:
            risk = f"{risk}+{attr_risk}" if risk else attr_risk
        rn = make(name, attrs=attrs, is_component=is_comp, risk=risk)
        if rn is None:
            return []
        for aname, value, secret in ev:
            events_out.append((name, aname, value, secret))
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


def _jsx_returns(body) -> list:
    """Return ``(return_node, jsx_expression)`` pairs outside nested scopes."""
    found = []
    for node in _return_nodes(body):
        for child in node.named_children:
            value = _unwrap_parens(child)
            if _jsx_bearing(value):
                found.append((node, value))
                break
    return found


def _return_nodes(body) -> list:
    """Return all return statements outside nested function/class scopes."""
    found = []
    stack = list(body.named_children)
    while stack:
        node = stack.pop()
        if node.type in NESTED_SCOPE_TYPES:
            continue
        if node.type == "return_statement":
            found.append(node)
            continue
        stack.extend(node.named_children)
    return sorted(found, key=lambda node: node.start_byte)


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
                cb = _function_argument(args)
                if cb is not None:
                    params = cb.child_by_field_name("parameters")
                    single = cb.child_by_field_name("parameter")
                    if single is not None:
                        param_txt = ntext(single, 40)
                    elif params is not None and params.named_children:
                        param_txt = ntext(params.named_children[0], 40)
                    else:
                        param_txt = "_"
                    receiver_txt = ntext(receiver, 60)
                    if SECRET_NAME.search(param_txt):
                        receiver_txt = sanitize_string(
                            ntext(receiver, 10000), secret_hint=True)
                    rn = make(f"FOR {param_txt} in {receiver_txt}", is_structure=True)
                    if rn is None:
                        return []
                    body = cb.child_by_field_name("body")
                    render_body = _unwrap_parens(body)
                    if render_body is not None and render_body.type == "statement_block":
                        returns = _jsx_returns(render_body)
                        all_returns = _return_nodes(render_body)
                        if returns:
                            _, render_body = returns[-1]
                            if (len(all_returns) > 1
                                    or any(ret.parent != body for ret, _ in returns)):
                                rn.risk = "render-control-flow"
                        else:
                            render_body = None
                    rn.children = (_lower_expr(render_body, ntext, counter,
                                               events_out, element_name)
                                   if render_body is not None else [])
                    return [rn]
    elif n.type in ("arrow_function", "function_expression"):
        rn = make("{" + ntext(n, 60) + "}", risk="render-prop")
        return [rn] if rn is not None else []
    rn = make("{" + ntext(n, 80) + "}")
    return [rn] if rn is not None else []


def _event_action(value_node, setters, ntext):
    expr = value_node
    if expr.type == "jsx_expression":
        expr = expr.named_children[0] if expr.named_children else None
    expr = _unwrap_parens(expr)
    if expr is None:
        return ""
    if expr.type in ("arrow_function", "function_expression"):
        body = expr.child_by_field_name("body")
        body = _unwrap_parens(body)
        if body is not None and body.type == "statement_block":
            stmts = body.named_children
            if (len(stmts) == 1 and stmts[0].type == "expression_statement"
                    and stmts[0].named_children
                    and stmts[0].named_children[0].type == "call_expression"):
                body = stmts[0].named_children[0]
            else:
                body = None
        if body is not None and body.type == "call_expression":
            callee = body.child_by_field_name("function")
            callee_txt = ntext(callee, 60) if callee is not None else ""
            if callee_txt in setters:
                args = body.child_by_field_name("arguments")
                arg = args.named_children[0] if args is not None and args.named_children else None
                state = setters[callee_txt]
                if arg is None:
                    arg_txt = "undefined"
                elif SECRET_NAME.search(state):
                    arg_txt = sanitize_string(ntext(arg, 10000), secret_hint=True)
                else:
                    arg_txt = ntext(arg, 60)
                return f"set {state}={arg_txt}"
            return callee_txt
        return ntext(expr, 80)
    return ntext(expr, 60)


def _extract_events(react, ntext):
    setters = react.get("setters", {})
    for element, attr, value, secret in react.pop("_raw_events", []):
        action = (sanitize_string(ntext(value, 10000), secret_hint=True)
                  if secret else _event_action(value, setters, ntext))
        react["events"].append(EventUse(f"{element}.{attr}", action,
                                        value.start_point[0] + 1))


def _secret_fragments(node, setters, ntext) -> list[str]:
    """Collect syntax-selected secret values that can reappear in raw RET ops."""
    selected = []

    def add(value):
        if value is None:
            return
        selected.append(value)
        # A capped flow detail can contain only part of a compound expression;
        # retain literal descendants as additional safe replacement units.
        stack = list(value.named_children)
        while stack:
            child = stack.pop()
            if child.type in {"string", "template_string"}:
                selected.append(child)
                continue
            stack.extend(child.named_children)

    stack = [node]
    while stack:
        current = stack.pop()
        if current.type == "jsx_attribute":
            name = current.named_children[0] if current.named_children else None
            value = current.named_children[1] if len(current.named_children) > 1 else None
            if name is not None and SECRET_NAME.search(ntext(name, 60)):
                add(value)
        elif current.type == "variable_declarator":
            name = current.child_by_field_name("name")
            value = current.child_by_field_name("value")
            if name is not None and SECRET_NAME.search(ntext(name, 100)):
                add(value)
        elif current.type in {"assignment_expression", "assignment_pattern",
                              "object_assignment_pattern"}:
            left = current.child_by_field_name("left")
            right = current.child_by_field_name("right")
            if left is not None and SECRET_NAME.search(ntext(left, 100)):
                add(right)
        elif current.type == "call_expression":
            callee = current.child_by_field_name("function")
            callee_txt = ntext(callee, 80) if callee is not None else ""
            state = setters.get(callee_txt)
            if state and SECRET_NAME.search(state):
                arguments = current.child_by_field_name("arguments")
                if arguments is not None:
                    for argument in arguments.named_children:
                        add(argument)
            if callee is not None and callee.type == "member_expression":
                prop = callee.child_by_field_name("property")
                if prop is not None and ntext(prop, 20) == "map":
                    arguments = current.child_by_field_name("arguments")
                    callback = _function_argument(arguments)
                    if callback is not None:
                        params = (callback.child_by_field_name("parameter")
                                  or callback.child_by_field_name("parameters"))
                        if params is not None and SECRET_NAME.search(ntext(params, 100)):
                            add(callee.child_by_field_name("object"))
        stack.extend(current.named_children)

    fragments = []
    for value in selected:
        raw = one_line_text(_raw_text(value))
        if raw and raw not in fragments:
            fragments.append(raw)
    return sorted(fragments, key=lambda value: (-len(value), value))


def _scrub_text(text: str, fragments=()) -> str:
    for raw in fragments:
        if raw in text:
            text = text.replace(raw, sanitize_string(raw, secret_hint=True))
    return _scrub_named_secrets(text)


def _scrub_flow_op(op, fragments=()):
    detail = op.detail
    if op.binds and SECRET_NAME.search(op.binds):
        detail = sanitize_string(detail, secret_hint=True)
    else:
        detail = _scrub_text(detail, fragments)
    return replace(op, detail=detail)


def _extract_render(node, react, ntext, events_out):
    body = node.child_by_field_name("body")
    if body is None:
        return
    counter = {"n": 0, "dropped": False}
    jsx_root = None
    if body.type != "statement_block":
        jsx_root = _unwrap_parens(body)
        if not _jsx_bearing(jsx_root):
            jsx_root = None
    else:
        returns = _jsx_returns(body)
        if returns:
            jsx_root = returns[-1][1]  # textually last JSX-bearing return wins
            all_returns = _return_nodes(body)
            if (len(all_returns) > 1
                    or any(ret.parent != body for ret, _ in returns)):
                _append_fault(react["faults"], "render-control-flow",
                              returns[0][0].start_point[0] + 1)
    if jsx_root is None:
        return
    if jsx_root.type in JSX_TYPES:
        react["render"] = _lower_jsx(jsx_root, ntext, counter, events_out)
    else:
        # ternary / `&&` return: _lower_expr lowers both shapes to IF/ELSE nodes
        react["render"] = _lower_expr(jsx_root, ntext, counter, events_out, "")
    if counter.get("dropped"):
        react["render"].append(RenderNode(tag="…", risk="render-truncated",
                                          line=jsx_root.start_point[0] + 1))


def _collect_faults(nodes, faults):
    for n in nodes:
        if n.risk:
            faults.append(f"{n.risk}(L{n.line})")
        _collect_faults(n.children, faults)


def _render_lines(nodes, level, depth, faults, lines):
    for n in nodes:
        keep = level >= 3 or n.is_component or n.is_structure
        if not keep:
            if n.risk:
                faults.append(f"{n.risk}(L{n.line})")
            _render_lines(n.children, level, depth, faults, lines)
            continue
        piece = n.tag
        if level >= 3 and n.attrs:
            piece += f" {n.attrs}"
        if n.risk:
            piece += f" !FAULT({n.risk})"
            faults.append(f"{n.risk}(L{n.line})")
        kept_children = _kept(n.children, level)
        if (level == 2 and n.is_structure and len(kept_children) == 1
                and not _kept(kept_children[0].children, level)):
            child = kept_children[0]
            piece += f" > {child.tag}"
            if child.risk:
                piece += f" !FAULT({child.risk})"
                faults.append(f"{child.risk}(L{child.line})")
            # The inlined child's descendants are dropped from the tree, but
            # their risks must still reach the FAULT-BEFORE footer.
            _collect_faults(child.children, faults)
            lines.append("  " * depth + piece)
            continue
        lines.append("  " * depth + piece)
        _render_lines(n.children, level, depth + 1, faults, lines)


def _kept(nodes, level):
    out = []
    for n in nodes:
        if level >= 3 or n.is_component or n.is_structure:
            out.append(n)
        else:
            out.extend(_kept(n.children, level))
    return out


def _hook_call_op(op, aliases=None, shadowed=None) -> bool:
    """True for CALL flow ops whose callee is a hook: already surfaced as
    STATE/CTX/REF/HOOK/EFFECT lines, so they must not duplicate as flow ops."""
    if op.op != "CALL":
        return False
    match = re.match(
        r"^\s*([A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$]*)*)",
        op.detail,
    )
    if match is None:
        return False
    callee = re.sub(r"\s+", "", match.group(1))
    _, _, is_hook = _hook_info(callee, aliases or {}, shadowed or ())
    return is_hook


def _react_risk_entries(react, symbol=None) -> list[tuple[str, int]]:
    entries = list(react.get("faults", []))
    for hook in react.get("hooks", []):
        if hook.risk:
            entries.append((hook.risk, hook.line))
        if hook.inherited_risk:
            entries.append((hook.inherited_risk, hook.line))
        for op in hook.flow:
            if op.risk:
                entries.append((op.risk, op.line))
    if symbol is not None:
        for op in symbol.flow:
            if op.risk:
                entries.append((op.risk, op.line))
    stack = list(react.get("render", []))
    while stack:
        node = stack.pop()
        if node.risk:
            entries.append((node.risk, node.line))
        stack.extend(node.children)
    return list(dict.fromkeys(entries))


def component_lines(s, level, tier, faults):
    handle = s.semantic8 or s.slice8
    head = f"COMPONENT {s.name}({s.signature}) @L{s.span[0]}-{s.span[1]} ^{handle} ~{tier}"
    r = s.react
    if level == 1:
        head_risks = set()
        for risk, line in _react_risk_entries(r, s):
            if risk not in head_risks:
                head += f" !FAULT({risk})"
                head_risks.add(risk)
            faults.append(f"{risk}(L{line})")
        return [head]
    lines = [head]
    secret_fragments = r.get("secret_fragments", ())
    if r.get("wrapper"):
        lines.append(f"  WRAP {r['wrapper']}")
    if r.get("props"):
        lines.append("  PROPS " + ", ".join(r["props"]))
    for h in r.get("hooks", []):
        hook_risks = [risk for risk in (h.risk, h.inherited_risk) if risk]
        tag = "".join(f" !FAULT({risk})" for risk in dict.fromkeys(hook_risks))
        for risk in dict.fromkeys(hook_risks):
            faults.append(f"{risk}(L{h.line})")
        lines.append(f"  {h.kind} {h.detail}{tag}")
        if h.kind == "EFFECT" and h.flow:
            effect_ops = [_scrub_flow_op(op, secret_fragments) for op in h.flow]
            visible_effect_ops = (effect_ops if level >= 3
                                  else [op for op in effect_ops if op.risk])
            if visible_effect_ops:
                lines.extend(flow_lines(s, level, tier, faults, ops=visible_effect_ops))
    for e in r.get("events", []):
        lines.append(f"  EVENT {e.target} -> {_scrub_text(e.action, secret_fragments)}")
    effects = _render_provenanced(s.effects, s.unknown_calls)
    if effects:
        lines.append("  EFFECTS " + effects)
    if level >= 2 and s.flow:
        body_ops = [_scrub_flow_op(op, secret_fragments)
                    for op in s.flow
                    if not _hook_call_op(
                        op, r.get("hook_aliases"), r.get("shadowed_hook_aliases"))]
        visible_body_ops = (body_ops if level >= 3
                            else [op for op in body_ops if op.risk])
        if visible_body_ops:
            lines.extend(flow_lines(s, level, tier, faults, ops=visible_body_ops))
    for risk, line in r.get("faults", []):
        lines.append(f"  !FAULT({risk}) @L{line}")
        faults.append(f"{risk}(L{line})")
    if r.get("render"):
        lines.append("  RENDER")
        _render_lines(r["render"], level, 2, faults, lines)
    return lines


def _inside_class(node) -> bool:
    parent = node.parent
    while parent is not None:
        if parent.type in {"class", "class_declaration", "abstract_class_declaration"}:
            return True
        parent = parent.parent
    return False


def lower_components(fn_nodes, ntext, flow_fn) -> bool:
    upgraded = False
    hook_aliases = _react_hook_aliases(fn_nodes, ntext)
    for sym, node in fn_nodes:
        short = sym.name.split(".")[-1]
        if (node.type not in COMPONENT_FN_TYPES or _inside_class(node)
                or not COMPONENT_NAME_RE.match(short)
                or not _returns_jsx(node)):
            continue
        sym.kind = "component"
        shadowed_hook_aliases = _shadowed_hook_aliases(node, hook_aliases)
        sym.react = {
            "wrapper": sym.decorators[0] if sym.decorators else "",
            "props": _extract_props(node, ntext),
            "hooks": [],
            "events": [],
            "render": [],
            "faults": [],
            "hook_aliases": hook_aliases,
            "shadowed_hook_aliases": tuple(sorted(shadowed_hook_aliases)),
        }
        body = node.child_by_field_name("body")
        if body is not None and body.type == "statement_block":
            _extract_hooks(body, sym.react, ntext, flow_fn,
                           hook_aliases, shadowed_hook_aliases)
        else:
            sym.react["setters"] = {}
        raw_events = []
        _extract_render(node, sym.react, ntext, raw_events)
        sym.react["_raw_events"] = raw_events
        _extract_events(sym.react, ntext)
        sym.react["secret_fragments"] = _secret_fragments(
            node, sym.react.get("setters", {}), ntext)
        upgraded = True
    return upgraded
