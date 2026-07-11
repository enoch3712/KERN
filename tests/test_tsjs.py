import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "kern" / "scripts"))
import kern_compile  # noqa: E402

TS_SAMPLE = '''
import { readFile } from "fs/promises";

const MAX = 10;

export class Loader {
  async fetch(url: string): Promise<string> {
    const data = await readFile(url);
    if (!data) {
      throw new Error(url);
    }
    return data.toString();
  }
}

export function parse(raw: string): number {
  return Number(raw);
}
'''


class TestAvailability(unittest.TestCase):
    def test_available_returns_bool(self):
        self.assertIsInstance(kern_compile.tsjs_available(), bool)

    def test_capabilities_and_fingerprint_are_capability_specific(self):
        caps = kern_compile.tsjs_capabilities()
        self.assertEqual(set(caps), {"javascript", "typescript", "tsx"})
        self.assertTrue(all(isinstance(value, bool) for value in caps.values()))
        fingerprint = kern_compile.tsjs_capability_fingerprint()
        self.assertIn("tree-sitter=", fingerprint)
        self.assertIn("caps=", fingerprint)


@unittest.skipUnless(kern_compile.tsjs_available(typescript=True), "TypeScript grammar not installed")
class TestTsFrontend(unittest.TestCase):
    def setUp(self):
        self.mod = kern_compile.parse_tsjs(TS_SAMPLE, typescript=True)

    def sym(self, name):
        return next(s for s in self.mod.symbols if s.name == name)

    def test_module_metadata(self):
        self.assertEqual(self.mod.lang, "typescript")
        self.assertEqual(self.mod.frontend, "tree-sitter")

    def test_function_and_method(self):
        f = self.sym("parse")
        self.assertEqual(f.kind, "function")
        self.assertIn("raw: string", f.signature)
        m = self.sym("Loader.fetch")
        self.assertTrue(m.is_async)
        self.assertIn("readFile", " ".join(m.calls))

    def test_flow_and_raises(self):
        m = self.sym("Loader.fetch")
        ops = [o.op for o in m.flow]
        self.assertIn("IF", ops)
        self.assertIn("RAISE", ops)
        self.assertIn("RET", ops)
        self.assertIn("Error", m.raises)

    def test_slice_hash_present(self):
        self.assertEqual(len(self.sym("parse").slice8), kern_compile.HANDLE_HEX_LENGTH)

    def test_emit_works(self):
        il = kern_compile.emit_il(self.mod, "src/x.ts", "b" * 64, "none", "L2")
        self.assertTrue(il.startswith("KERN-IL/0.2"))
        self.assertIn("F parse", il)

    def test_broken_source_sets_parse_error(self):
        broken = "export function f( {\n  return 1;\n}\n"
        mod = kern_compile.parse_tsjs(broken, typescript=True)
        self.assertNotEqual(mod.parse_error, "")

    def test_clean_source_has_no_parse_error(self):
        self.assertEqual(self.mod.parse_error, "")

    def test_unparenthesized_expression_arrow_keeps_signature_and_return(self):
        mod = kern_compile.parse_tsjs("const double = x => x * 2;\n")
        symbol = next(s for s in mod.symbols if s.name == "double")
        self.assertEqual(symbol.signature, "x")
        self.assertEqual([(op.op, op.detail) for op in symbol.flow], [("RET", "x * 2")])

    def test_method_decorator_is_in_span_hash_and_redacted(self):
        src = (
            "@auth(\"class-secret\")\n"
            "class Service {\n"
            "  @auth(token=\"hunter2\")\n"
            "  run(password: string = \"correct horse battery staple\", "
            "token: string = \"ghp_abcdefghijklmnop\") {}\n"
            "}\n"
        )
        mod = kern_compile.parse_tsjs(src, typescript=True)
        method = next(s for s in mod.symbols if s.name == "Service.run")
        cls = next(s for s in mod.symbols if s.name == "Service")
        self.assertEqual(cls.span[0], 1)
        self.assertEqual(method.span[0], 3)
        self.assertNotIn("hunter2", " ".join(method.decorators))
        self.assertNotIn("correct horse battery staple", method.signature)
        self.assertNotIn("ghp_abcdefghijklmnop", method.signature)
        self.assertIn("password: string", method.signature)
        self.assertIn("token: string", method.signature)
        changed = kern_compile.parse_tsjs(src.replace("hunter2", "different"), typescript=True)
        changed_method = next(s for s in changed.symbols if s.name == "Service.run")
        self.assertNotEqual(method.slice8, changed_method.slice8)
        il = kern_compile.emit_il(mod, "x.ts", "0" * 64, "none", "L3")
        self.assertNotIn("hunter2", il)
        self.assertNotIn("class-secret", il)
        self.assertNotIn("correct horse battery staple", il)
        self.assertNotIn("ghp_abcdefghijklmnop", il)
        self.assertIn("DECORATORS", il)

    def test_finally_flow_is_preserved(self):
        src = "function f(){try{return acquire()}finally{release()}}\n"
        fn = next(s for s in kern_compile.parse_tsjs(src).symbols if s.name == "f")
        self.assertEqual([op.op for op in fn.flow], ["TRY", "RET", "FINALLY", "CALL"])
        self.assertEqual(fn.flow[-1].detail, "release()")

    def test_nested_function_facts_do_not_leak_to_parent(self):
        src = (
            "function outer(){\n"
            "  function inner(){ audit({password: 'hunter2'}); throw new Error('inner'); }\n"
            "  return 1;\n"
            "}\n"
        )
        outer = next(s for s in kern_compile.parse_tsjs(src).symbols if s.name == "outer")
        self.assertNotIn("audit", outer.calls)
        self.assertNotIn("Error", outer.raises)
        self.assertIn("NESTED", [op.op for op in outer.flow])

    def test_common_types_namespaces_and_commonjs_are_explicit(self):
        src = (
            "namespace API { export function run(){ return 1; } }\n"
            "interface User { id: string; save(): Promise<void>; }\n"
            "type UserId = string;\n"
            "enum State { Ready = 1, Done = 2 }\n"
            "module.exports.load = x => x + 1;\n"
        )
        mod = kern_compile.parse_tsjs(src, typescript=True)
        facts = {(s.kind, s.name) for s in mod.symbols}
        for expected in {
            ("namespace", "API"), ("function", "API.run"),
            ("type", "User"), ("function", "User.save"),
            ("type", "UserId"), ("enum", "State"),
            ("function", "module.exports.load"),
        }:
            self.assertIn(expected, facts)
        self.assertGreater(mod.omit["assignments"], 0)
        il = kern_compile.emit_il(mod, "x.ts", "0" * 64, "none", "L2")
        self.assertIn("NAMESPACE API", il)
        self.assertIn("TYPE User", il)
        self.assertIn("ENUM State", il)
        self.assertIn("F module.exports.load(x)", il)
        self.assertNotIn("User.idid", il)

    def test_default_arrow_is_named_and_emitted(self):
        mod = kern_compile.parse_tsjs("export default x => x.trim();\n")
        symbol = next(s for s in mod.symbols if s.name == "default")
        self.assertEqual(symbol.signature, "x")
        self.assertEqual(symbol.flow[0].op, "RET")

    def test_secret_calls_and_literal_whitespace_are_safe(self):
        src = (
            "function login(password = \"hunter2\") {\n"
            "  configure({token: \"call-secret\", label: \"two  spaces\"});\n"
            "  setToken(\"positional-secret\");\n"
            "  return /a  b/.test(\"a  b\");\n"
            "}\n"
        )
        il = kern_compile.emit_il(
            kern_compile.parse_tsjs(src), "x.js", "0" * 64, "none", "L3"
        )
        for secret in ("hunter2", "call-secret", "positional-secret"):
            self.assertNotIn(secret, il)
        self.assertIn('label: "two  spaces"', il)
        self.assertIn('/a  b/.test("a  b")', il)

    def test_secret_enum_type_and_commonjs_properties_are_redacted(self):
        src = (
            'enum Secrets { API_TOKEN = "enum-secret", SAFE = "visible" }\n'
            'interface Shape { password: "interface-secret"; label: string; }\n'
            'type Alias = { credential: "type-secret"; };\n'
            'module.exports = { token: "commonjs-secret", label: "visible" };\n'
        )
        il = kern_compile.emit_il(
            kern_compile.parse_tsjs(src, typescript=True),
            "x.ts", "0" * 64, "none", "L3",
        )
        for secret in ("enum-secret", "interface-secret", "type-secret", "commonjs-secret"):
            self.assertNotIn(secret, il)
        self.assertIn("<REDACTED", il)
        self.assertIn('SAFE = "visible"', il)
        self.assertIn('module.exports.label="visible"', il)

    def test_unicode_line_separators_are_escaped_in_strings_and_templates(self):
        src = (
            'function values(){ return ["a\x85b", '
            '`c\u2028d\u2029e`, `f\x1cg\x1dh\x1ei`]; }\n'
        )
        mod = kern_compile.parse_tsjs(src)
        self.assertEqual(mod.parse_error, "")
        il = kern_compile.emit_il(mod, "x.js", "0" * 64, "none", "L3")
        for separator in ("\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"):
            self.assertNotIn(separator, il)
        for escaped in (r"\x1c", r"\x1d", r"\x1e", r"\x85", r"\u2028", r"\u2029"):
            self.assertIn(escaped, il)
        self.assertEqual(len(il.splitlines()), il.count("\n"))

    def test_escaped_physical_separators_cannot_bypass_one_line_encoding(self):
        backslash = "\\"
        src = (
            'function quoted(){ return "a' + backslash + "\n"
            + "b" + backslash + "\t"
            + "c" + backslash + "\x85" + 'd"; }\n'
            + 'function templated(){ return `e' + backslash + "\u2028"
            + "f" + backslash + "\u2029"
            + "g" + backslash + "\x1c" + 'h`; }\n'
        )
        mod = kern_compile.parse_tsjs(src)
        self.assertEqual(mod.parse_error, "")
        il = kern_compile.emit_il(mod, "x.js", "0" * 64, "none", "L3")
        for separator in ("\n", "\t", "\x1c", "\x85", "\u2028", "\u2029"):
            if separator != "\n":
                self.assertNotIn(separator, il)
        for escaped in (r"\\n", r"\\t", r"\\x1c", r"\\x85", r"\\u2028", r"\\u2029"):
            self.assertIn(escaped, il)
        self.assertEqual(len(il.splitlines()), il.count("\n"))

    def test_language_aware_effects_and_conservative_resolution(self):
        src = (
            "class Parser { parse(){ throw new SyntaxError(); } }\n"
            "async function load(obj, url){\n"
            "  const response = await fetch(url);\n"
            "  const data = await readFile('x');\n"
            "  await writeFile('y', data);\n"
            "  return obj.parse(response);\n"
            "}\n"
        )
        mod = kern_compile.parse_tsjs(src)
        kern_compile.propagate(mod)
        load = next(s for s in mod.symbols if s.name == "load")
        self.assertEqual(set(load.effects), {"net", "fs:read", "fs:write"})
        self.assertNotIn("SyntaxError", load.raises_all)
        self.assertGreaterEqual(load.unknown_calls, 1)


@unittest.skipUnless(kern_compile.tsjs_available(tsx=True), "TSX grammar not installed")
class TestTsxFrontend(unittest.TestCase):
    def test_valid_tsx_uses_tsx_grammar(self):
        src = "export const App = (): JSX.Element => <main>Hello</main>;\n"
        mod = kern_compile.parse_tsjs(src, typescript=True, tsx=True)
        self.assertEqual(mod.parse_error, "")
        app = next(s for s in mod.symbols if s.name == "App")
        self.assertIn("<main>Hello</main>", app.flow[0].detail)


if __name__ == "__main__":
    unittest.main()
