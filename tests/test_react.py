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


if __name__ == "__main__":
    unittest.main()
