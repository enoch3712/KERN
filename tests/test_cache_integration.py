import concurrent.futures
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "kern" / "scripts"))
import kern_cache  # noqa: E402
import kern_compile  # noqa: E402
import kern_react  # noqa: E402

BIG_PY = '"""Doc."""\n\nimport json\n\n' + "\n\n".join(
    f'def fn_{i}(path, n):\n'
    f'    """Doc {i}."""\n'
    f'    data = path.read_bytes()\n'
    f'    if not data:\n'
    f'        raise ValueError(n)\n'
    f'    return json.loads(data)\n'
    for i in range(30)
)


def write_render_artifacts(paths, relative, profile, input_sha):
    artifacts = kern_cache.artifact_paths(paths, relative)
    artifacts["images"].mkdir(parents=True, exist_ok=True)
    page = (artifacts["images"] / "page-001-of-001.webp").resolve()
    payload = b"RIFF-fake-lossless-webp"
    page.write_bytes(payload)
    metrics = {
        "schema": "kern-render/0.1",
        "input_sha256": input_sha,
        "profile": {"name": profile},
        "pages": [{"page": 1, "path": str(page), "bytes": len(payload)}],
        "page_count": 1,
        "bytes_total": len(payload),
    }
    kern_cache.atomic_json(artifacts["images"] / "metrics.json", metrics)
    return metrics


class TestCacheIntegration(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "big.py").write_text(BIG_PY)
        (self.root / "tiny.py").write_text("X = 1\n")
        self.paths, self.config = kern_cache.initialize(self.root)

    def ensure(self, name, tier=None):
        rel, src = kern_cache.normalize_rel(self.root, name)
        return kern_cache.ensure_file(self.root, self.paths, rel, src, self.config, tier=tier)

    def test_codec_is_0_2(self):
        self.assertEqual(kern_cache.CODEC_VERSION, "kern-il/0.2")

    def test_initialize_rejects_symlinked_cache_without_outside_writes(self):
        repo = self.root / "untrusted"
        outside = self.root / "outside-cache"
        repo.mkdir()
        outside.mkdir()
        (repo / ".kern").symlink_to(outside, target_is_directory=True)

        with self.assertRaisesRegex(ValueError, "Symlinked cache path"):
            kern_cache.initialize(repo)
        self.assertEqual(list(outside.iterdir()), [])

    def test_artifact_paths_reject_traversal(self):
        invalid = (
            "../outside.py",
            "pkg/../outside.py",
            "pkg//mod.py",
            "pkg/./mod.py",
            "/absolute.py",
            "",
        )
        for relative in invalid:
            with self.subTest(relative=relative):
                with self.assertRaisesRegex(ValueError, "Invalid cache artifact source path"):
                    kern_cache.artifact_paths(self.paths, relative)

    def test_manifest_rejects_unsafe_file_keys(self):
        manifest = json.loads(self.paths["manifest"].read_text())
        manifest["files"]["../outside.py"] = {"status": "missing"}
        kern_cache.atomic_json(self.paths["manifest"], manifest)
        with self.assertRaisesRegex(RuntimeError, "unsafe source path"):
            kern_cache.load_manifest(self.paths["manifest"], self.root)

    def test_ensure_rejects_nested_ir_symlink_before_outside_write(self):
        source = self.root / "pkg" / "mod.py"
        source.parent.mkdir()
        source.write_text(BIG_PY)
        outside = self.root / "outside-ir"
        outside.mkdir()
        (self.paths["ir"] / "pkg").symlink_to(outside, target_is_directory=True)
        rel, normalized = kern_cache.normalize_rel(self.root, "pkg/mod.py")

        with self.assertRaisesRegex(ValueError, "Symlinked cache path"):
            kern_cache.ensure_file(
                self.root, self.paths, rel, normalized, self.config
            )
        self.assertEqual(list(outside.iterdir()), [])
        manifest = json.loads(self.paths["manifest"].read_text())
        self.assertNotIn(rel, manifest["files"])

    def test_big_python_file_gets_deterministic_il(self):
        result = self.ensure("big.py")
        il = Path(result["ir"]).read_text()
        self.assertTrue(il.startswith("KERN-IL/0.2"))
        self.assertIn("tier=L2", il)
        self.assertIn("F fn_0(", il)
        self.assertIn("EFFECTS fs:read", il)
        self.assertIn("RAISES ValueError", il)

    def test_tier_override(self):
        result = self.ensure("big.py", tier="L1")
        il = Path(result["ir"]).read_text()
        self.assertIn("tier=L1", il)
        self.assertNotIn("    IF", il)
        manifest = json.loads((self.paths["manifest"]).read_text())
        self.assertEqual(manifest["files"]["big.py"]["ir_tier"], "L1")

    def test_tiny_file_gets_source_cheaper_stub(self):
        result = self.ensure("tiny.py")
        il = Path(result["ir"]).read_text()
        self.assertIn("mode=source-cheaper", il)
        self.assertNotIn("F ", il)

    def test_syntax_error_falls_back_to_generic(self):
        (self.root / "broken.py").write_text("def broken(:\n    pass\n" * 300)
        il = Path(self.ensure("broken.py")["ir"]).read_text()
        self.assertIn("mode=generic-line-baseline", il)

    def test_repo_revision_is_stable_and_ensure_runs_no_git_command(self):
        with mock.patch.object(
            kern_cache.subprocess, "run", side_effect=AssertionError("ensure must not invoke git")
        ):
            il = Path(self.ensure("big.py")["ir"]).read_text()
        self.assertIn("repo_revision=none", il)

    def test_crlf_bytes_are_hashed_without_newline_normalization(self):
        raw = BIG_PY.replace("\n", "\r\n").encode("utf-8")
        (self.root / "big.py").write_bytes(raw)
        result = self.ensure("big.py")
        expected = kern_cache.sha256_bytes(raw)
        self.assertEqual(result["source_sha256"], expected)
        self.assertIn(f"source_sha256={expected}", Path(result["ir"]).read_text())

    def test_default_tier_change_invalidates_cache(self):
        first = self.ensure("big.py")
        self.assertFalse(first["cache_hit"])
        self.assertTrue(self.ensure("big.py")["cache_hit"])
        self.config["default_tier"] = "L3"
        changed = self.ensure("big.py")
        self.assertFalse(changed["cache_hit"])
        self.assertEqual(changed["tier"], "L3")
        self.assertIn("tier=L3", Path(changed["ir"]).read_text())

    def test_parser_capability_version_change_invalidates_cache(self):
        source = "export function run(x) { return x + 1; }\n" * 100
        (self.root / "mod.js").write_text(source)
        capability = {"value": "tree-sitter=1;javascript=absent;enabled=none"}
        with mock.patch.object(
            kern_compile,
            "tsjs_capability_fingerprint",
            side_effect=lambda: capability["value"],
        ):
            first = self.ensure("mod.js")
            manifest = json.loads(self.paths["manifest"].read_text())
            first_fingerprint = manifest["files"]["mod.js"]["ir_compiler_fingerprint"]
            self.assertTrue(self.ensure("mod.js")["cache_hit"])
            capability["value"] = "tree-sitter=1;javascript=1;enabled=javascript"
            changed = self.ensure("mod.js")
            manifest = json.loads(self.paths["manifest"].read_text())
        self.assertFalse(changed["cache_hit"])
        self.assertNotEqual(
            first_fingerprint,
            manifest["files"]["mod.js"]["ir_compiler_fingerprint"],
        )

    def test_react_adapter_content_change_invalidates_tsjs_cache(self):
        if not kern_compile.tsjs_available(tsx=True):
            self.skipTest("tsx grammar not installed")
        self.config["min_ir_tokens"] = 1
        source = "export const Card = ({ title }) => <article>{title}</article>;\n"
        (self.root / "card.tsx").write_text(source)
        adapter = self.root / "kern_react.py"
        adapter.write_text("# adapter revision one\n")

        with mock.patch.object(kern_react, "__file__", str(adapter)):
            first = self.ensure("card.tsx")
            manifest = json.loads(self.paths["manifest"].read_text())
            first_fingerprint = manifest["files"]["card.tsx"]["ir_compiler_fingerprint"]
            self.assertFalse(first["cache_hit"])
            self.assertEqual(first["mode"], "structured:tree-sitter+react")
            self.assertTrue(self.ensure("card.tsx")["cache_hit"])

            adapter.write_text("# adapter revision two\n")
            changed = self.ensure("card.tsx")
            manifest = json.loads(self.paths["manifest"].read_text())

        self.assertFalse(changed["cache_hit"])
        self.assertNotEqual(
            first_fingerprint,
            manifest["files"]["card.tsx"]["ir_compiler_fingerprint"],
        )

    def test_react_adapter_fingerprint_tolerates_missing_and_unavailable_file(self):
        source = self.root / "card.tsx"
        source.write_text("export const Card = () => <article />;\n")
        adapter = self.root / "missing-kern-react.py"

        with mock.patch.object(kern_react, "__file__", str(adapter)):
            missing = kern_cache.compiler_fingerprint(
                source, self.config, "L2", "structured:tree-sitter+react"
            )

            adapter.write_text("# unreadable adapter\n")
            original_sha256_file = kern_cache.sha256_file

            def reject_adapter(path):
                if Path(path) == adapter:
                    raise PermissionError("adapter source is unreadable")
                return original_sha256_file(path)

            with mock.patch.object(kern_cache, "sha256_file", side_effect=reject_adapter):
                unavailable = kern_cache.compiler_fingerprint(
                    source, self.config, "L2", "structured:tree-sitter+react"
                )

        self.assertNotEqual(missing, unavailable)

    def test_pre_fingerprint_manifest_is_proactively_invalidated(self):
        self.ensure("big.py")
        manifest = json.loads(self.paths["manifest"].read_text())
        record = manifest["files"]["big.py"]
        manifest.pop("derivation_marker")
        record.pop("ir_compiler_fingerprint")
        record.pop("ir_derivation_marker")
        record["status"] = "ready"
        record["image_status"] = "ready"
        kern_cache.atomic_json(self.paths["manifest"], manifest)

        self.paths, self.config = kern_cache.initialize(self.root)
        migrated = json.loads(self.paths["manifest"].read_text())
        migrated_record = migrated["files"]["big.py"]
        self.assertEqual(migrated["derivation_marker"], kern_cache.DERIVATION_MARKER)
        self.assertEqual(migrated_record["status"], "stale")
        self.assertEqual(migrated_record["image_status"], "stale")

    def test_derivation_marker_is_part_of_compiler_fingerprint(self):
        source = self.root / "big.py"
        before = kern_cache.compiler_fingerprint(source, self.config, "L2", "structured:pyast")
        with mock.patch.object(kern_cache, "DERIVATION_MARKER", "kern-derivation/next"):
            after = kern_cache.compiler_fingerprint(source, self.config, "L2", "structured:pyast")
        self.assertNotEqual(before, after)

    def test_concurrent_tiers_leave_matching_artifact_and_metadata(self):
        original = kern_cache.baseline_for
        both_compiled = threading.Barrier(2)
        l3_committed = threading.Event()

        def coordinated(*args, **kwargs):
            result = original(*args, **kwargs)
            requested = kwargs.get("tier") if "tier" in kwargs else args[5]
            both_compiled.wait(timeout=5)
            if requested == "L1":
                self.assertTrue(l3_committed.wait(timeout=5))
            return result

        def run(tier):
            result = self.ensure("big.py", tier=tier)
            if tier == "L3":
                l3_committed.set()
            return result

        with mock.patch.object(kern_cache, "baseline_for", side_effect=coordinated):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(run, "L1"), pool.submit(run, "L3")]
                for future in futures:
                    future.result(timeout=10)

        manifest = json.loads(self.paths["manifest"].read_text())
        record = manifest["files"]["big.py"]
        ir_path = kern_cache.artifact_paths(self.paths, "big.py")["ir"]
        il = ir_path.read_text()
        self.assertEqual(record["ir_tier"], "L1")
        self.assertIn("tier=L1", il)
        self.assertEqual(record["ir_sha256"], kern_cache.sha256_file(ir_path))

    def test_render_rejects_ir_digest_change_during_render(self):
        self.ensure("big.py")
        rel, source = kern_cache.normalize_rel(self.root, "big.py")
        ir_path = kern_cache.artifact_paths(self.paths, rel)["ir"]
        pinned_sha = kern_cache.sha256_file(ir_path)

        def mutate_ir(*args, **kwargs):
            metrics = write_render_artifacts(self.paths, rel, "dense", pinned_sha)
            payload = ir_path.read_bytes() + b"# concurrent replacement\n"
            kern_cache.atomic_write(ir_path, payload)
            with kern_cache.CacheLock(self.paths["lock"]):
                manifest = kern_cache.load_manifest(self.paths["manifest"], self.root)
                manifest["files"][rel]["ir_sha256"] = kern_cache.sha256_bytes(payload)
                kern_cache.atomic_json(self.paths["manifest"], manifest)
            return kern_cache.subprocess.CompletedProcess(args[0], 0, json.dumps(metrics), "")

        with mock.patch.object(kern_cache.subprocess, "run", side_effect=mutate_ir):
            with self.assertRaisesRegex(RuntimeError, "IR changed"):
                kern_cache.render_file(self.root, self.paths, self.config, rel, source, "dense")
        manifest = json.loads(self.paths["manifest"].read_text())
        self.assertEqual(manifest["files"][rel]["image_status"], "stale")

    def test_render_failure_clears_rendering_state(self):
        self.ensure("big.py")
        rel, source = kern_cache.normalize_rel(self.root, "big.py")
        failed = kern_cache.subprocess.CompletedProcess([], 1, "", "renderer failed")
        with mock.patch.object(kern_cache.subprocess, "run", return_value=failed):
            with self.assertRaisesRegex(RuntimeError, "renderer failed"):
                kern_cache.render_file(self.root, self.paths, self.config, rel, source, "dense")
        manifest = json.loads(self.paths["manifest"].read_text())
        record = manifest["files"][rel]
        self.assertEqual(record["image_status"], "stale")
        self.assertNotIn("image_render_id", record)
        self.assertIn("renderer failed", record["image_error"])

    def test_render_rejects_symlinked_output_without_deleting_outside_files(self):
        self.ensure("big.py")
        rel, source = kern_cache.normalize_rel(self.root, "big.py")
        artifacts = kern_cache.artifact_paths(self.paths, rel)
        outside = self.root / "outside-render"
        outside.mkdir()
        page = outside / "page-001-of-001.webp"
        metrics = outside / "metrics.json"
        page.write_bytes(b"outside-page-must-survive")
        metrics.write_text('{"outside": true}\n')
        artifacts["images"].symlink_to(outside, target_is_directory=True)

        with mock.patch.object(
            kern_cache.subprocess, "run", side_effect=AssertionError("renderer must not run")
        ):
            with self.assertRaisesRegex(ValueError, "Symlinked cache path"):
                kern_cache.render_file(
                    self.root, self.paths, self.config, rel, source, "dense"
                )
        self.assertEqual(page.read_bytes(), b"outside-page-must-survive")
        self.assertEqual(metrics.read_text(), '{"outside": true}\n')

    def test_render_finalization_failure_clears_rendering_state(self):
        self.ensure("big.py")
        rel, source = kern_cache.normalize_rel(self.root, "big.py")
        pinned_sha = kern_cache.sha256_file(kern_cache.artifact_paths(self.paths, rel)["ir"])

        def delete_source(args, **kwargs):
            metrics = write_render_artifacts(self.paths, rel, "dense", pinned_sha)
            source.unlink()
            return kern_cache.subprocess.CompletedProcess(args, 0, json.dumps(metrics), "")

        with mock.patch.object(kern_cache.subprocess, "run", side_effect=delete_source):
            with self.assertRaises(FileNotFoundError):
                kern_cache.render_file(self.root, self.paths, self.config, rel, source, "dense")
        manifest = json.loads(self.paths["manifest"].read_text())
        record = manifest["files"][rel]
        self.assertEqual(record["image_status"], "stale")
        self.assertNotIn("image_render_id", record)
        self.assertIn("No such file", record["image_error"])

    def test_render_rejects_non_object_metrics_without_artifacts(self):
        self.ensure("big.py")
        rel, source = kern_cache.normalize_rel(self.root, "big.py")
        invalid = kern_cache.subprocess.CompletedProcess([], 0, "[]", "")
        with mock.patch.object(kern_cache.subprocess, "run", return_value=invalid):
            with self.assertRaisesRegex(RuntimeError, "metrics must be a JSON object"):
                kern_cache.render_file(self.root, self.paths, self.config, rel, source, "dense")
        manifest = json.loads(self.paths["manifest"].read_text())
        record = manifest["files"][rel]
        self.assertEqual(record["image_status"], "stale")
        self.assertNotIn("image_render_id", record)

    def test_render_rejects_declared_pages_when_artifacts_are_missing(self):
        self.ensure("big.py")
        rel, source = kern_cache.normalize_rel(self.root, "big.py")
        artifacts = kern_cache.artifact_paths(self.paths, rel)
        pinned_sha = kern_cache.sha256_file(artifacts["ir"])
        missing_page = (artifacts["images"] / "page-001-of-001.webp").resolve()
        metrics = {
            "schema": "kern-render/0.1",
            "input_sha256": pinned_sha,
            "profile": {"name": "dense"},
            "pages": [{"page": 1, "path": str(missing_page), "bytes": 10}],
            "page_count": 1,
            "bytes_total": 10,
        }
        completed = kern_cache.subprocess.CompletedProcess([], 0, json.dumps(metrics), "")
        with mock.patch.object(kern_cache.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(RuntimeError, "metrics.json artifact"):
                kern_cache.render_file(self.root, self.paths, self.config, rel, source, "dense")
        manifest = json.loads(self.paths["manifest"].read_text())
        record = manifest["files"][rel]
        self.assertEqual(record["image_status"], "stale")
        self.assertNotIn("image_render_id", record)

    def test_render_rejects_missing_and_stale_compiler_fingerprints(self):
        self.ensure("big.py")
        rel, source = kern_cache.normalize_rel(self.root, "big.py")
        manifest = json.loads(self.paths["manifest"].read_text())
        manifest["files"][rel].pop("ir_compiler_fingerprint")
        kern_cache.atomic_json(self.paths["manifest"], manifest)
        with mock.patch.object(
            kern_cache.subprocess, "run", side_effect=AssertionError("renderer must not run")
        ):
            with self.assertRaisesRegex(RuntimeError, "compiler derivation"):
                kern_cache.render_file(self.root, self.paths, self.config, rel, source, "dense")

        self.ensure("big.py")
        self.config["default_tier"] = "L3"
        with mock.patch.object(
            kern_cache.subprocess, "run", side_effect=AssertionError("renderer must not run")
        ):
            with self.assertRaisesRegex(RuntimeError, "compiler derivation"):
                kern_cache.render_file(self.root, self.paths, self.config, rel, source, "dense")

    def test_render_profiles_are_serialized_per_file(self):
        self.ensure("big.py")
        rel, source = kern_cache.normalize_rel(self.root, "big.py")
        state_lock = threading.Lock()
        state = {"active": 0, "max_active": 0}

        def render_stub(args, **kwargs):
            with state_lock:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
            time.sleep(0.05)
            profile = args[args.index("--profile") + 1]
            ir_path = kern_cache.artifact_paths(self.paths, rel)["ir"]
            metrics = write_render_artifacts(
                self.paths, rel, profile, kern_cache.sha256_file(ir_path)
            )
            with state_lock:
                state["active"] -= 1
            return kern_cache.subprocess.CompletedProcess(args, 0, json.dumps(metrics), "")

        with mock.patch.object(kern_cache.subprocess, "run", side_effect=render_stub):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(kern_cache.render_file, self.root, self.paths, self.config, rel, source, "dense"),
                    pool.submit(kern_cache.render_file, self.root, self.paths, self.config, rel, source, "safe"),
                ]
                for future in futures:
                    future.result(timeout=10)
        self.assertEqual(state["max_active"], 1)

    @unittest.skipUnless(
        kern_compile.tsjs_available(typescript=True), "TypeScript grammar not installed"
    )
    def test_verify_accepts_emitted_addressable_declarations(self):
        source_text = (
            "namespace API { export function run() { return 1; } }\n"
            "interface User { id: string; }\n"
            "enum State { Ready, Done }\n"
            "console.log(State.Ready);\n"
        )
        source = self.root / "decls.ts"
        source.write_text(source_text)
        module = kern_compile.apply_semantic_handles(
            kern_compile.parse_tsjs(source_text, typescript=True)
        )
        expected = {
            "namespace": "API",
            "type": "User",
            "enum": "State",
            "module": "<module-op>",
        }
        for kind, name in expected.items():
            with self.subTest(kind=kind, name=name):
                symbol = next(s for s in module.symbols if s.kind == kind and s.name == name)
                result = kern_cache.verify_symbol(
                    self.root,
                    self.paths,
                    "decls.ts",
                    source,
                    name,
                    symbol.semantic8,
                    f"L{symbol.span[0]}-{symbol.span[1]}",
                )
                self.assertEqual(result["result"], "ok")

    def test_generic_ir_line_numbers_use_newline_math(self):
        # A form feed (\x0c) is a line boundary for str.splitlines() but not for
        # a plain "\n" split; generic_ir's line numbers must match the latter
        # (the same \n-only math fault_source uses) or "N|" refs point at the
        # wrong source line whenever a file contains such control characters.
        content = (
            "#!/bin/sh\n# \x0c\n"
            + ("echo filler\n" * 400)
            + "if true; then\n  echo a\nfi\n"
        )
        (self.root / "notpython.sh").write_text(content)
        il = Path(self.ensure("notpython.sh")["ir"]).read_text()
        self.assertIn("mode=generic-line-baseline", il)
        line_refs = [l for l in il.splitlines() if l and l[0].isdigit()]
        ref = next(l for l in line_refs if "if true" in l)
        n = int(ref.split("|")[0])
        raw_lines = (self.root / "notpython.sh").read_text().split("\n")
        self.assertIn("if true", raw_lines[n - 1])


if __name__ == "__main__":
    unittest.main()
