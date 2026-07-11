import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "kern" / "scripts"))
import kern_compile  # noqa: E402

SAMPLE = '''
import re
from pathlib import Path

TOKEN = "ghp_abcdefghijklmnop1234"
PATTERN = re.compile(r"^[a-z]+$")


def load_entry(path: Path, expected_sha: str) -> dict:
    data = path.read_bytes()
    if not data:
        raise ValueError(path)
    return parse(data)


def parse(data):
    return data.decode()
'''


def emit(tier):
    mod = kern_compile.parse_python(SAMPLE)
    return kern_compile.emit_il(mod, "src/x.py", "a" * 64, "d7e8242", tier)


class TestEmitter(unittest.TestCase):
    def test_header(self):
        il = emit("L2").splitlines()
        self.assertEqual(il[0], "KERN-IL/0.2")
        self.assertEqual(il[1], "source_rel=src/x.py")
        self.assertEqual(il[2], "source_sha256=" + "a" * 64)
        self.assertEqual(il[3], "repo_revision=d7e8242")
        self.assertIn("generator=kern-det/0.2 lang=python frontend=pyast tier=L2", il[4])

    def test_function_line_format(self):
        il = emit("L2")
        fline = next(l for l in il.splitlines() if l.startswith("F load_entry"))
        self.assertRegex(fline, r"^F load_entry\(.+\) -> dict @L\d+-\d+ \^[0-9a-f]{8} ~L2$")

    def test_tier_l1_has_no_flow(self):
        il = emit("L1")
        self.assertNotIn("  IF", il)
        self.assertIn("CALLS", il)
        self.assertIn("EFFECTS fs:read", il)
        self.assertIn("RAISES ValueError", il)

    def test_tier_l2_flow_without_expressions(self):
        il = emit("L2")
        body = [l for l in il.splitlines() if l.startswith("    ")]
        joined = "\n".join(body)
        self.assertIn("IF", joined)
        self.assertIn("RAISE", joined)
        self.assertNotIn("not data", joined)

    def test_tier_l3_flow_with_expressions_and_binds(self):
        il = emit("L3")
        self.assertIn("CALL path.read_bytes() -> data", il)
        self.assertIn("IF not data", il)

    def test_l3_is_larger_than_l2_is_larger_than_l1(self):
        self.assertGreater(len(emit("L3")), len(emit("L2")))
        self.assertGreater(len(emit("L2")), len(emit("L1")))

    def test_secret_never_in_output(self):
        for tier in ("L1", "L2", "L3"):
            self.assertNotIn("ghp_abcdefghijklmnop1234", emit(tier))

    def test_regex_const_fault_tagged(self):
        il = emit("L2")
        cline = next(l for l in il.splitlines() if l.startswith("C PATTERN"))
        self.assertIn("!FAULT(regex)", cline)
        self.assertIn("regex(L", il.splitlines()[-1])

    def test_omit_counts_and_fault_before(self):
        lines = emit("L2").splitlines()
        self.assertTrue(lines[-2].startswith("OMIT "))
        self.assertIn("bodies-tier=L2", lines[-2])
        self.assertTrue(lines[-1].startswith("FAULT-BEFORE edit(any), exact-literals"))

    def test_deterministic(self):
        self.assertEqual(emit("L2"), emit("L2"))


if __name__ == "__main__":
    unittest.main()
