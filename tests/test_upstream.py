"""Tests for scripts/check_upstream.py, the check against new Codex and Claude Code releases."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import check_upstream as cu

from peer.models import claude_efforts_from_help
from peer.runner import CODEX_RISKY_FEATURES, claude_command, parse_feature_rows

FEATURES = """\
apps                         stable             true
hooks                        stable             true
memories                     stable             false
shell_tool                   stable             true
"""


class CheckUpstreamTest(unittest.TestCase):
    def test_flags_of_keeps_option_names_only(self):
        cmd = ["codex", "exec", "-C", "/x", "--disable", "apps", "-c", "k=v", "--disable", "hooks", "-"]
        self.assertEqual(cu.flags_of(cmd), ["-C", "--disable", "-c"])

    def test_missing_flags_matches_whole_names(self):
        help_text = "  -o, --output-last-message <FILE>\n      --tools-extra\n"
        self.assertEqual(cu.missing_flags(["-o", "--output-last-message", "--tools"], help_text), ["--tools"])

    def test_claude_flags_found_in_help(self):
        cmd = claude_command("claude", "m", "e", True, web=True)
        help_text = " ".join(cu.flags_of(cmd))
        self.assertEqual(cu.missing_flags(cu.flags_of(cmd), help_text), [])

    def test_feature_rows_read_three_columns(self):
        rows = parse_feature_rows(FEATURES + "Feature list\n")
        self.assertEqual([r[0] for r in rows], ["apps", "hooks", "memories", "shell_tool"])

    def test_new_enabled_feature_fails(self):
        rows = parse_feature_rows(FEATURES)
        failures, _ = cu.feature_problems(rows, reviewed=set())
        self.assertEqual(len(failures), 1)
        self.assertIn("shell_tool", failures[0])
        self.assertNotIn("memories", failures[0])  # off by default

    def test_reviewed_feature_passes_and_absent_risky_feature_is_a_note(self):
        failures, notes = cu.feature_problems(parse_feature_rows(FEATURES), reviewed={"shell_tool"})
        self.assertEqual(failures, [])
        self.assertIn("browser_use", notes[0])

    def test_no_rows_fails(self):
        failures, _ = cu.feature_problems([], reviewed=set())
        self.assertTrue(failures)

    def test_reviewed_file_has_no_risky_features(self):
        reviewed = cu.reviewed_features()
        self.assertIn("shell_tool", reviewed)
        self.assertFalse(reviewed & set(CODEX_RISKY_FEATURES))

    def test_claude_efforts_from_help(self):
        help_text = "  --effort <level>   Effort level for the session (low, medium, high, xhigh, max)\n"
        self.assertEqual(claude_efforts_from_help(help_text), ["low", "medium", "high", "xhigh", "max"])
        self.assertEqual(claude_efforts_from_help("no such flag"), [])


if __name__ == "__main__":
    unittest.main()
