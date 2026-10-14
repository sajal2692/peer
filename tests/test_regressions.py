"""Regression tests for problems found in the pre-release code review."""

import json
import unittest

from helpers import (
    Sandbox,
    claude_assistant,
    claude_user,
    codex_event,
    human,
    write_jsonl,
)

from peer.claude_transcript import parse_claude
from peer.cli import parse_args
from peer.codex_transcript import parse_codex
from peer.common import PeerError
from peer.request import Limits, build_request, render_transcript, render_turn, split_turns
from peer.runner import parse_claude_run
from peer.sessions import sniff_format
from peer.transcript import Item


def queued(text):
    return {
        "type": "attachment",
        "attachment": {"type": "queued_command", "commandMode": "prompt", "origin": {"kind": "human"}, "prompt": text},
    }


class RequestRemovalTests(unittest.TestCase):
    def test_a_message_sent_after_peer_does_not_become_the_target(self):
        rows = [
            {"type": "session_meta", "payload": {"cwd": "/proj"}},
            codex_event({"type": "UserMessage", "content": [{"type": "text", "text": "Is this safe?"}]}),
            codex_event({"type": "AgentMessage", "content": [{"type": "Text", "text": "Mostly."}]}),
            codex_event({"type": "UserMessage", "content": [{"type": "text", "text": "[$peer](/x/SKILL.md)"}]}),
            codex_event({"type": "UserMessage", "content": [{"type": "text", "text": "also check perf"}]}),
        ]
        session = parse_codex(write_jsonl(rows))
        req = build_request(session, "claude", "ask", "", True, Limits())
        self.assertEqual(req.target, "Is this safe?")

    def test_without_an_invocation_the_last_typed_message_is_dropped(self):
        rows = [human("first"), claude_assistant({"type": "text", "text": "a"}), human("ask codex please")]
        session = parse_claude(write_jsonl(rows))
        req = build_request(session, "codex", "ask", "", True, Limits())
        self.assertEqual(req.target, "first")

    def test_a_repeated_queued_message_is_kept(self):
        rows = [
            human("continue"),
            claude_assistant({"type": "text", "text": "A"}),
            human("next"),
            claude_assistant({"type": "text", "text": "B"}),
            queued("continue"),
            claude_assistant({"type": "text", "text": "C"}),
            human("<command-name>/peer</command-name>"),
        ]
        session = parse_claude(write_jsonl(rows))
        self.assertEqual([i.text for i in session.items if i.role == "user"], ["continue", "next", "continue", "/peer"])
        req = build_request(session, "codex", "ask", "", True, Limits())
        self.assertEqual(req.target, "continue")
        self.assertIn("### User\nnext", req.prompt)

    def test_a_queued_message_written_twice_is_sent_once(self):
        rows = [queued("hello"), human("hello"), claude_assistant({"type": "text", "text": "hi"})]
        session = parse_claude(write_jsonl(rows))
        self.assertEqual([i.text for i in session.items if i.role == "user"], ["hello"])


class AttachmentTests(unittest.TestCase):
    def test_attachments_stay_with_the_withheld_message(self):
        rows = [
            human("Summarise this file"),
            {
                "type": "attachment",
                "attachment": {
                    "type": "file",
                    "filename": "/p/notes.md",
                    "content": {"type": "text", "file": {"content": "the milk"}},
                },
            },
            claude_assistant({"type": "text", "text": "It is about milk."}),
            human("<command-name>/peer</command-name>"),
        ]
        session = parse_claude(write_jsonl(rows))
        req = build_request(session, "codex", "ask", "", True, Limits())
        latest = req.prompt.split("<latest_input", 1)[1]
        self.assertIn("attached file: /p/notes.md", latest)
        self.assertIn("the milk", latest)
        self.assertNotIn("It is about milk.", req.prompt)


class CodexFormatTests(unittest.TestCase):
    def test_legacy_prefix_and_new_items_are_both_read(self):
        rows = [
            {"type": "session_meta", "payload": {"cwd": "/proj"}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "old question"}},
            {"type": "event_msg", "payload": {"type": "agent_message", "message": "old answer"}},
            codex_event({"type": "UserMessage", "content": [{"type": "text", "text": "new question"}]}),
        ]
        texts = [i.text for i in parse_codex(write_jsonl(rows)).items]
        self.assertEqual(texts, ["old question", "old answer", "new question"])

    def test_legacy_events_keep_tool_calls(self):
        rows = [
            {"type": "event_msg", "payload": {"type": "user_message", "message": "run tests"}},
            {
                "type": "response_item",
                "payload": {"type": "function_call", "name": "shell", "arguments": '{"cmd": "pytest"}'},
            },
            {"type": "response_item", "payload": {"type": "function_call_output", "output": "3 passed"}},
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "run tests"}],
                },
            },
            {"type": "event_msg", "payload": {"type": "agent_message", "message": "All green."}},
        ]
        items = [(i.role, i.text) for i in parse_codex(write_jsonl(rows)).items]
        self.assertEqual([r for r, _ in items], ["user", "tool", "result", "assistant"])
        self.assertIn(("result", "3 passed"), items)

    def test_a_codex_export_outside_codex_home_is_recognised(self):
        rows = [
            {"type": "session_meta", "payload": {"cwd": "/proj"}},
            codex_event({"type": "UserMessage", "content": [{"type": "text", "text": "hi"}]}),
        ]
        self.assertEqual(sniff_format(write_jsonl(rows)), "codex")
        self.assertEqual(sniff_format(write_jsonl([claude_user("hi")])), "claude")


class LimitRegressionTests(unittest.TestCase):
    def test_budget_is_a_real_cap(self):
        items = []
        for n in range(4):
            items += [Item("user", f"q{n} " + "x" * 1000), Item("assistant", "y" * 1000)]
        transcript, _ = render_transcript(split_turns(items), "Codex", Limits(budget=500))
        self.assertLessEqual(len(transcript), 560)  # the budget plus the cut marker

    def test_untrimmed_tool_calls_keep_every_argument(self):
        rows = [
            human("edit it"),
            claude_assistant(
                {
                    "type": "tool_use",
                    "id": "t",
                    "name": "Edit",
                    "input": {"file_path": "/p/a.py", "old_string": "foo", "new_string": "bar"},
                }
            ),
        ]
        session = parse_claude(write_jsonl(rows))
        full = render_turn(split_turns(session.items)[0], "Claude Code", Limits())
        self.assertIn('"old_string": "foo"', full)
        brief = render_turn(split_turns(session.items)[0], "Claude Code", Limits.trimmed())
        self.assertIn("- tool call: Edit: /p/a.py", brief)
        self.assertNotIn("old_string", brief)


class CommandLineTests(Sandbox):
    def test_options_may_follow_the_words(self):
        args = parse_args(["review", "--model", "gpt-x", "error", "handling"])
        self.assertEqual((args.words, args.model), (["review", "error", "handling"], "gpt-x"))

    def test_bad_claude_output_is_an_error(self):
        for stdout in ("{}", "[]", json.dumps({"result": "  "})):
            with self.assertRaises(PeerError):
                parse_claude_run(0, stdout, "", False)


if __name__ == "__main__":
    unittest.main()
