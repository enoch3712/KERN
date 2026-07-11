import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "kern" / "scripts"))
import kern_cache  # noqa: E402
import kern_compile  # noqa: E402

SRC = '''import json


def load_entry(path, expected_sha):
    data = path.read_bytes()
    if not data:
        raise ValueError(path)
    return json.loads(data)
'''


class TestVerify(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.file = self.root / "mod.py"
        self.file.write_text(SRC)
        self.paths, self.config = kern_cache.initialize(self.root)
        mod = kern_compile.parse_python(SRC)
        self.sym = next(s for s in mod.symbols if s.name == "load_entry")

    def verify(self, expected_hash, span=None):
        rel, src = kern_cache.normalize_rel(self.root, "mod.py")
        return kern_cache.verify_symbol(self.root, self.paths, rel, src,
                                        "load_entry", expected_hash, span)

    def test_ok(self):
        r = self.verify(self.sym.slice8, f"L{self.sym.span[0]}-{self.sym.span[1]}")
        self.assertEqual(r["result"], "ok")

    def test_moved_when_file_shifts(self):
        self.file.write_text("# new comment line\n" + SRC)
        r = self.verify(self.sym.slice8, f"L{self.sym.span[0]}-{self.sym.span[1]}")
        self.assertEqual(r["result"], "moved")
        self.assertIn("current_span", r)

    def test_stale_when_body_changes(self):
        self.file.write_text(SRC.replace("json.loads(data)", "json.loads(data.strip())"))
        r = self.verify(self.sym.slice8)
        self.assertEqual(r["result"], "stale")
        self.assertEqual(r["reason"], "symbol-bytes-changed")

    def test_stale_when_symbol_deleted(self):
        self.file.write_text("import json\n")
        r = self.verify(self.sym.slice8)
        self.assertEqual(r["result"], "stale")
        self.assertEqual(r["reason"], "symbol-not-found")

    def test_unsupported_suffix_raises(self):
        (self.root / "x.rb").write_text("def x; end\n" * 200)
        rel, src = kern_cache.normalize_rel(self.root, "x.rb")
        with self.assertRaises(ValueError):
            kern_cache.verify_symbol(self.root, self.paths, rel, src, "x", "deadbeef", None)


if __name__ == "__main__":
    unittest.main()
