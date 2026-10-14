"""Building the prompt the other tool receives."""

import unittest
from pathlib import Path

from helpers import CLAUDE_ROWS, TRIMMED, peer, write_jsonl


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.session = peer.parse_claude(write_jsonl(CLAUDE_ROWS))

    def test_ask_withholds_last_reply_and_drops_request(self):
        req = peer.build_request(self.session, "codex", "ask", "", True, TRIMMED)
        self.assertEqual(req.target, "What about invalidation?")
        self.assertIn("Use an LRU cache.", req.prompt)
        self.assertNotIn("Expire on write.", req.prompt)
        self.assertNotIn("/peer", req.prompt)
        self.assertTrue(req.prompt.startswith(peer.MARKER))

    def test_review_includes_last_reply(self):
        req = peer.build_request(self.session, "codex", "review", "edge cases", True, TRIMMED)
        self.assertIn("Expire on write.", req.prompt)
        self.assertIn("focus on: edge cases", req.prompt)

    def test_question_uses_history_as_context(self):
        req = peer.build_request(self.session, "codex", "question", "Is Redis overkill?", True, TRIMMED)
        self.assertIn("Expire on write.", req.prompt)
        self.assertIn("<question>\nIs Redis overkill?", req.prompt)

    def test_fresh_question(self):
        empty = peer.Session("claude", Path("."), "/proj", [])
        req = peer.build_request(empty, "codex", "question", "Explain CRDTs", False, TRIMMED)
        self.assertNotIn("<transcript>", req.prompt)
        self.assertTrue(req.prompt.endswith("Explain CRDTs"))

    def test_current_turn_is_answered_when_not_dropped(self):
        req = peer.build_request(self.session, "codex", "ask", "", False, TRIMMED)
        self.assertEqual(req.target, "/peer")

    def test_tools_none_hides_tool_activity(self):
        req = peer.build_request(self.session, "codex", "review", "", True, peer.Limits(tools=False))
        self.assertNotIn("tool call", req.prompt)

    def test_budget_keeps_first_and_newest_turns(self):
        items = []
        for n in range(20):
            items += [peer.Item("user", f"question {n} " + "x" * 1000), peer.Item("assistant", f"answer {n}")]
        session = peer.Session("codex", Path("."), "/proj", items)
        req = peer.build_request(session, "claude", "review", "", False, peer.Limits.trimmed(budget=5_000))
        self.assertIn("question 0 ", req.prompt)
        self.assertIn("answer 19", req.prompt)
        self.assertNotIn("question 5 ", req.prompt)
        self.assertGreater(req.turns_omitted, 0)


class LimitTests(unittest.TestCase):
    def test_default_sends_everything(self):
        items = [
            peer.Item("user", "q " + "x" * 30_000),
            peer.Item("tool", "Bash: " + "y" * 5_000),
            peer.Item("result", "z" * 9_000),
            peer.Item("assistant", "done"),
        ]
        session = peer.Session("codex", Path("."), "/proj", items)
        req = peer.build_request(session, "claude", "review", "", False, peer.Limits())
        self.assertNotIn("characters cut", req.prompt)
        self.assertIn("y" * 5_000, req.prompt)
        self.assertIn("z" * 9_000, req.prompt)
        trimmed = peer.build_request(session, "claude", "review", "", False, peer.Limits.trimmed())
        self.assertIn("characters cut", trimmed.prompt)

    def test_resolve_limits(self):
        saved = dict(peer.os.environ)
        try:
            peer.os.environ.pop("PEER_TRIM", None)
            full = peer.resolve_limits(peer.parse_args([]), {})
            self.assertEqual(full, peer.Limits())
            self.assertEqual(full.describe(), "everything, untrimmed")
            trimmed = peer.resolve_limits(peer.parse_args(["--trim", "--max-tool-output", "0"]), {})
            self.assertEqual((trimmed.message, trimmed.tool_output, trimmed.budget), (20_000, 0, 400_000))
            one = peer.resolve_limits(peer.parse_args(["--max-tool-output", "800", "--no-tools"]), {"budget": 9})
            self.assertEqual((one.tool_output, one.message, one.budget, one.tools), (800, 0, 9, False))
            self.assertEqual(peer.resolve_limits(peer.parse_args([]), {"trim": True}), peer.Limits.trimmed())
        finally:
            peer.os.environ.clear()
            peer.os.environ.update(saved)


if __name__ == "__main__":
    unittest.main()
