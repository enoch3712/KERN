import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "kern" / "scripts"))
import kern_cache  # noqa: E402

BIG_PY = '"""Doc."""\n\nimport json\n\n' + "\n\n".join(
    f'def fn_{i}(path, n):\n'
    f'    """Doc {i}."""\n'
    f'    data = path.read_bytes()\n'
    f'    if not data:\n'
    f'        raise ValueError(n)\n'
    f'    return json.loads(data)\n'
    for i in range(30)
)


class TestCacheIntegration(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "big.py").write_text(BIG_PY)
        (self.root / "tiny.py").write_text("X = 1\n")
        self.paths, self.config = kern_cache.initialize(self.root)

    def ensure(self, name, tier=None):
        rel, src = kern_cache.normalize_rel(self.root, name)
        return kern_cache.ensure_file(self.root, self.paths, rel, src, self.config, tier=tier)

    def test_codec_is_0_2(self):
        self.assertEqual(kern_cache.CODEC_VERSION, "kern-il/0.2")

    def test_big_python_file_gets_deterministic_il(self):
        result = self.ensure("big.py")
        il = Path(result["ir"]).read_text()
        self.assertTrue(il.startswith("KERN-IL/0.2"))
        self.assertIn("tier=L2", il)
        self.assertIn("F fn_0(", il)
        self.assertIn("EFFECTS fs:read", il)
        self.assertIn("RAISES ValueError", il)

    def test_tier_override(self):
        result = self.ensure("big.py", tier="L1")
        il = Path(result["ir"]).read_text()
        self.assertIn("tier=L1", il)
        self.assertNotIn("    IF", il)
        manifest = json.loads((self.paths["manifest"]).read_text())
        self.assertEqual(manifest["files"]["big.py"]["ir_tier"], "L1")

    def test_tiny_file_gets_source_cheaper_stub(self):
        result = self.ensure("tiny.py")
        il = Path(result["ir"]).read_text()
        self.assertIn("mode=source-cheaper", il)
        self.assertNotIn("F ", il)

    def test_syntax_error_falls_back_to_generic(self):
        (self.root / "broken.py").write_text("def broken(:\n    pass\n" * 300)
        il = Path(self.ensure("broken.py")["ir"]).read_text()
        self.assertIn("mode=generic-line-baseline", il)

    def test_repo_revision_header_present(self):
        il = Path(self.ensure("big.py")["ir"]).read_text()
        self.assertIn("repo_revision=", il)

    def test_generic_ir_line_numbers_use_newline_math(self):
        # A form feed (\x0c) is a line boundary for str.splitlines() but not for
        # a plain "\n" split; generic_ir's line numbers must match the latter
        # (the same \n-only math fault_source uses) or "N|" refs point at the
        # wrong source line whenever a file contains such control characters.
        content = (
            "#!/bin/sh\n# \x0c\n"
            + ("echo filler\n" * 400)
            + "if true; then\n  echo a\nfi\n"
        )
        (self.root / "notpython.sh").write_text(content)
        il = Path(self.ensure("notpython.sh")["ir"]).read_text()
        self.assertIn("mode=generic-line-baseline", il)
        line_refs = [l for l in il.splitlines() if l and l[0].isdigit()]
        ref = next(l for l in line_refs if "if true" in l)
        n = int(ref.split("|")[0])
        raw_lines = (self.root / "notpython.sh").read_text().split("\n")
        self.assertIn("if true", raw_lines[n - 1])


if __name__ == "__main__":
    unittest.main()
