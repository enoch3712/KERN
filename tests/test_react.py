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


if __name__ == "__main__":
    unittest.main()
