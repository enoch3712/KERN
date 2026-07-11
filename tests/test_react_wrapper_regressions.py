import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "kern" / "scripts"))
import kern_cache  # noqa: E402
import kern_compile  # noqa: E402


@unittest.skipUnless(kern_compile.tsjs_available(tsx=True), "tsx grammar not installed")
class TestReactWrapperRegressions(unittest.TestCase):
    def parse(self, source):
        return kern_compile.parse_tsjs(source, tsx=True)

    def symbol(self, module, name="Row"):
        return next(symbol for symbol in module.symbols if symbol.name == name)

    def test_non_jsx_wrapper_probe_preserves_const_symbol(self):
        cases = {
            "memo": "const Row = memo((value) => value + 1);\n",
            "forwardRef": "const Row = forwardRef((props, ref) => props.value);\n",
            "React.memo": "const Row = React.memo((value) => value + 1);\n",
            "React.forwardRef": (
                "const Row = React.forwardRef((props, ref) => props.value);\n"
            ),
        }
        for wrapper, source in cases.items():
            with self.subTest(wrapper=wrapper):
                module = self.parse(source)
                symbol = self.symbol(module)
                self.assertEqual(module.frontend, "tree-sitter")
                self.assertEqual(symbol.kind, "const")
                self.assertEqual(symbol.detail, "=" + source.split("=", 1)[1].strip().rstrip(";"))
                self.assertEqual(symbol.react, {})
                self.assertEqual(len([item for item in module.symbols if item.name == "Row"]), 1)

    def test_inline_comparator_is_part_of_component_source_identity(self):
        original = (
            "const Row = memo(\n"
            "  ({ id }) => <li>{id}</li>,\n"
            "  (previous, next) => previous.id === next.id\n"
            ");\n"
        )
        changed = original.replace("previous.id === next.id", "previous.key === next.key")

        before_module = kern_compile.apply_semantic_handles(self.parse(original))
        after_module = kern_compile.apply_semantic_handles(self.parse(changed))
        before = self.symbol(before_module)
        after = self.symbol(after_module)

        self.assertEqual(before.kind, "component")
        self.assertEqual(before.react["wrapper"], "memo")
        self.assertEqual(before.span, (1, 4))
        self.assertEqual(after.span, before.span)
        self.assertNotEqual(after.slice8, before.slice8)
        self.assertNotEqual(after.semantic8, before.semantic8)

    def test_declaration_keyword_is_part_of_component_source_identity(self):
        original = "const\nRow = memo(() => <X/>);\n"
        changed = original.replace("const", "var")

        before = self.symbol(kern_compile.apply_semantic_handles(self.parse(original)))
        after = self.symbol(kern_compile.apply_semantic_handles(self.parse(changed)))

        self.assertEqual(before.span, (1, 2))
        self.assertEqual(after.span, before.span)
        self.assertNotEqual(after.slice8, before.slice8)
        self.assertNotEqual(after.semantic8, before.semantic8)

    def test_typed_wrapper_callbacks_are_unwrapped(self):
        callbacks = {
            "parenthesized": "(({ id }: P) => <li>{id}</li>)",
            "as": "((({ id }: P) => <li>{id}</li>) as React.FC<P>)",
            "satisfies": "((({ id }: P) => <li>{id}</li>) satisfies React.FC<P>)",
            "non-null": "((({ id }: P) => <li>{id}</li>)!)",
        }
        for label, callback in callbacks.items():
            with self.subTest(label=label):
                module = self.parse(f"const Row = memo({callback});\n")
                symbol = self.symbol(module)
                self.assertEqual(module.frontend, "tree-sitter+react")
                self.assertEqual(symbol.kind, "component")
                self.assertEqual(symbol.react["wrapper"], "memo")

    def test_typed_wrapper_result_is_unwrapped(self):
        module = self.parse(
            "const Row = (memo(({id}: P) => <li>{id}</li>) as React.FC<P>);\n"
        )
        symbol = self.symbol(module)
        self.assertEqual(module.frontend, "tree-sitter+react")
        self.assertEqual(symbol.kind, "component")
        self.assertEqual(symbol.react["wrapper"], "memo")

    def test_verify_reports_stale_after_inline_comparator_change(self):
        original = (
            "const Row = memo(\n"
            "  ({ id }) => <li>{id}</li>,\n"
            "  (previous, next) => previous.id === next.id\n"
            ");\n"
        )
        changed = original.replace("previous.id === next.id", "previous.key === next.key")
        module = kern_compile.apply_semantic_handles(self.parse(original))
        symbol = self.symbol(module)

        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        source = root / "component.tsx"
        source.write_text(changed)
        paths, _ = kern_cache.initialize(root)
        relative, normalized = kern_cache.normalize_rel(root, "component.tsx")

        result = kern_cache.verify_symbol(
            root,
            paths,
            relative,
            normalized,
            "Row",
            symbol.semantic8,
            f"L{symbol.span[0]}-{symbol.span[1]}",
        )

        self.assertEqual(result["result"], "stale")
        self.assertIs(result["ok"], False)
        self.assertEqual(result["reason"], "source-handle-changed")
        self.assertEqual(result["current_span"], "L1-4")


if __name__ == "__main__":
    unittest.main()
