import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "kern" / "scripts"))
import kern_compile  # noqa: E402

SAMPLE = '''"""Module docstring."""

import json
from hashlib import sha256
from pathlib import Path

MANIFEST_NAME = "manifest.json"
API_KEY = "s2_abcdefghijklmnop1234"


class StaleSource(Exception):
    """Raised on hash mismatch."""


def load_entry(path: Path, expected_sha: str) -> dict:
    """Read, verify, parse."""
    data = path.read_bytes()
    current_sha = sha256(data).hexdigest()
    if current_sha != expected_sha:
        raise StaleSource(path)
    return json.loads(data)


class Loader:
    async def fetch(self, url):
        return await self.client.get(url)
'''


class TestPythonFrontend(unittest.TestCase):
    def setUp(self):
        self.mod = kern_compile.parse_python(SAMPLE)

    def sym(self, name):
        return next(s for s in self.mod.symbols if s.name == name)

    def test_module_metadata(self):
        self.assertEqual(self.mod.lang, "python")
        self.assertEqual(self.mod.frontend, "pyast")
        self.assertEqual(self.mod.parse_error, "")

    def test_function_symbol(self):
        f = self.sym("load_entry")
        self.assertEqual(f.kind, "function")
        self.assertIn("path: Path", f.signature)
        self.assertIn("expected_sha: str", f.signature)
        self.assertEqual(f.returns, "dict")
        self.assertFalse(f.is_async)
        self.assertIn("path.read_bytes", f.calls)
        self.assertIn("json.loads", f.calls)
        self.assertEqual(f.raises, ["StaleSource"])

    def test_slice_hash_matches_exact_source_lines(self):
        f = self.sym("load_entry")
        start, end = f.span
        lines = SAMPLE.splitlines(keepends=True)
        expected = hashlib.sha256("".join(lines[start - 1:end]).encode()).hexdigest()[:8]
        self.assertEqual(f.slice8, expected)
        self.assertEqual(SAMPLE.splitlines()[start - 1].strip(), "def load_entry(path: Path, expected_sha: str) -> dict:")

    def test_method_is_qualified_and_async(self):
        m = self.sym("Loader.fetch")
        self.assertTrue(m.is_async)

    def test_class_symbol(self):
        c = self.sym("StaleSource")
        self.assertEqual(c.kind, "class")
        self.assertEqual(c.bases, "Exception")
        self.assertEqual(len(c.slice8), 8)

    def test_secret_const_redacted(self):
        consts = [s for s in self.mod.symbols if s.kind == "const"]
        api = next(s for s in consts if s.name == "API_KEY")
        self.assertNotIn("s2_abcdefghijklmnop1234", api.detail)
        self.assertIn("REDACTED", api.detail)

    def test_imports_collected(self):
        imports = [s for s in self.mod.symbols if s.kind == "import"]
        details = " ".join(s.detail for s in imports)
        self.assertIn("json", details)
        self.assertIn("sha256", details)

    def test_omit_counts(self):
        self.assertGreaterEqual(self.mod.omit["docstrings"], 3)
        self.assertGreaterEqual(self.mod.omit["blank"], 5)

    def test_parse_error_reported(self):
        bad = kern_compile.parse_python("def broken(:\n")
        self.assertNotEqual(bad.parse_error, "")
        self.assertEqual(bad.symbols, [])

    def test_decorated_class_span_includes_decorator(self):
        src = "import functools\n\n@functools.total_ordering\nclass Ordered:\n    pass\n"
        mod = kern_compile.parse_python(src)
        c = next(s for s in mod.symbols if s.name == "Ordered")
        self.assertEqual(c.span[0], 3)
        self.assertEqual(c.decorators, ["functools.total_ordering"])


if __name__ == "__main__":
    unittest.main()
