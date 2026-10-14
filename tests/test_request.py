"""Building the prompt the other tool receives."""

import os
import unittest
from pathlib import Path

from helpers import CLAUDE_ROWS, TRIMMED, write_jsonl

from peer.claude_transcript import parse_claude
from peer.cli import parse_args
from peer.common import MARKER
from peer.request import Limits, build_request
from peer.settings import resolve_limits
from peer.transcript import Item, Session


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.session = parse_claude(write_jsonl(CLAUDE_ROWS))

    def test_ask_withholds_last_reply_and_drops_request(self):
        req = build_request(self.session, "codex", "ask", "", True, TRIMMED)
        self.assertEqual(req.target, "What about invalidation?")
        self.assertIn("Use an LRU cache.", req.prompt)
        self.assertNotIn("Expire on write.", req.prompt)
        self.assertNotIn("/peer", req.prompt)
        self.assertTrue(req.prompt.startswith(MARKER))

    def test_review_includes_last_reply(self):
        req = build_request(self.session, "codex", "review", "edge cases", True, TRIMMED)
        self.assertIn("Expire on write.", req.prompt)
        self.assertIn("focus on: edge cases", req.prompt)

    def test_question_uses_history_as_context(self):
        req = build_request(self.session, "codex", "question", "Is Redis overkill?", True, TRIMMED)
        self.assertIn("Expire on write.", req.prompt)
        self.assertIn("<question>\nIs Redis overkill?", req.prompt)

    def test_fresh_question(self):
        empty = Session("claude", Path("."), "/proj", [])
        req = build_request(empty, "codex", "question", "Explain CRDTs", False, TRIMMED)
        self.assertNotIn("<transcript>", req.prompt)
        self.assertTrue(req.prompt.endswith("Explain CRDTs"))

    def test_current_turn_is_answered_when_not_dropped(self):
        req = build_request(self.session, "codex", "ask", "", False, TRIMMED)
        self.assertEqual(req.target, "/peer")

    def test_tools_none_hides_tool_activity(self):
        req = build_request(self.session, "codex", "review", "", True, Limits(tools=False))
        self.assertNotIn("tool call", req.prompt)

    def test_budget_keeps_first_and_newest_turns(self):
        items = []
        for n in range(20):
            items += [Item("user", f"question {n} " + "x" * 1000), Item("assistant", f"answer {n}")]
        session = Session("codex", Path("."), "/proj", items)
        req = build_request(session, "claude", "review", "", False, Limits.trimmed(budget=5_000))
        self.assertIn("question 0 ", req.prompt)
        self.assertIn("answer 19", req.prompt)
        self.assertNotIn("question 5 ", req.prompt)
        self.assertGreater(req.turns_omitted, 0)


class LimitTests(unittest.TestCase):
    def test_default_sends_everything(self):
        items = [
            Item("user", "q " + "x" * 30_000),
            Item("tool", "Bash: " + "y" * 5_000),
            Item("result", "z" * 9_000),
            Item("assistant", "done"),
        ]
        session = Session("codex", Path("."), "/proj", items)
        req = build_request(session, "claude", "review", "", False, Limits())
        self.assertNotIn("characters cut", req.prompt)
        self.assertIn("y" * 5_000, req.prompt)
        self.assertIn("z" * 9_000, req.prompt)
        trimmed = build_request(session, "claude", "review", "", False, Limits.trimmed())
        self.assertIn("characters cut", trimmed.prompt)

    def test_resolve_limits(self):
        saved = dict(os.environ)
        try:
            os.environ.pop("PEER_TRIM", None)
            full = resolve_limits(parse_args([]), {})
            self.assertEqual(full, Limits())
            self.assertEqual(full.describe(), "everything, untrimmed")
            trimmed = resolve_limits(parse_args(["--trim", "--max-tool-output", "0"]), {})
            self.assertEqual((trimmed.message, trimmed.tool_output, trimmed.budget), (20_000, 0, 400_000))
            one = resolve_limits(parse_args(["--max-tool-output", "800", "--no-tools"]), {"budget": 9})
            self.assertEqual((one.tool_output, one.message, one.budget, one.tools), (800, 0, 9, False))
            self.assertEqual(resolve_limits(parse_args([]), {"trim": True}), Limits.trimmed())
        finally:
            os.environ.clear()
            os.environ.update(saved)


if __name__ == "__main__":
    unittest.main()
