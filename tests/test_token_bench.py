import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills" / "kern" / "scripts"))

spec = importlib.util.spec_from_file_location("token_bench", REPO / "benchmarks" / "token_bench.py")
token_bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(token_bench)

BIG = '"""Doc."""\n\nimport json\n\n' + "\n\n".join(
    f'def fn_{i}(path):\n'
    f'    """Doc {i} with a somewhat longer explanatory sentence to add bulk."""\n'
    f'    # a comment line adding source-only weight\n'
    f'    data = path.read_bytes()\n'
    f'    if not data:\n'
    f'        raise ValueError(path)\n'
    f'    return json.loads(data)\n'
    for i in range(40)
)


class TestTokenBench(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.f = self.tmp / "big.py"
        self.f.write_text(BIG)

    def test_bench_file_shape(self):
        row = token_bench.bench_file(self.f)
        self.assertIn("source_tokens", row)
        self.assertEqual(set(row["tiers"]), {"L1", "L2", "L3"})
        for tier in row["tiers"].values():
            self.assertGreater(tier["ratio"], 1.0)
            self.assertIs(tier["fidelity_ok"], True)
            self.assertEqual(tier["fidelity_missing"], [])

    def test_tier_ordering(self):
        row = token_bench.bench_file(self.f)
        self.assertGreater(row["tiers"]["L1"]["ratio"], row["tiers"]["L2"]["ratio"])
        self.assertGreater(row["tiers"]["L2"]["ratio"], row["tiers"]["L3"]["ratio"])

    def test_fidelity_no_missing_functions(self):
        row = token_bench.bench_file(self.f)
        self.assertEqual(row["fidelity_missing"], [])

    def test_parse_error_reported_not_raised(self):
        bad = self.tmp / "bad.py"
        bad.write_text("def broken(:\n")
        row = token_bench.bench_file(bad)
        self.assertIn("error", row)

    def test_fidelity_not_fooled_by_substring(self):
        f = self.tmp / "sub.py"
        f.write_text(
            '"""Doc."""\n\n' + "\n\n".join(
                f'def helper_{i}(x):\n    return format(x)\n' for i in range(40)
            )
        )
        row = token_bench.bench_file(f)
        self.assertEqual(row["fidelity_missing"], [])

    def test_fidelity_flags_suffix_collision(self):
        f = self.tmp / "suffix.py"
        f.write_text(
            '"""Doc."""\n\n'
            "def foobar(x):\n    return x\n\n\n"
            "def bar(x):\n    return x\n"
        )
        text = f.read_text()
        module = token_bench.kern_compile.parse_python(text)
        il = token_bench.kern_compile.emit_il(module, "x.py", "0" * 64, "none", "L2")
        # Drop bar's own F-line, leaving only "F foobar(" in the IL. The tail
        # "bar" is a strict suffix of "foobar" but not the full final name
        # segment, so it must not count as present.
        il_without_bar = "\n".join(
            l for l in il.splitlines() if not l.startswith("F bar(")
        )
        missing = token_bench.fidelity_missing(module, il_without_bar)
        self.assertTrue(any(item.startswith("bar@") and item.endswith(":header") for item in missing))

    def test_fidelity_checks_semantic_facts_independently(self):
        text = self.f.read_text()
        module = token_bench.kern_compile.parse_python(text)
        token_bench.kern_compile.propagate(module)
        symbol = next(s for s in module.symbols if s.name == "fn_0")
        il = token_bench.kern_compile.emit_il(module, "x.py", "0" * 64, "none", "L2")

        cases = {
            "signature": il.replace(
                f"fn_0({symbol.signature})", "fn_0(definitely_wrong)", 1
            ),
            "returns": il.replace(
                f") -> {symbol.returns or 'Any'} @L{symbol.span[0]}",
                f") -> DefinitelyWrong @L{symbol.span[0]}",
                1,
            ),
            "source-handle": il.replace(
                f"^{token_bench.symbol_handle(symbol)}", "^deadbeefdeadbeef", 1
            ),
            "tier": il.replace("~L2", "~L1", 1),
            "call": il.replace("path.read_bytes", "path.missing_read", 1),
            "effect": il.replace("EFFECTS fs:read", "EFFECTS missing", 1),
            "raise": il.replace("RAISES ValueError", "RAISES MissingError", 1),
        }

        for category, changed in cases.items():
            with self.subTest(category=category):
                missing = token_bench.fidelity_missing(module, changed, "L2")
                self.assertTrue(
                    any(f":{category}" in item for item in missing),
                    f"expected {category} failure, got {missing}",
                )

    def test_fidelity_checks_class_source_handle(self):
        source = "class Widget:\n    def run(self):\n        return 1\n"
        module = token_bench.kern_compile.parse_python(source)
        cls = next(s for s in module.symbols if s.kind == "class")
        il = token_bench.kern_compile.emit_il(module, "x.py", "0" * 64, "none", "L2")
        changed = il.replace(f"^{token_bench.symbol_handle(cls)}", "^deadbeefdeadbeef", 1)
        missing = token_bench.fidelity_missing(module, changed, "L2")
        self.assertTrue(any(item.startswith("Widget@") and item.endswith(":source-handle") for item in missing))

    def test_call_fidelity_ignores_effect_provenance_but_accepts_l3_flow(self):
        source = (
            "def load(path):\n"
            "    return path.read_bytes()\n\n"
            "def caller(path):\n"
            "    data = load(path)\n"
            "    return transform(data)\n"
        )
        module = token_bench.kern_compile.parse_python(source)

        l2 = token_bench.kern_compile.emit_il(module, "x.py", "0" * 64, "none", "L2")
        self.assertIn("EFFECTS fs:read (via load)", l2)
        l2_without_call = l2.replace("  CALLS load, transform\n", "  CALLS transform\n", 1)
        missing = token_bench.fidelity_missing(module, l2_without_call, "L2")
        self.assertTrue(any(item.endswith(":call:load") for item in missing), missing)

        l3 = token_bench.kern_compile.emit_il(module, "x.py", "0" * 64, "none", "L3")
        self.assertNotIn("  CALLS load", l3)
        self.assertIn("CALL load(path) -> data", l3)
        self.assertIn("RET transform(data)", l3)
        self.assertFalse(token_bench.fidelity_missing(module, l3, "L3"))

        l3_without_call = l3.replace("CALL load(path) -> data", "CALL missing(path) -> data", 1)
        missing = token_bench.fidelity_missing(module, l3_without_call, "L3")
        self.assertTrue(any(item.endswith(":call:load") for item in missing), missing)
        self.assertFalse(any(item.endswith(":call:transform") for item in missing), missing)


if __name__ == "__main__":
    unittest.main()
