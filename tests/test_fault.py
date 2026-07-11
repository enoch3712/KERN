import sys
import unittest
from pathlib import Path
import shutil
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "kern" / "scripts"))
import kern_cache  # noqa: E402


class TestFaultSource(unittest.TestCase):
    def fault_body(self, content, start, end):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        f = tmp / "s.py"
        f.write_bytes(content)
        out = kern_cache.fault_source(f, "s.py", start, end, None)
        return out.split("--- SOURCE ---\n", 1)[1]

    def test_range_ending_on_blank_line_is_byte_exact(self):
        self.assertEqual(self.fault_body(b"a = 1\n\n\nb = 2\n", 1, 3), "a = 1\n\n\n")

    def test_single_blank_line_file(self):
        self.assertEqual(self.fault_body(b"\n", 1, 1), "\n")

    def test_full_file_roundtrip(self):
        content = b"x = 1\ny = 2\n\nz = 3\n"
        self.assertEqual(self.fault_body(content, 1, 4), content.decode())

    def test_crlf_preserved(self):
        self.assertEqual(self.fault_body(b"a = 1\r\nb = 2\r\n", 1, 1), "a = 1\r\n")


if __name__ == "__main__":
    unittest.main()
