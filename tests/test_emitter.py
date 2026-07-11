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
        self.assertRegex(fline, r"^F load_entry\(.+\) -> dict @L\d+-\d+ \^[0-9a-f]{16} ~L2$")

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

    def test_tier_l2_drops_bare_call_keeps_risky_call(self):
        src = (
            "import hashlib\n\n"
            "def f(x):\n"
            "    a = helper(x)\n"
            "    d = hashlib.sha1(x).digest()\n"
            "    return a\n"
        )
        mod = kern_compile.parse_python(src)
        il = kern_compile.emit_il(mod, "src/x.py", "a" * 64, "none", "L2")
        flow = [l.strip() for l in il.splitlines() if l.startswith("    ")]
        self.assertNotIn("CALL", flow)
        self.assertIn("CALL !FAULT(crypto)", flow)
        self.assertIn("CALLS", il)
        l3 = kern_compile.emit_il(mod, "src/x.py", "a" * 64, "none", "L3")
        self.assertIn("CALL helper(x) -> a", l3)

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

    def test_scattered_imports_do_not_balloon_span(self):
        src = (
            "import os\n\n\n"
            "def a():\n    return 1\n\n\n"
            "import sys\n\n\n"
            "def b():\n    return 2\n"
        )
        mod = kern_compile.parse_python(src)
        il = kern_compile.emit_il(mod, "src/x.py", "a" * 64, "none", "L2")
        import_lines = [l for l in il.splitlines() if l.startswith("IMPORTS")]
        self.assertEqual(len(import_lines), 2)
        self.assertIn("os @L1-1", import_lines[0])
        self.assertIn("sys @L8-8", import_lines[1])

    def test_l3_keeps_compound_expression_calls(self):
        src = (
            "def combine(a, b):\n"
            "    total = compute_one(a) + compute_two(b)\n"
            "    return total\n"
        )
        mod = kern_compile.parse_python(src)
        il3 = kern_compile.emit_il(mod, "src/x.py", "a" * 64, "none", "L3")
        self.assertIn("compute_one", il3)
        self.assertIn("compute_two", il3)
        il3_dup = kern_compile.emit_il(mod, "src/x.py", "a" * 64, "none", "L3")
        self.assertEqual(il3, il3_dup)

    def test_call_fidelity_is_not_truncated_after_twenty_five_names(self):
        calls = "\n".join(f"    call_{index}()" for index in range(40))
        source = f"def many_calls():\n{calls}\n"
        module = kern_compile.parse_python(source)
        il = kern_compile.emit_il(module, "src/x.py", "a" * 64, "none", "L1")
        for index in range(40):
            self.assertIn(f"call_{index}", il)
        self.assertNotIn("…+", il)

    def test_l3_omits_calls_already_in_flow(self):
        mod = kern_compile.parse_python(SAMPLE)
        il3 = kern_compile.emit_il(mod, "src/x.py", "a" * 64, "none", "L3")
        f_block = il3.split("F load_entry")[1].split("\n\n")[0]
        self.assertNotIn("CALLS path.read_bytes", f_block)

    def test_l3_short_call_name_not_falsely_covered(self):
        src = (
            "def f(y):\n"
            "    pos = at(y) + 1\n"
            "    return format(y)\n"
        )
        mod = kern_compile.parse_python(src)
        il3 = kern_compile.emit_il(mod, "src/x.py", "a" * 64, "none", "L3")
        f_block = il3.split("F f(")[1]
        self.assertIn("CALLS", f_block)
        self.assertIn("at", [c.strip() for c in f_block.split("CALLS")[1].splitlines()[0].split(",")])

    def test_secret_parameter_defaults_redacted(self):
        src = 'def connect(host, password="hunter2", api_key="abc123shortkey"):\n    return host\n'
        mod = kern_compile.parse_python(src)
        for tier in ("L1", "L2", "L3"):
            il = kern_compile.emit_il(mod, "src/x.py", "a" * 64, "none", tier)
            self.assertNotIn("hunter2", il)
            self.assertNotIn("abc123shortkey", il)
            self.assertIn("password", il)

    def test_secret_kwargs_and_decorator_arguments_redacted_structurally(self):
        src = (
            "@auth('decorator-secret', token='keyword-secret')\n"
            "def connect():\n"
            "    login(password='call-secret', role='a  b')\n"
            "    set_token('positional-secret')\n"
            "    return 'two  spaces'\n"
        )
        for tier in ("L1", "L2", "L3"):
            il = kern_compile.emit_il(
                kern_compile.parse_python(src), "src/x.py", "a" * 64, "none", tier
            )
            for secret in ("decorator-secret", "keyword-secret", "call-secret", "positional-secret"):
                self.assertNotIn(secret, il)
            self.assertIn("<REDACTED", il)
        il3 = kern_compile.emit_il(
            kern_compile.parse_python(src), "src/x.py", "a" * 64, "none", "L3"
        )
        self.assertIn("role='a  b'", il3)
        self.assertIn("RET 'two  spaces'", il3)

    def test_semantic_handle_is_emitted_instead_of_bare_slice(self):
        mod = kern_compile.parse_python(SAMPLE)
        source_slice = next(s for s in mod.symbols if s.name == "load_entry").slice8
        il = kern_compile.emit_il(mod, "src/x.py", "a" * 64, "none", "L2")
        symbol = next(s for s in mod.symbols if s.name == "load_entry")
        self.assertNotEqual(symbol.semantic8, source_slice)
        self.assertIn("^" + symbol.semantic8, il)

    def test_annotation_only_const_has_no_fabricated_value(self):
        src = "count: int\nNAME = 'x'\n\n\ndef f():\n    return count\n"
        mod = kern_compile.parse_python(src)
        il = kern_compile.emit_il(mod, "src/x.py", "a" * 64, "none", "L2")
        self.assertNotIn("count=None", il)
        self.assertIn("count", il)


if __name__ == "__main__":
    unittest.main()
