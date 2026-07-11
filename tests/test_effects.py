import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "kern" / "scripts"))
import kern_compile  # noqa: E402

SAMPLE = '''
def read_expected(root):
    pin = (root / ".pin").read_text().strip()
    if len(pin) != 64:
        raise ValueError(root)
    return pin


def load_entry(path, expected_sha):
    data = path.read_bytes()
    if not data:
        raise StaleSource(path)
    return data


def find_entries(root):
    manifest = load_entry(root / "m.json", read_expected(root))
    frobnicate(manifest)
    return manifest
'''


class TestClassify(unittest.TestCase):
    def test_fs_read(self):
        self.assertIn("fs:read", kern_compile.classify_call("path.read_bytes"))
        self.assertIn("fs:read", kern_compile.classify_call("open"))
    def test_fs_write(self):
        self.assertIn("fs:write", kern_compile.classify_call("os.replace"))
    def test_proc(self):
        self.assertIn("proc", kern_compile.classify_call("subprocess.run"))
    def test_net(self):
        self.assertIn("net", kern_compile.classify_call("requests.post"))
    def test_unknown(self):
        self.assertEqual(kern_compile.classify_call("frobnicate"), [])

    def test_python_open_modes_and_chained_calls(self):
        self.assertEqual(kern_compile.classify_call('open("x")'), ["fs:read"])
        self.assertEqual(kern_compile.classify_call('open("x", "w")'), ["fs:write"])
        self.assertEqual(
            set(kern_compile.classify_call('open("x", mode="r+")')),
            {"fs:read", "fs:write"},
        )
        self.assertIn(
            "fs:write",
            kern_compile.classify_call('Path("x").open("w").write("payload")'),
        )

    def test_javascript_effects_are_language_specific(self):
        self.assertEqual(kern_compile.classify_call("open(url)", "javascript"), [])
        self.assertIn("net", kern_compile.classify_call("fetch(url)", "javascript"))
        self.assertIn("fs:read", kern_compile.classify_call('readFile("x")', "javascript"))
        self.assertIn("fs:write", kern_compile.classify_call('fs.promises.writeFile("x", data)', "javascript"))
        self.assertIn("thread", kern_compile.classify_call('new Worker("x.js")', "javascript"))


class TestPropagate(unittest.TestCase):
    def setUp(self):
        self.mod = kern_compile.parse_python(SAMPLE)
        kern_compile.propagate(self.mod)

    def sym(self, name):
        return next(s for s in self.mod.symbols if s.name == name)

    def test_direct_effect(self):
        self.assertEqual(self.sym("load_entry").effects.get("fs:read"), [])

    def test_inherited_effect_with_via(self):
        eff = self.sym("find_entries").effects
        self.assertIn("fs:read", eff)
        self.assertIn("load_entry", eff["fs:read"])

    def test_raises_propagate(self):
        ra = self.sym("find_entries").raises_all
        self.assertIn("StaleSource", ra)
        self.assertIn("ValueError", ra)
        self.assertIn("read_expected", ra["ValueError"])

    def test_unknown_counted(self):
        self.assertGreaterEqual(self.sym("find_entries").unknown_calls, 1)

    def test_idempotent(self):
        before = {s.name: dict(s.effects) for s in self.mod.symbols}
        kern_compile.propagate(self.mod)
        after = {s.name: dict(s.effects) for s in self.mod.symbols}
        self.assertEqual(before, after)

    def test_member_tail_does_not_invent_local_edge(self):
        src = (
            "class Parser:\n"
            "    def parse(self):\n"
            "        raise SyntaxError()\n\n"
            "def route(obj):\n"
            "    return obj.parse()\n"
        )
        mod = kern_compile.parse_python(src)
        kern_compile.propagate(mod)
        route = next(s for s in mod.symbols if s.name == "route")
        self.assertNotIn("SyntaxError", route.raises_all)
        self.assertEqual(route.unknown_calls, 1)

    def test_explicit_self_call_resolves_within_owner(self):
        src = (
            "class Parser:\n"
            "    def parse(self):\n"
            "        raise SyntaxError()\n"
            "    def route(self):\n"
            "        return self.parse()\n"
        )
        mod = kern_compile.parse_python(src)
        kern_compile.propagate(mod)
        route = next(s for s in mod.symbols if s.name == "Parser.route")
        self.assertIn("SyntaxError", route.raises_all)
        self.assertIn("Parser.parse", route.raises_all["SyntaxError"])

    def test_structural_effects_keep_mixed_known_calls(self):
        src = (
            "def mixed(path):\n"
            "    open(path, 'w').write('x')\n"
            "    subprocess.run(['true'])\n"
        )
        mod = kern_compile.parse_python(src)
        kern_compile.propagate(mod)
        effects = next(s for s in mod.symbols if s.name == "mixed").effects
        self.assertIn("fs:write", effects)
        self.assertIn("proc", effects)
        self.assertNotIn("fs:read", effects)


if __name__ == "__main__":
    unittest.main()
