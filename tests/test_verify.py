import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "kern" / "scripts"))
import kern_cache  # noqa: E402
import kern_compile  # noqa: E402

KERN_CACHE = Path(kern_cache.__file__).resolve()

SRC = '''import json


def load_entry(path, expected_sha):
    data = path.read_bytes()
    if not data:
        raise ValueError(path)
    return json.loads(data)
'''


def compiled_module(text):
    return kern_compile.apply_semantic_handles(kern_compile.parse_python(text))


def handle_of(symbol):
    return symbol.semantic8


class TestVerify(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.file = self.root / "mod.py"
        self.file.write_text(SRC)
        self.paths, self.config = kern_cache.initialize(self.root)
        mod = compiled_module(SRC)
        self.sym = next(s for s in mod.symbols if s.name == "load_entry")

    def verify(self, expected_hash, span=None):
        rel, src = kern_cache.normalize_rel(self.root, "mod.py")
        return kern_cache.verify_symbol(self.root, self.paths, rel, src,
                                        "load_entry", expected_hash, span)

    def test_ok(self):
        r = self.verify(handle_of(self.sym), f"L{self.sym.span[0]}-{self.sym.span[1]}")
        self.assertEqual(r["result"], "ok")
        self.assertIs(r["ok"], True)

    def test_moved_when_file_shifts(self):
        self.file.write_text("# new comment line\n" + SRC)
        r = self.verify(handle_of(self.sym), f"L{self.sym.span[0]}-{self.sym.span[1]}")
        self.assertEqual(r["result"], "moved")
        self.assertIs(r["ok"], True)
        self.assertIn("current_span", r)

    def test_stale_when_body_changes(self):
        self.file.write_text(SRC.replace("json.loads(data)", "json.loads(data.strip())"))
        r = self.verify(handle_of(self.sym))
        self.assertEqual(r["result"], "stale")
        self.assertIs(r["ok"], False)
        self.assertEqual(r["reason"], "source-handle-changed")

    def test_stale_when_symbol_deleted(self):
        self.file.write_text("import json\n")
        r = self.verify(handle_of(self.sym))
        self.assertEqual(r["result"], "stale")
        self.assertIs(r["ok"], False)
        self.assertEqual(r["reason"], "symbol-not-found")

    def test_cli_stale_exits_one_and_logs_failure(self):
        self.file.write_text(SRC.replace("json.loads(data)", "json.loads(data.strip())"))
        result = subprocess.run(
            [
                sys.executable,
                str(KERN_CACHE),
                "--repo",
                str(self.root),
                "verify",
                "mod.py",
                "--symbol",
                "load_entry",
                "--hash",
                handle_of(self.sym),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["result"], "stale")
        self.assertIs(payload["ok"], False)

        entries = [json.loads(line) for line in self.paths["log"].read_text().splitlines()]
        self.assertEqual(entries[-1]["op"], "verify")
        self.assertEqual(entries[-1]["result"], "stale")
        self.assertIs(entries[-1]["ok"], False)

    def test_unsupported_suffix_raises(self):
        (self.root / "x.rb").write_text("def x; end\n" * 200)
        rel, src = kern_cache.normalize_rel(self.root, "x.rb")
        with self.assertRaises(ValueError):
            kern_cache.verify_symbol(self.root, self.paths, rel, src, "x", "deadbeef", None)

    def test_duplicate_names_hash_selects_correct_candidate(self):
        import kern_compile
        src = (
            "class Widget:\n"
            "    @property\n"
            "    def x(self):\n"
            "        return self._x\n"
            "    @x.setter\n"
            "    def x(self, value):\n"
            "        self._x = value\n"
        )
        self.file.write_text(src)
        mod = compiled_module(src)
        setter = [s for s in mod.symbols if s.name == "Widget.x"][1]
        rel, srcp = kern_cache.normalize_rel(self.root, "mod.py")
        r = kern_cache.verify_symbol(self.root, self.paths, rel, srcp, "Widget.x",
                                     handle_of(setter), f"L{setter.span[0]}-{setter.span[1]}")
        self.assertEqual(r["result"], "ok")
        self.assertEqual(r["current_span"], f"L{setter.span[0]}-{setter.span[1]}")

    def test_duplicate_names_stale_lists_candidates(self):
        src = (
            "class Widget:\n"
            "    @property\n"
            "    def x(self):\n"
            "        return self._x\n"
            "    @x.setter\n"
            "    def x(self, value):\n"
            "        self._x = value\n"
        )
        self.file.write_text(src)
        rel, srcp = kern_cache.normalize_rel(self.root, "mod.py")
        r = kern_cache.verify_symbol(self.root, self.paths, rel, srcp, "Widget.x",
                                     "00000000", None)
        self.assertEqual(r["result"], "stale")
        self.assertEqual(len(r["candidates"]), 2)

    def test_source_sha256_matches_raw_bytes(self):
        data = self.file.read_bytes()
        rel, srcp = kern_cache.normalize_rel(self.root, "mod.py")
        r = kern_cache.verify_symbol(self.root, self.paths, rel, srcp,
                                     "load_entry", "00000000", None)
        self.assertEqual(r["source_sha256"], kern_cache.sha256_bytes(data))

    def test_unicode_separator_cannot_fake_ok(self):
        import kern_compile
        src = 'X = "a b"\n\n\ndef load_entry(path):\n    return path.read_bytes()\n'
        self.file.write_text(src)
        mod = compiled_module(src)
        sym = next(s for s in mod.symbols if s.name == "load_entry")
        self.file.write_text(src.replace("read_bytes()", "read_text()"))
        rel, srcp = kern_cache.normalize_rel(self.root, "mod.py")
        r = kern_cache.verify_symbol(self.root, self.paths, rel, srcp, "load_entry", handle_of(sym), None)
        self.assertEqual(r["result"], "stale")

    def test_same_file_dependency_change_invalidates_caller(self):
        src = (
            "def normalize(data):\n"
            "    return data.strip()\n\n"
            "def load_entry(path, expected_sha):\n"
            "    return normalize(path.read_bytes())\n"
        )
        self.file.write_text(src)
        old = compiled_module(src)
        caller = next(s for s in old.symbols if s.name == "load_entry")
        self.file.write_text(src.replace("return data.strip()", "return data.rstrip()"))
        r = self.verify(handle_of(caller), f"L{caller.span[0]}-{caller.span[1]}")
        self.assertEqual(r["result"], "stale")
        self.assertIs(r["ok"], False)
        self.assertEqual(r["reason"], "source-handle-changed")

    def test_crlf_source_verifies_against_exact_bytes(self):
        raw = SRC.replace("\n", "\r\n").encode("utf-8")
        self.file.write_bytes(raw)
        module = compiled_module(raw.decode("utf-8"))
        symbol = next(s for s in module.symbols if s.name == "load_entry")
        r = self.verify(handle_of(symbol), f"L{symbol.span[0]}-{symbol.span[1]}")
        self.assertEqual(r["result"], "ok")
        self.assertEqual(r["source_sha256"], kern_cache.sha256_bytes(raw))

    def test_invalid_utf8_is_rejected_without_replacement(self):
        self.file.write_bytes(b"def load_entry():\n    return 1\n\xff")
        with self.assertRaisesRegex(ValueError, "not valid UTF-8"):
            self.verify("deadbeef")

    def test_fault_source_immune_to_unicode_line_separators(self):
        src = 'X = "a b"\n\n\ndef load_entry(path):\n    return path.read_bytes()\n'
        self.file.write_text(src)
        body = kern_cache.fault_source(self.file, "mod.py", 4, 5, None)
        self.assertIn("def load_entry(path):", body)
        self.assertIn("return path.read_bytes()", body)


if __name__ == "__main__":
    unittest.main()
