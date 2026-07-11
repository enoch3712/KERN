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


if __name__ == "__main__":
    unittest.main()
