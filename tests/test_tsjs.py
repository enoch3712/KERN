import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "kern" / "scripts"))
import kern_compile  # noqa: E402

TS_SAMPLE = '''
import { readFile } from "fs/promises";

const MAX = 10;

export class Loader {
  async fetch(url: string): Promise<string> {
    const data = await readFile(url);
    if (!data) {
      throw new Error(url);
    }
    return data.toString();
  }
}

export function parse(raw: string): number {
  return Number(raw);
}
'''


class TestAvailability(unittest.TestCase):
    def test_available_returns_bool(self):
        self.assertIsInstance(kern_compile.tsjs_available(), bool)


@unittest.skipUnless(kern_compile.tsjs_available(), "tree-sitter not installed")
class TestTsFrontend(unittest.TestCase):
    def setUp(self):
        self.mod = kern_compile.parse_tsjs(TS_SAMPLE, typescript=True)

    def sym(self, name):
        return next(s for s in self.mod.symbols if s.name == name)

    def test_module_metadata(self):
        self.assertEqual(self.mod.lang, "typescript")
        self.assertEqual(self.mod.frontend, "tree-sitter")

    def test_function_and_method(self):
        f = self.sym("parse")
        self.assertEqual(f.kind, "function")
        self.assertIn("raw: string", f.signature)
        m = self.sym("Loader.fetch")
        self.assertTrue(m.is_async)
        self.assertIn("readFile", " ".join(m.calls))

    def test_flow_and_raises(self):
        m = self.sym("Loader.fetch")
        ops = [o.op for o in m.flow]
        self.assertIn("IF", ops)
        self.assertIn("RAISE", ops)
        self.assertIn("RET", ops)
        self.assertIn("Error", m.raises)

    def test_slice_hash_present(self):
        self.assertEqual(len(self.sym("parse").slice8), 8)

    def test_emit_works(self):
        il = kern_compile.emit_il(self.mod, "src/x.ts", "b" * 64, "none", "L2")
        self.assertTrue(il.startswith("KERN-IL/0.2"))
        self.assertIn("F parse", il)

    def test_broken_source_sets_parse_error(self):
        broken = "export function f( {\n  return 1;\n}\n"
        mod = kern_compile.parse_tsjs(broken, typescript=True)
        self.assertNotEqual(mod.parse_error, "")

    def test_clean_source_has_no_parse_error(self):
        self.assertEqual(self.mod.parse_error, "")


if __name__ == "__main__":
    unittest.main()
