import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "kern" / "scripts"))
import kern_compile  # noqa: E402

TSX_SAMPLE = '''
import { useState, useEffect } from "react";
import { Card, Avatar, UserDetails } from "./ui";

export function UserCard({ user, onClose = noop }) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    analytics.track("view_user", user.id);
  }, [user.id]);

  return (
    <Card onClick={() => setOpen(true)}>
      <Avatar src={user.avatar} />
      <span>{user.name}</span>
      {open && <UserDetails user={user} />}
    </Card>
  );
}

export function formatName(name: string): string {
  return name.trim();
}
'''


@unittest.skipUnless(kern_compile.tsjs_available(), "tree-sitter not installed")
class TestDialectRouting(unittest.TestCase):
    def test_tsx_dialect_parses_jsx_clean(self):
        mod = kern_compile.parse_tsjs(TSX_SAMPLE, dialect="tsx")
        self.assertEqual(mod.parse_error, "")
        self.assertEqual(mod.lang, "tsx")

    def test_plain_ts_dialect_chokes_on_jsx(self):
        # Documents the routed-around limitation: TS grammar has no JSX.
        mod = kern_compile.parse_tsjs(TSX_SAMPLE, dialect="ts")
        self.assertNotEqual(mod.parse_error, "")

    def test_js_dialect_still_default(self):
        mod = kern_compile.parse_tsjs("function f() { return 1; }\n")
        self.assertEqual(mod.lang, "javascript")


@unittest.skipUnless(kern_compile.tsjs_available(), "tree-sitter not installed")
class TestComponentDetection(unittest.TestCase):
    def parse(self, src, dialect="tsx"):
        return kern_compile.parse_tsjs(src, dialect=dialect)

    def sym(self, mod, name):
        return next(s for s in mod.symbols if s.name == name)

    def test_capitalized_jsx_function_is_component(self):
        mod = self.parse(TSX_SAMPLE)
        s = self.sym(mod, "UserCard")
        self.assertEqual(s.kind, "component")
        self.assertEqual(mod.frontend, "tree-sitter+react")

    def test_props_extracted(self):
        mod = self.parse(TSX_SAMPLE)
        self.assertEqual(self.sym(mod, "UserCard").react["props"],
                         ["user", "onClose=noop"])

    def test_capitalized_without_jsx_stays_function(self):
        mod = self.parse("export function Parse(x) { return x + 1; }\n")
        self.assertEqual(self.sym(mod, "Parse").kind, "function")

    def test_lowercase_with_jsx_stays_function(self):
        mod = self.parse("function helper() { return <div />; }\n")
        self.assertEqual(self.sym(mod, "helper").kind, "function")

    def test_arrow_component_detected(self):
        mod = self.parse("export const Badge = ({ label }) => <b>{label}</b>;\n")
        self.assertEqual(self.sym(mod, "Badge").kind, "component")

    def test_memo_wrapper_unwrapped(self):
        mod = self.parse("import { memo } from 'react';\n"
                         "const Row = memo(({ id }) => <li>{id}</li>);\n")
        s = self.sym(mod, "Row")
        self.assertEqual(s.kind, "component")
        self.assertEqual(s.react["wrapper"], "memo")

    def test_memo_with_comparator_identifier_not_hijacked(self):
        src = ("const Row = ({ id }) => <li>{id}</li>;\n"
               "const MemoRow = memo(Row, (a, b) => a.id === b.id);\n")
        mod = self.parse(src)
        memo_syms = [s for s in mod.symbols if s.name == "MemoRow"]
        self.assertEqual(len(memo_syms), 1)
        self.assertEqual(memo_syms[0].kind, "const")   # falls to const fallback

    def test_memo_inline_with_comparator_uses_first_arg(self):
        src = "const Row = memo(({ id }) => <li>{id}</li>, (a, b) => a.id === b.id);\n"
        mod = self.parse(src)
        s = self.sym(mod, "Row")
        self.assertEqual(s.kind, "component")
        self.assertEqual(s.react["wrapper"], "memo")
        self.assertEqual(s.react["props"], ["id"])

    def test_non_react_file_untouched(self):
        mod = self.parse("export function parse(raw) { return Number(raw); }\n",
                         dialect="js")
        self.assertEqual(mod.frontend, "tree-sitter")
        self.assertEqual(self.sym(mod, "parse").kind, "function")

    def test_ternary_return_is_component(self):
        mod = self.parse("function Card({ loading }) {\n"
                         "  return loading ? <Spinner /> : <Content />;\n}\n")
        self.assertEqual(self.sym(mod, "Card").kind, "component")

    def test_logical_and_return_is_component(self):
        mod = self.parse("const Hint = ({ ok }) => ok && <b>hi</b>;\n")
        self.assertEqual(self.sym(mod, "Hint").kind, "component")

    def test_nested_return_jsx_in_inner_fn_not_component(self):
        src = ("function Outer() {\n"
               "  const inner = () => <div />;\n"
               "  return 42;\n"
               "}\n")
        mod = self.parse(src)
        self.assertEqual(self.sym(mod, "Outer").kind, "function")


@unittest.skipUnless(kern_compile.tsjs_available(), "tree-sitter not installed")
class TestHooks(unittest.TestCase):
    def component(self, src):
        mod = kern_compile.parse_tsjs(src, dialect="tsx")
        return next(s for s in mod.symbols if s.kind == "component")

    def hooks(self, src):
        return self.component(src).react["hooks"]

    def test_usestate_setter_and_init(self):
        s = self.component(TSX_SAMPLE)
        h = [x for x in s.react["hooks"] if x.kind == "STATE"]
        self.assertEqual(h[0].detail, "open=false")
        self.assertEqual(s.react["setters"], {"setOpen": "open"})

    def test_effect_deps_verbatim(self):
        h = [x for x in self.hooks(TSX_SAMPLE) if x.kind == "EFFECT"]
        self.assertEqual(h[0].detail, "deps=[user.id]")
        self.assertTrue(h[0].flow)  # body captured for L3

    def test_effect_missing_deps(self):
        src = ("function T() {\n  useEffect(() => { tick(); });\n"
               "  return <div />;\n}\n")
        h = [x for x in self.hooks(src) if x.kind == "EFFECT"]
        self.assertEqual(h[0].detail, "deps=EVERY-RENDER")

    def test_reducer_context_ref_custom(self):
        src = ("function T() {\n"
               "  const [state, dispatch] = useReducer(reducer, init);\n"
               "  const theme = useContext(ThemeContext);\n"
               "  const inputRef = useRef(null);\n"
               "  const data = useUserData(id);\n"
               "  return <div />;\n}\n")
        kinds = [(h.kind, h.detail) for h in self.hooks(src)]
        self.assertEqual(kinds, [
            ("STATE", "[state, dispatch]=useReducer(reducer, init)"),
            ("CTX", "theme=useContext(ThemeContext)"),
            ("REF", "inputRef"),
            ("HOOK", "data=useUserData(id)"),
        ])

    def test_aliased_hook_faulted(self):
        src = ("import * as R from 'react';\n"
               "function T() {\n  const [a, setA] = R.useState(0);\n"
               "  return <div />;\n}\n")
        h = self.hooks(src)[0]
        self.assertEqual(h.risk, "aliased-hook")

    def test_conditional_hook_faulted(self):
        src = ("function T({ on }) {\n"
               "  if (on) { useEffect(() => {}); }\n"
               "  return <div />;\n}\n")
        faults = self.component(src).react["faults"]
        self.assertIn("conditional-hook", [f[0] for f in faults])

    def test_logical_and_guarded_hook_faulted(self):
        src = ("function T({ on }) {\n"
               "  on && useEffect(() => {});\n"
               "  return <div />;\n}\n")
        faults = self.component(src).react["faults"]
        self.assertIn("conditional-hook", [f[0] for f in faults])

    def test_ternary_guarded_hook_faulted(self):
        src = ("function T({ on }) {\n"
               "  on ? useState(0) : null;\n"
               "  return <div />;\n}\n")
        faults = self.component(src).react["faults"]
        self.assertIn("conditional-hook", [f[0] for f in faults])

    def test_plain_toplevel_call_not_faulted(self):
        src = ("function T() {\n"
               "  analytics.track(useContextValue());\n"
               "  return <div />;\n}\n")
        faults = self.component(src).react["faults"]
        self.assertEqual(faults, [])


@unittest.skipUnless(kern_compile.tsjs_available(), "tree-sitter not installed")
class TestRenderTree(unittest.TestCase):
    def render(self, src):
        mod = kern_compile.parse_tsjs(src, dialect="tsx")
        comp = next(s for s in mod.symbols if s.kind == "component")
        return comp.react["render"]

    def flat(self, nodes, depth=0):
        out = []
        for n in nodes:
            out.append((depth, n.tag, n.risk))
            out.extend(self.flat(n.children, depth + 1))
        return out

    def test_hierarchy_and_conditional(self):
        tags = self.flat(self.render(TSX_SAMPLE))
        self.assertIn((0, "Card", ""), tags)
        self.assertIn((1, "Avatar", ""), tags)
        self.assertIn((1, "span", ""), tags)
        self.assertIn((1, "IF open", ""), tags)
        self.assertIn((2, "UserDetails", ""), tags)

    def test_component_flag_and_attrs(self):
        nodes = self.render(TSX_SAMPLE)
        card = nodes[0]
        self.assertTrue(card.is_component)
        avatar = next(c for c in card.children if c.tag == "Avatar")
        self.assertEqual(avatar.attrs, "src={user.avatar}")

    def test_map_becomes_for(self):
        src = ("function L({ items }) {\n"
               "  return <ul>{items.map(item => <Row key={item.id} />)}</ul>;\n}\n")
        tags = [t for _, t, _ in self.flat(self.render(src))]
        self.assertIn("FOR item in items", tags)
        self.assertIn("Row", tags)

    def test_ternary_if_else(self):
        src = ("function T({ ok }) {\n"
               "  return <div>{ok ? <Yes /> : <No />}</div>;\n}\n")
        tags = [t for _, t, _ in self.flat(self.render(src))]
        self.assertIn("IF ok", tags)
        self.assertIn("ELSE", tags)

    def test_ternary_return_lowered_to_if_else(self):
        src = ("function Card({ loading }) {\n"
               "  return loading ? <Spinner /> : <Content />;\n}\n")
        tags = [t for _, t, _ in self.flat(self.render(src))]
        self.assertIn("IF loading", tags)
        self.assertIn("ELSE", tags)
        self.assertIn("Spinner", tags)
        self.assertIn("Content", tags)

    def test_logical_and_return_lowered_to_if(self):
        src = "const Hint = ({ ok }) => ok && <b>hi</b>;\n"
        tags = [t for _, t, _ in self.flat(self.render(src))]
        self.assertIn("IF ok", tags)
        self.assertIn("b", tags)

    def test_dynamic_component_faulted(self):
        src = ("function T() {\n  return <Foo.Bar />;\n}\n")
        flat = self.flat(self.render(src))
        self.assertIn("dynamic-component", [r for _, _, r in flat])

    def test_spread_sole_prop_source_faulted(self):
        src = ("function T({ rest }) {\n  return <Input {...rest} />;\n}\n")
        node = self.render(src)[0]
        self.assertEqual(node.risk, "spread-props")
        self.assertIn("...rest", node.attrs)

    def test_spread_with_named_attrs_not_faulted(self):
        src = ("function T({ rest }) {\n  return <Input id=\"x\" {...rest} />;\n}\n")
        self.assertEqual(self.render(src)[0].risk, "")

    def test_render_prop_faulted(self):
        src = ("function T() {\n"
               "  return <List>{item => <Row item={item} />}</List>;\n}\n")
        flat = self.flat(self.render(src))
        self.assertIn("render-prop", [r for _, _, r in flat])

    def test_fragment(self):
        src = "function T() {\n  return <><A /><B /></>;\n}\n"
        tags = [t for _, t, _ in self.flat(self.render(src))]
        self.assertIn("A", tags)
        self.assertIn("B", tags)

    def test_guard_clause_last_return_wins(self):
        src = ("function Card({ loading }) {\n"
               "  if (loading) { return <Spinner />; }\n"
               "  return <Content />;\n}\n")
        tags = [t for _, t, _ in self.flat(self.render(src))]
        self.assertEqual(tags, ["Content"])

    def test_two_guards_last_return_wins(self):
        src = ("function T({ a, b }) {\n"
               "  if (a) return <A />;\n"
               "  if (b) return <B />;\n"
               "  return <C />;\n}\n")
        tags = [t for _, t, _ in self.flat(self.render(src))]
        self.assertEqual(tags, ["C"])

    def test_exact_budget_no_false_truncation(self):
        # 1 (outer div) + 199 self-closing <i/> children == RENDER_BUDGET (200)
        # exactly, with nothing dropped -- must NOT be flagged as truncated.
        children = "<i/>" * 199
        src = f"function T() {{\n  return <div>{children}</div>;\n}}\n"
        risks = [r for _, _, r in self.flat(self.render(src))]
        self.assertNotIn("render-truncated", risks)

    def test_overflow_truncation_faulted(self):
        children = "".join(f"<i>{{x{i}}}</i>" for i in range(120))
        src = f"function T() {{\n  return <div>{children}</div>;\n}}\n"
        risks = [r for _, _, r in self.flat(self.render(src))]
        self.assertIn("render-truncated", risks)

    def test_dynamic_component_with_spread_keeps_both_faults(self):
        src = "function T({ p }) {\n  return <Menu.Item {...p} />;\n}\n"
        risks = [r for _, _, r in self.flat(self.render(src))]
        self.assertIn("dynamic-component+spread-props", risks)


@unittest.skipUnless(kern_compile.tsjs_available(), "tree-sitter not installed")
class TestEvents(unittest.TestCase):
    def events(self, src):
        mod = kern_compile.parse_tsjs(src, dialect="tsx")
        comp = next(s for s in mod.symbols if s.kind == "component")
        return comp.react["events"]

    def test_setter_arrow_lowered(self):
        ev = self.events(TSX_SAMPLE)
        self.assertEqual([(e.target, e.action) for e in ev],
                         [("Card.onClick", "set open=true")])

    def test_handler_reference(self):
        src = ("function T({ onSave }) {\n"
               "  return <button onClick={onSave}>go</button>;\n}\n")
        ev = self.events(src)
        self.assertEqual((ev[0].target, ev[0].action), ("button.onClick", "onSave"))

    def test_non_setter_arrow_uses_callee(self):
        src = ("function T() {\n"
               "  return <a onMouseEnter={() => analytics.track('x')} />;\n}\n")
        ev = self.events(src)
        self.assertEqual(ev[0].action, "analytics.track")

    def test_raw_events_cleaned_up(self):
        mod = kern_compile.parse_tsjs(TSX_SAMPLE, dialect="tsx")
        comp = next(s for s in mod.symbols if s.kind == "component")
        self.assertNotIn("_raw_events", comp.react)

    def test_guarded_setter_not_lowered_to_unconditional_set(self):
        src = ("function T() {\n"
               "  const [open, setOpen] = useState(false);\n"
               "  return <a onClick={() => { if (open) return; setOpen(true); }} />;\n}\n")
        ev = self.events(src)
        self.assertNotEqual(ev[0].action, "set open=true")
        self.assertIn("if", ev[0].action)   # guard remains visible in the fallback text

    def test_single_statement_setter_still_lowered(self):
        src = ("function T() {\n"
               "  const [open, setOpen] = useState(false);\n"
               "  return <a onClick={() => { setOpen(true); }} />;\n}\n")
        ev = self.events(src)
        self.assertEqual(ev[0].action, "set open=true")


@unittest.skipUnless(kern_compile.tsjs_available(), "tree-sitter not installed")
class TestEmit(unittest.TestCase):
    def il(self, tier, src=None):
        mod = kern_compile.parse_tsjs(src or TSX_SAMPLE, dialect="tsx")
        return kern_compile.emit_il(mod, "app/UserCard.tsx", "a" * 64, "none", tier)

    def test_l1_head_only(self):
        il = self.il("L1")
        self.assertIn("COMPONENT UserCard({ user, onClose = noop }) @L", il)
        self.assertNotIn("STATE", il)
        self.assertNotIn("RENDER", il)

    def test_l2_heads_and_collapsed_render(self):
        il = self.il("L2")
        self.assertIn("  PROPS user, onClose=noop", il)
        self.assertIn("  STATE open=false", il)
        self.assertIn("  EFFECT deps=[user.id]", il)
        self.assertIn("  EVENT Card.onClick -> set open=true", il)
        self.assertIn("  RENDER", il)
        self.assertIn("Card", il)
        self.assertIn("IF open > UserDetails", il)   # single kept child inlined
        self.assertNotIn("span", il)                  # host element collapsed at L2
        self.assertNotIn("src={user.avatar}", il)     # attrs dropped at L2

    def test_l3_full_render_and_effect_body(self):
        il = self.il("L3")
        self.assertIn("span", il)
        self.assertIn("src={user.avatar}", il)
        self.assertIn("CALL analytics.track", il)     # effect body flow

    def test_frontend_header_tag(self):
        self.assertIn("frontend=tree-sitter+react", self.il("L2"))

    def test_fault_footer(self):
        src = "function T() {\n  return <Foo.Bar />;\n}\n"
        il = self.il("L2", src)
        self.assertIn("!FAULT(dynamic-component)", il)
        self.assertIn("dynamic-component(L", il)      # FAULT-BEFORE footer

    def test_plain_function_in_same_file_unchanged(self):
        il = self.il("L2")
        self.assertIn("F formatName(name: string) -> string", il)

    def test_l2_collapsed_node_fault_reaches_footer(self):
        src = ("function T({ props }) {\n"
               "  return <div {...props}><span>hi</span></div>;\n}\n")
        il = self.il("L2", src)
        self.assertIn("spread-props(L", il)   # footer net holds at L2

    def test_l2_inline_when_child_has_only_host_children(self):
        src = ("function T({ open, user }) {\n"
               "  return <div>{open && <UserDetails user={user}><span>x</span></UserDetails>}</div>;\n}\n")
        il = self.il("L2", src)
        self.assertIn("IF open > UserDetails", il)

    def test_l3_component_body_flow_visible(self):
        src = ("function Card({ items, loading }) {\n"
               "  const total = computeTotal(items);\n"
               "  if (loading) { return <Spinner />; }\n"
               "  return <div>{total}</div>;\n}\n")
        il = self.il("L3", src)
        self.assertIn("computeTotal", il)
        self.assertIn("IF loading", il)
        self.assertIn("Spinner", il)   # guard render visible via RET flow op

    def test_l2_component_effects_line(self):
        # No TSJS call classifies into a named effect class today; the
        # unknown-calls counter still renders, so EFFECTS must appear.
        src = ("function T() {\n"
               "  useEffect(() => { fetch('/api'); }, []);\n"
               "  console.log('x');\n"
               "  return <div />;\n}\n")
        il = self.il("L2", src)
        self.assertIn("EFFECTS", il)
        self.assertIn("unknown-calls=", il)

    def test_l3_hook_calls_not_duplicated_as_flow_ops(self):
        # useState appears once as STATE, not again as a CALL flow op.
        src = ("function T() {\n"
               "  const [open, setOpen] = useState(false);\n"
               "  return <div />;\n}\n")
        il = self.il("L3", src)
        self.assertEqual(il.count("useState"), 0)   # STATE open=false covers it
        self.assertIn("STATE open=false", il)


@unittest.skipUnless(kern_compile.tsjs_available(), "tree-sitter not installed")
class TestNoOpOnPlainCode(unittest.TestCase):
    PLAIN = ('import { readFile } from "fs/promises";\n\n'
             "export async function load(url) {\n"
             "  const data = await readFile(url);\n"
             "  if (!data) {\n    throw new Error(url);\n  }\n"
             "  return data.toString();\n}\n")

    def test_component_kind_absent_and_frontend_plain(self):
        mod = kern_compile.parse_tsjs(self.PLAIN, dialect="js")
        self.assertEqual(mod.frontend, "tree-sitter")
        self.assertFalse(any(s.kind == "component" for s in mod.symbols))
        il = kern_compile.emit_il(mod, "src/load.js", "c" * 64, "none", "L2")
        self.assertNotIn("COMPONENT", il)
        self.assertIn("frontend=tree-sitter tier=L2", il)


if __name__ == "__main__":
    unittest.main()
