import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "kern" / "scripts"))
import render_ir  # noqa: E402


class TestRenderContainment(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.cache = self.base / "repo" / ".kern"
        self.cache.mkdir(parents=True)
        self.outside = self.base / "outside"
        self.outside.mkdir()

    def test_output_symlink_is_rejected_without_external_deletion(self):
        images = self.cache / "images"
        images.mkdir()
        output = images / "pkg"
        output.symlink_to(self.outside, target_is_directory=True)
        page = self.outside / "page-001-of-001.webp"
        metrics = self.outside / "metrics.json"
        page.write_bytes(b"outside-page")
        metrics.write_text('{"outside": true}\n')

        with self.assertRaisesRegex(ValueError, "Symlinked render output path"):
            render_ir.safe_clear_output(output, self.cache)
        self.assertEqual(page.read_bytes(), b"outside-page")
        self.assertEqual(metrics.read_text(), '{"outside": true}\n')

    def test_output_ancestor_symlink_is_rejected(self):
        images = self.cache / "images"
        images.symlink_to(self.outside, target_is_directory=True)
        (self.outside / "pkg").mkdir()
        page = self.outside / "pkg" / "page-001-of-001.webp"
        page.write_bytes(b"outside-page")

        with self.assertRaisesRegex(ValueError, "Symlinked render output path"):
            render_ir.safe_clear_output(images / "pkg", self.cache)
        self.assertEqual(page.read_bytes(), b"outside-page")

    def test_cleanup_preflights_every_candidate_before_deleting(self):
        output = self.cache / "images" / "pkg"
        output.mkdir(parents=True)
        regular = output / "page-001-of-002.webp"
        regular.write_bytes(b"ordinary-stale-page")
        external_target = self.outside / "external.webp"
        external_target.write_bytes(b"outside-page")
        linked = output / "page-002-of-002.webp"
        linked.symlink_to(external_target)

        with self.assertRaisesRegex(ValueError, "Symlinked render output path"):
            render_ir.safe_clear_output(output, self.cache)
        self.assertEqual(regular.read_bytes(), b"ordinary-stale-page")
        self.assertTrue(linked.is_symlink())
        self.assertEqual(external_target.read_bytes(), b"outside-page")

    def test_output_outside_cache_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "escapes cache root"):
            render_ir.safe_clear_output(self.outside, self.cache)
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_atomic_json_does_not_follow_precreated_temp_symlink(self):
        output = self.cache / "images" / "pkg"
        output.mkdir(parents=True)
        target = self.outside / "victim.json"
        target.write_text("do not overwrite\n")
        destination = output / "metrics.json"
        temporary = output / f".metrics.json.{render_ir.os.getpid()}.fixed.tmp"
        temporary.symlink_to(target)

        with mock.patch.object(
            render_ir.uuid, "uuid4", return_value=SimpleNamespace(hex="fixed")
        ):
            with self.assertRaises(FileExistsError):
                render_ir.atomic_json(destination, {"safe": True})
        self.assertEqual(target.read_text(), "do not overwrite\n")
        self.assertTrue(temporary.is_symlink())
        self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
