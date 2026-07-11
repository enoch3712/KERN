import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "kern" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import kern_cache  # noqa: E402

KERN_CACHE = SCRIPTS / "kern_cache.py"

BIG_PY = '"""Doc."""\n\nimport json\n\n' + "\n\n".join(
    f'def fn_{i}(path, n):\n'
    f'    """Doc {i}."""\n'
    f'    data = path.read_bytes()\n'
    f'    if not data:\n'
    f'        raise ValueError(n)\n'
    f'    return json.loads(data)\n'
    for i in range(30)
)


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(KERN_CACHE), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


class TestOperationLogging(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "big.py").write_text(BIG_PY)

    def log_lines(self):
        log_path = self.root / ".kern" / "log.jsonl"
        self.assertTrue(log_path.is_file(), "expected .kern/log.jsonl to exist")
        return [line for line in log_path.read_text().splitlines() if line.strip()]

    def test_ensure_writes_log_entry(self):
        result = run_cli("--repo", str(self.root), "ensure", "big.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = self.log_lines()
        self.assertGreaterEqual(len(lines), 1)
        entry = json.loads(lines[-1])
        self.assertEqual(entry["op"], "ensure")
        self.assertIs(entry["ok"], True)
        self.assertEqual(entry["source_rel"], "big.py")
        self.assertIsInstance(entry["duration_ms"], int)

    def test_error_logged(self):
        result = run_cli("--repo", str(self.root), "ensure", "does-not-exist.py")
        self.assertEqual(result.returncode, 2)
        lines = self.log_lines()
        entry = json.loads(lines[-1])
        self.assertIs(entry["ok"], False)
        self.assertIn("error", entry)
        self.assertTrue(entry["error"])

    def test_error_log_never_contains_credential_shaped_content(self):
        ensure_result = run_cli("--repo", str(self.root), "ensure", "big.py")
        self.assertEqual(ensure_result.returncode, 0, ensure_result.stderr)
        ensured = json.loads(ensure_result.stdout)
        baseline = Path(ensured["ir"]).read_text()
        source_sha = ensured["source_sha256"]

        staging = self.root / "staging.kern-il.txt"
        staging.write_text(baseline + "\nENRICHMENT model=m\nDB_PASSWORD=hunter2secretvalue\n")

        commit_result = run_cli(
            "--repo", str(self.root), "commit", "big.py",
            "--ir-file", str(staging), "--source-sha", source_sha,
        )
        self.assertEqual(commit_result.returncode, 2)
        self.assertNotIn("hunter2secretvalue", commit_result.stderr)

        log_contents = (self.root / ".kern" / "log.jsonl").read_text()
        self.assertNotIn("hunter2secretvalue", log_contents)

    def test_redact_line_scrubs_value_without_delimiter(self):
        out = kern_cache.redact_line("push rejected using ghp_ABCDEFGHIJKLMNOP12345 by client")
        self.assertNotIn("ghp_ABCDEFGHIJKLMNOP12345", out)

    def test_redact_line_catches_bearer_header(self):
        out = kern_cache.redact_line("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig")
        self.assertNotIn("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", out)
        self.assertIn("REDACTED", out)


class TestLogCommand(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.paths, _ = kern_cache.initialize(self.root)

    def seed_log(self, n=30):
        ops = ["ensure", "scan", "status", "render", "commit"]
        lines = []
        for i in range(n):
            op = ops[i % len(ops)]
            entry = {
                "ts": f"2026-01-01T00:00:{i:02d}Z",
                "op": op,
                "ok": True,
                "duration_ms": i,
                "source_rel": f"file_{i}.py",
                "status": "ready",
            }
            lines.append(json.dumps(entry, sort_keys=True))
        lines.append("{this is not valid json")
        self.paths["log"].write_text("\n".join(lines) + "\n")

    def test_log_tail_and_filter(self):
        self.seed_log()
        result = run_cli("--repo", str(self.root), "log", "--tail", "5", "--op", "ensure", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 5)
        for line in lines:
            entry = json.loads(line)
            self.assertEqual(entry["op"], "ensure")

    def test_log_event_does_not_raise_on_non_serializable_field(self):
        # A set is not JSON serializable; log_event must swallow the resulting
        # TypeError rather than letting it abort the command.
        try:
            kern_cache.log_event(
                self.paths, {"ts": "2026-01-01T00:00:00Z", "op": "ensure", "ok": True, "weird": {1, 2, 3}}
            )
        except Exception as exc:  # pragma: no cover - failure path
            self.fail(f"log_event raised unexpectedly: {exc!r}")

    def test_read_log_entries_tail_zero_is_empty(self):
        self.seed_log()
        entries = kern_cache.read_log_entries(self.paths, tail=0, op_filter=None)
        self.assertEqual(entries, [])

    def test_log_human_table_and_empty(self):
        result = run_cli("--repo", str(self.root), "log")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no log entries", result.stdout.lower())

        self.seed_log()
        result2 = run_cli("--repo", str(self.root), "log", "--tail", "10")
        self.assertEqual(result2.returncode, 0, result2.stderr)
        self.assertIn("ensure", result2.stdout)
        self.assertIn("file_", result2.stdout)


if __name__ == "__main__":
    unittest.main()
