import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "kern" / "scripts"))
import kern_cache  # noqa: E402

BIG_PY = '"""Doc."""\n\nimport json\n\n' + "\n\n".join(
    f'def fn_{i}(path):\n'
    f'    data = path.read_bytes()\n'
    f'    return json.loads(data)\n'
    for i in range(40)
)


class TestEnrichmentAppendOnly(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "mod.py").write_text(BIG_PY)
        self.paths, self.config = kern_cache.initialize(self.root)
        rel, src = kern_cache.normalize_rel(self.root, "mod.py")
        self.rel, self.src = rel, src
        ensured = kern_cache.ensure_file(self.root, self.paths, rel, src, self.config)
        self.digest = ensured["source_sha256"]
        self.baseline = Path(ensured["ir"]).read_text()

    def commit(self, staging_text):
        staging = self.root / "staging.kern-il.txt"
        staging.write_text(staging_text)
        return kern_cache.commit_file(self.root, self.paths, self.rel, self.src,
                                      staging, self.digest)

    def test_valid_append_accepted(self):
        staged = self.baseline + "\nENRICHMENT model=test-model\nINTENT fn_0: reads and parses a JSON file\n"
        result = self.commit(staged)
        self.assertEqual(result["status"], "ready")

    def test_replacement_rejected(self):
        rogue = self.baseline.replace("F fn_0(", "F totally_different(")
        with self.assertRaises(ValueError):
            self.commit(rogue + "\nENRICHMENT model=test-model\nINTENT fn_0: x\n")

    def test_missing_enrichment_header_rejected(self):
        with self.assertRaises(ValueError):
            self.commit(self.baseline + "\nINTENT fn_0: no header line\n")

    def test_non_intent_lines_rejected(self):
        staged = self.baseline + "\nENRICHMENT model=test-model\nF injected_fact() -> Any @L1-1 ^deadbeef ~L2\n"
        with self.assertRaises(ValueError):
            self.commit(staged)

    def test_newline_splice_rejected(self):
        spliced = self.baseline.rstrip("\n") + "ENRICHMENT model=evil\nINTENT fn_0: x\n"
        with self.assertRaises(ValueError):
            self.commit(spliced)

    def test_identical_baseline_accepted_as_noop(self):
        result = self.commit(self.baseline)
        self.assertEqual(result["status"], "ready")

    def test_two_step_splice_rejected(self):
        with self.assertRaises(ValueError):
            self.commit(self.baseline.rstrip("\n"))

    def test_committed_ir_always_ends_with_single_newline(self):
        staged = self.baseline + "\nENRICHMENT model=test-model\nINTENT fn_0: reads json\n\n\n"
        self.commit(staged)
        rel, _ = kern_cache.normalize_rel(self.root, "mod.py")
        ir_path = kern_cache.artifact_paths(self.paths, rel)["ir"]
        data = ir_path.read_bytes()
        self.assertTrue(data.endswith(b"\n"))
        self.assertFalse(data.endswith(b"\n\n"))

    def test_intent_line_with_secret_rejected(self):
        staged = self.baseline + "\nENRICHMENT model=test-model\nINTENT fn_0: uses key ghp_abcdefghijklmnop1234\n"
        with self.assertRaises(ValueError):
            self.commit(staged)

    def test_enrichment_header_with_secret_rejected(self):
        staged = self.baseline + "\nENRICHMENT model=ghp_abcdefghijklmnop1234\nINTENT fn_0: fine\n"
        with self.assertRaises(ValueError):
            self.commit(staged)


if __name__ == "__main__":
    unittest.main()
