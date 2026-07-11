import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "kern" / "scripts"))
import kern_compile  # noqa: E402


@unittest.skipUnless(kern_compile.tsjs_available(tsx=True), "tsx grammar not installed")
class TestReactAdversarial(unittest.TestCase):
    def parse(self, source):
        return kern_compile.parse_tsjs(source, tsx=True)

    def il(self, source, tier="L3"):
        module = self.parse(source)
        return kern_compile.emit_il(
            module, "app/T.tsx", "c" * 64, "none", tier,
        )

    def test_compound_and_nested_prop_defaults_are_structurally_redacted(self):
        source = (
            'function Login({ config: { apiToken = String("prop-secret"), '
            'label = "visible" }, password: local = make("nested-secret") }) {'
            ' return <div/>; }'
        )
        for tier in ("L1", "L2", "L3"):
            rendered = self.il(source, tier)
            self.assertNotIn("prop-secret", rendered)
            self.assertNotIn("nested-secret", rendered)
        self.assertIn('label = "visible"', self.il(source))

    def test_compound_default_does_not_reintroduce_secret_literal_type(self):
        source = (
            'function Login({ apiToken = String("default-secret"), password }:'
            ' { apiToken?: string; password: "type-secret" }) {'
            ' return <div/>; }'
        )
        rendered = self.il(source, "L1")
        self.assertNotIn("default-secret", rendered)
        self.assertNotIn("type-secret", rendered)

    def test_aliased_prop_preserves_generic_secret_redaction(self):
        source = (
            'function Login({ label: local = "sk-abcdefghijklmnop", '
            'value: other = getSecret("plain-secret") }) {'
            ' return <div/>; }'
        )
        rendered = self.il(source)
        self.assertNotIn("sk-abcdefghijklmnop", rendered)
        self.assertNotIn("plain-secret", rendered)

    def test_secret_event_attr_and_nested_setter_argument_do_not_leak(self):
        source = (
            'function T(){ const [password, setPassword] = useState("");'
            ' return <Input onToken="event-secret" '
            'onClick={() => setPassword(make("setter-secret"))}/>; }'
        )
        rendered = self.il(source)
        self.assertNotIn("event-secret", rendered)
        self.assertNotIn("setter-secret", rendered)
        self.assertIn("EVENT Input.onToken -> <REDACTED", rendered)
        self.assertIn("EVENT Input.onClick -> set password=<REDACTED", rendered)

    def test_secret_flow_bindings_are_redacted_in_component_and_effect(self):
        source = (
            'function T(){ const apiToken = identity("component-secret");'
            ' useEffect(() => { const password = identity("effect-secret");'
            ' save(password); }, []); return <X/>; }'
        )
        rendered = self.il(source)
        self.assertNotIn("component-secret", rendered)
        self.assertNotIn("effect-secret", rendered)
        self.assertIn("-> apiToken", rendered)
        self.assertIn("-> password", rendered)

    def test_l1_keeps_react_fault_in_head_and_footer(self):
        rendered = self.il("function T(){ return <Foo.Bar/>; }", "L1")
        self.assertIn("COMPONENT T()", rendered)
        self.assertIn("!FAULT(dynamic-component)", rendered)
        self.assertIn("dynamic-component(L1)", rendered)

    def test_l2_keeps_inherited_flow_risk(self):
        source = "function T({pattern}){ const re = RegExp(pattern); return <X/>; }"
        rendered = self.il(source, "L2")
        self.assertIn("CALL !FAULT(regex)", rendered)
        self.assertIn("regex(L1)", rendered)

    def test_l2_keeps_effect_risk_without_emitting_plain_body_flow(self):
        source = (
            "function T({value}){ useEffect(() => value.match(/a+/), []);"
            " return <X/>; }"
        )
        rendered = self.il(source, "L2")
        self.assertIn("CALL !FAULT(regex)", rendered)
        self.assertIn("regex(L1)", rendered)
        self.assertNotIn("\n    RET", rendered)

    def test_hook_argument_risks_survive_all_tiers(self):
        source = (
            "function T({pattern}){ const [re] = useState(RegExp(pattern));"
            " return <X/>; }"
        )
        for tier in ("L1", "L2", "L3"):
            rendered = self.il(source, tier)
            self.assertIn("!FAULT(regex)", rendered)
            self.assertIn("regex(L1)", rendered)

    def test_l1_keeps_effect_body_risk(self):
        source = (
            "function T({pattern}){ useEffect(() => RegExp(pattern), []);"
            " return <X/>; }"
        )
        rendered = self.il(source, "L1")
        self.assertIn("!FAULT(regex)", rendered)
        self.assertIn("regex(L1)", rendered)

    def test_conditional_hook_in_declaration_initializer_is_faulted(self):
        source = (
            "function T({ready}){ const state = ready ? useState(0) : null;"
            " return <X/>; }"
        )
        rendered = self.il(source, "L1")
        self.assertIn("!FAULT(conditional-hook)", rendered)
        self.assertIn("conditional-hook(L1)", rendered)

    def test_nested_conditional_hook_shapes_are_faulted(self):
        cases = (
            "let state; state = ready ? useState(0) : null;",
            "const state = identity(ready ? useState(0) : null);",
            "const view = <div>{ready && useState(0)}</div>;",
        )
        for body in cases:
            with self.subTest(body=body):
                source = f"function T({{ready}}){{ {body} return <X/>; }}"
                rendered = self.il(source, "L1")
                self.assertIn("!FAULT(conditional-hook)", rendered)

    def test_renamed_react_hook_imports_are_lowered_and_faulted(self):
        source = (
            'import { useState as useS, useEffect as useE } from "react";'
            " function T(){ const [value, setValue] = useS(0);"
            " useE(() => tick(), []); return <X/>; }"
        )
        rendered = self.il(source)
        self.assertIn("STATE value=0 !FAULT(aliased-hook)", rendered)
        self.assertIn("EFFECT deps=[] !FAULT(aliased-hook)", rendered)
        self.assertIn("CALL tick()", rendered)
        self.assertEqual(rendered.count("CALL useS"), 0)

    def test_renamed_hook_alias_does_not_capture_member_or_shadowed_binding(self):
        source = (
            'import { useState as useS } from "react";'
            " function T({useS}){ const a = obj.useS(0);"
            " const b = useS(1); return <X/>; }"
        )
        rendered = self.il(source)
        self.assertNotIn("STATE a=", rendered)
        self.assertNotIn("STATE b=", rendered)
        self.assertIn("CALL obj.useS(0)", rendered)
        self.assertIn("CALL useS(1)", rendered)

    def test_generic_renamed_hook_is_not_duplicated_as_flow(self):
        source = (
            'import { useState as useS } from "react";'
            " function T(){ const [value] = useS<number>(0); return <X/>; }"
        )
        rendered = self.il(source)
        self.assertIn("STATE value=0", rendered)
        self.assertNotIn("CALL useS<number>", rendered)

    def test_function_typed_generic_hook_is_not_duplicated_as_flow(self):
        rendered = self.il(
            "function T(){ const cb = useCallback<() => void>(() => {});"
            " return <X/>; }"
        )
        self.assertIn("HOOK cb=useCallback<() => void>", rendered)
        self.assertNotIn("CALL useCallback<() => void>", rendered)

    def test_logical_assignment_and_optional_hooks_are_conditional(self):
        for operator in ("&&=", "||=", "??="):
            with self.subTest(operator=operator):
                rendered = self.il(
                    f"function T({{ready}}){{ ready {operator} useState(0);"
                    " return <X/>; }",
                    "L1",
                )
                self.assertIn("!FAULT(conditional-hook)", rendered)
        rendered = self.il("function T(){ useMaybe?.(0); return <X/>; }", "L1")
        self.assertIn("!FAULT(conditional-hook)", rendered)

    def test_awaited_hook_call_is_not_silently_suppressed(self):
        rendered = self.il(
            "async function T(){ await useData(); return <X/>; }",
            "L3",
        )
        self.assertIn("CALL await useData()", rendered)

    def test_unguarded_conditional_operands_are_not_faulted(self):
        sources = (
            "function T(){ const value = useReady() && true; return <X/>; }",
            "function T(){ const value = useReady() ? 1 : 2; return <X/>; }",
            ("function T({registry, maybe}){ const value = "
             "registry[maybe?.name].useState(0); return <X/>; }"),
        )
        for source in sources:
            with self.subTest(source=source):
                self.assertNotIn("conditional-hook", self.il(source, "L1"))

    def test_parentheses_terminate_optional_chain_short_circuiting(self):
        source = "function T(){ (React?.hooks).useState(0); return <X/>; }"
        self.assertNotIn("conditional-hook", self.il(source, "L1"))

    def test_expression_bodied_effect_callback_is_lowered(self):
        source = (
            'function T(){ useEffect(() => analytics.track("view"), []);'
            " return <X/>; }"
        )
        self.assertIn('CALL analytics.track("view")', self.il(source))

    def test_expression_bodied_cleanup_callback_is_lowered(self):
        source = (
            "function T(){ useEffect(() => () => cleanup(), []);"
            " return <X/>; }"
        )
        self.assertIn("CALL cleanup()", self.il(source))

    def test_wrapped_effect_callback_is_lowered(self):
        source = (
            "function T(){ useEffect((() => tick()) as EffectCallback, []);"
            " return <X/>; }"
        )
        self.assertIn("CALL tick()", self.il(source))

    def test_block_bodied_map_callback_return_is_lowered(self):
        source = (
            "function T({items}){ return <div>{items.map(item => {"
            " return <Row key={item.id}/>; })}</div>; }"
        )
        rendered = self.il(source)
        self.assertIn("FOR item in items", rendered)
        self.assertIn("Row key={item.id}", rendered)

    def test_guarded_block_map_is_faulted(self):
        source = (
            "function T({items}){ return <div>{items.map(item => {"
            " if (!item) return null; return <Row/>; })}</div>; }"
        )
        rendered = self.il(source, "L2")
        self.assertIn("FOR item in items", rendered)
        self.assertIn("> Row", rendered)
        self.assertIn("!FAULT(render-control-flow)", rendered)

    def test_secret_named_map_binding_redacts_receiver_and_raw_flow(self):
        source = (
            'function T(){ return <div>{["map-secret"].map('
            "password => <X/>)}</div>; }"
        )
        for tier in ("L2", "L3"):
            rendered = self.il(source, tier)
            self.assertNotIn("map-secret", rendered)
            self.assertIn("FOR password in <REDACTED", rendered)

    def test_typed_map_callback_redacts_secret_receiver(self):
        source = (
            'function T(){ return <div>{["quartz-velvet-7319"].map('
            "((password => <X/>) as Mapper))}</div>; }"
        )
        rendered = self.il(source, "L3")
        self.assertNotIn("quartz-velvet-7319", rendered)
        self.assertIn("FOR password in <REDACTED", rendered)

    def test_map_this_arg_is_not_treated_as_render_callback(self):
        source = (
            "function T({items, renderItem}){ return <div>{items.map("
            "renderItem, () => <Fake/>)}</div>; }"
        )
        rendered = self.il(source, "L3")
        self.assertNotIn("FOR _ in items", rendered)
        self.assertNotIn("\n      Fake", rendered)

    def test_long_structural_secret_values_are_redacted_before_text_cap(self):
        secret = "quartzvelvet" * 20
        sources = (
            f'function T(){{ return <X apiToken="{secret}" />; }}',
            (f'function T(){{ return <div>{{["{secret}"].map('
             "apiToken => <X/>)}</div>; }"),
        )
        for source in sources:
            with self.subTest(source=source[:40]):
                self.assertNotIn(secret[:64], self.il(source, "L3"))

    def test_ts_as_and_satisfies_wrappers_are_components(self):
        module = self.parse(
            "const A = () => (<X/> as JSX.Element);"
            " const B = () => <Y/> satisfies ReactNode;"
        )
        kinds = {symbol.name: symbol.kind for symbol in module.symbols}
        self.assertEqual(kinds["A"], "component")
        self.assertEqual(kinds["B"], "component")

    def test_ts_non_null_wrapper_is_component(self):
        module = self.parse("const A = () => (<X/>!);")
        self.assertEqual(next(symbol for symbol in module.symbols if symbol.name == "A").kind,
                         "component")

    def test_class_method_and_nested_generator_do_not_upgrade(self):
        source = (
            "class View { Card(){ return <X/>; } }"
            " function Outer(){ const Inner = function*(){ return <Y/>; };"
            " return 2; }"
        )
        module = self.parse(source)
        kinds = {symbol.name: symbol.kind for symbol in module.symbols}
        self.assertEqual(kinds["View.Card"], "function")
        self.assertEqual(kinds["Outer"], "function")
        self.assertNotIn("component", kinds.values())

    def test_generator_function_is_not_a_component(self):
        module = self.parse("function* Feed(){ return <X/>; }")
        self.assertEqual(next(symbol for symbol in module.symbols if symbol.name == "Feed").kind,
                         "function")

    def test_secret_named_object_method_body_is_redacted(self):
        source = (
            'function T(){ return <X config={{ password(){ return "method-secret"; } }} />; }'
        )
        rendered = self.il(source)
        self.assertNotIn("method-secret", rendered)

    def test_secret_named_method_signature_literals_are_redacted(self):
        methods = {
            "param-secret": 'password(value="param-secret") { return value; }',
            "type-secret": 'password(): "type-secret" { return value; }',
            "generic-secret": 'password<T="generic-secret">() { return value; }',
        }
        for secret, method in methods.items():
            with self.subTest(secret=secret):
                source = f"function T(){{ return <X config={{{{ {method} }}}} />; }}"
                self.assertNotIn(secret, self.il(source, "L3"))

    def test_secret_named_class_method_metadata_and_flow_are_redacted(self):
        source = (
            'class C { password(value="param-secret"): "type-secret" {'
            ' throw "raise-secret"; return "body-secret"; } }'
        )
        module = kern_compile.parse_tsjs(source, typescript=True)
        rendered = kern_compile.emit_il(
            module, "app/C.ts", "c" * 64, "none", "L3",
        )
        for secret in ("param-secret", "type-secret", "raise-secret", "body-secret"):
            self.assertNotIn(secret, rendered)

    def test_secret_named_type_method_signature_is_redacted(self):
        secret = "quartz-velvet-7319"
        source = f'type Config = {{ password(): "{secret}" }};'
        module = kern_compile.parse_tsjs(source, typescript=True)
        rendered = kern_compile.emit_il(
            module, "app/Config.ts", "c" * 64, "none", "L3",
        )
        self.assertNotIn(secret, rendered)

    def test_secret_named_method_call_metadata_is_redacted(self):
        secret = "quartz-velvet-7319"
        source = f'class C {{ password() {{ client["{secret}"](); }} }}'
        module = kern_compile.parse_tsjs(source, typescript=True)
        for tier in ("L1", "L2", "L3"):
            with self.subTest(tier=tier):
                rendered = kern_compile.emit_il(
                    module, "app/C.ts", "c" * 64, "none", tier,
                )
                self.assertNotIn(secret, rendered)

    def test_render_prop_attribute_is_faulted(self):
        source = "function T(){ return <Data render={() => <X/>}/>; }"
        for tier in ("L1", "L2", "L3"):
            rendered = self.il(source, tier)
            self.assertIn("!FAULT(render-prop)", rendered)
            self.assertIn("render-prop(L1)", rendered)

    def test_guard_returns_are_explicitly_faulted(self):
        source = "function T({ready}){ if (ready) return <A/>; return <B/>; }"
        for tier in ("L1", "L2", "L3"):
            rendered = self.il(source, tier)
            self.assertIn("render-control-flow", rendered)

    def test_output_is_deterministic(self):
        source = "function T({ok}){ return ok ? <A/> : <B/>; }"
        self.assertEqual(self.il(source), self.il(source))


if __name__ == "__main__":
    unittest.main()
