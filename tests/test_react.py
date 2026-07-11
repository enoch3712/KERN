import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "kern" / "scripts"))
import kern_compile  # noqa: E402

TSX_SAMPLE = '''
import { useState, useEffect } from "react";
import { Card, Avatar, UserDetails } from "./ui";

export function UserCard({ user, onClose = noop }) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    analytics.track("view_user", user.id);
  }, [user.id]);

  return (
    <Card onClick={() => setOpen(true)}>
      <Avatar src={user.avatar} />
      <span>{user.name}</span>
      {open && <UserDetails user={user} />}
    </Card>
  );
}

export function formatName(name: string): string {
  return name.trim();
}
'''


@unittest.skipUnless(kern_compile.tsjs_available(), "tree-sitter not installed")
class TestDialectRouting(unittest.TestCase):
    def test_tsx_dialect_parses_jsx_clean(self):
        mod = kern_compile.parse_tsjs(TSX_SAMPLE, dialect="tsx")
        self.assertEqual(mod.parse_error, "")
        self.assertEqual(mod.lang, "tsx")

    def test_plain_ts_dialect_chokes_on_jsx(self):
        # Documents the routed-around limitation: TS grammar has no JSX.
        mod = kern_compile.parse_tsjs(TSX_SAMPLE, dialect="ts")
        self.assertNotEqual(mod.parse_error, "")

    def test_js_dialect_still_default(self):
        mod = kern_compile.parse_tsjs("function f() { return 1; }\n")
        self.assertEqual(mod.lang, "javascript")


if __name__ == "__main__":
    unittest.main()
