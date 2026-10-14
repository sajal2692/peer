"""Reading Claude Code and Codex transcripts."""

import tempfile
import unittest
from pathlib import Path

from helpers import (
    CLAUDE_ROWS,
    CODEX_ROWS,
    CONTEXT_ROWS,
    INJECTED_ROWS,
    PNG_1PX,
    TRIMMED,
    codex_event,
    peer,
    write_jsonl,
)


class ParseTests(unittest.TestCase):
    def test_claude_transcript(self):
        session = peer.parse_claude(write_jsonl(CLAUDE_ROWS))
        roles = [(i.role, i.text) for i in session.items]
        self.assertEqual(session.cwd, "/proj")
        self.assertEqual(roles[0], ("user", "How should I cache this?"))
        self.assertIn(("tool", "Read: /proj/app.py"), roles)
        self.assertIn(("result", "def handler(): ..."), roles)
        users = [t for r, t in roles if r == "user"]
        # Built-in commands, compaction summaries and meta messages are not user turns.
        self.assertEqual(users, ["How should I cache this?", "What about invalidation?", "/peer"])
        self.assertNotIn("subagent", " ".join(t for _, t in roles))

    def test_codex_transcript(self):
        session = peer.parse_codex(write_jsonl(CODEX_ROWS))
        roles = [(i.role, i.text) for i in session.items]
        self.assertEqual(session.cwd, "/proj")
        self.assertEqual(roles[0], ("user", "Why is the build failing?"))
        self.assertIn(("tool", "shell: npm run build"), roles)
        self.assertIn(("result", "(exit 1) TypeError in a.ts"), roles)
        self.assertIn(("tool", "edit files: update /proj/a.ts"), roles)
        self.assertNotIn("AGENTS.md", " ".join(t for _, t in roles))

    def test_codex_legacy_events(self):
        rows = [
            {"type": "session_meta", "payload": {"cwd": "/old"}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "hi"}},
            {"type": "event_msg", "payload": {"type": "agent_message", "message": "hello"}},
        ]
        session = peer.parse_codex(write_jsonl(rows))
        self.assertEqual([(i.role, i.text) for i in session.items], [("user", "hi"), ("assistant", "hello")])


class InjectedInputTests(unittest.TestCase):
    def setUp(self):
        self.session = peer.parse_claude(write_jsonl(INJECTED_ROWS))

    def test_labels(self):
        labelled = [(i.role, i.label) for i in self.session.items if i.role in ("user", "input", "summary")]
        self.assertEqual(
            labelled,
            [
                ("user", ""),
                ("input", "Task notification"),
                ("input", peer.ANSWER_LABEL),
                ("summary", peer.SUMMARY_LABEL),
                ("input", "Scheduled task"),
                ("input", "Message from another Claude session"),
                ("user", ""),
                ("input", "Task notification"),
            ],
        )
        self.assertIn(("tool", "AskUserQuestion: Apply to prod?"), [(i.role, i.text) for i in self.session.items])

    def test_injected_inputs_mark_turns(self):
        turns = peer.split_turns(self.session.items)
        self.assertEqual(
            [t[0].heading for t in turns],
            [
                "User",
                "Task notification",
                peer.ANSWER_LABEL,
                "Scheduled task",
                "Message from another Claude session",
                "User",
                "Task notification",
            ],
        )

    def test_drop_from_last_typed_message(self):
        req = peer.build_request(self.session, "codex", "ask", "", True, TRIMMED)
        self.assertEqual(req.target_label, "Message from another Claude session")
        self.assertIn("ping", req.target)
        self.assertNotIn("Noted the ping.", req.prompt)
        self.assertNotIn("peer finished", req.prompt)
        self.assertIn('<latest_input from="Message from another Claude session">', req.prompt)
        self.assertIn("### Task notification", req.prompt)
        self.assertIn("### " + peer.SUMMARY_LABEL, req.prompt)

    def test_untagged_rows_count_as_typed(self):
        # Older Claude Code versions write no provenance fields.
        self.assertIsNone(peer.injected_label({}, "hello"))
        self.assertEqual(peer.injected_label({}, "<task-notification>x"), "Task notification")
        self.assertEqual(
            peer.injected_label({"promptSource": "sdk", "origin": {"kind": "task-notification"}}, "x"), "System input"
        )

    def test_codex_drop_unchanged(self):
        session = peer.parse_codex(write_jsonl(CODEX_ROWS))
        req = peer.build_request(session, "claude", "ask", "", True, TRIMMED)
        self.assertEqual(req.target, "Why is the build failing?")
        self.assertEqual(req.target_label, "User")


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.session = peer.parse_claude(write_jsonl(CONTEXT_ROWS))

    def test_attachments_reach_the_transcript(self):
        items = [(i.role, i.label, i.text) for i in self.session.items]
        self.assertIn(("user", "", "also check the footer"), items)
        self.assertIn(("input", "Task notification", "<task-notification>build done</task-notification>"), items)
        self.assertEqual([t for r, _, t in items if t == "and the header"], ["and the header"])
        self.assertIn(("tool", "", "attached file: /proj/notes.md"), items)
        self.assertIn(("result", "", "remember the milk"), items)
        self.assertEqual(self.session.instruction_files, ["/home/u/.claude/CLAUDE.md"])

    def test_images_are_saved_for_the_peer(self):
        req = peer.build_request(self.session, "codex", "review", "", False, TRIMMED)
        self.assertIn("[image #0]", req.prompt)
        self.assertIn("/home/u/.claude/CLAUDE.md", req.prompt)
        with tempfile.TemporaryDirectory() as tmp:
            prompt = peer.save_images(req.prompt, self.session.images, Path(tmp))
            saved = Path(tmp) / "image-0.png"
            self.assertIn(f"[image: {saved}]", prompt)
            self.assertTrue(saved.read_bytes().startswith(b"\x89PNG"))

    def test_codex_images_and_extensions(self):
        rows = [
            {"type": "session_meta", "payload": {"cwd": "/proj"}},
            codex_event(
                {
                    "type": "UserMessage",
                    "content": [
                        {"type": "text", "text": "look"},
                        {"type": "local_image", "path": "/tmp/shot.png"},
                        {"type": "image", "image_url": "data:image/png;base64," + PNG_1PX},
                    ],
                }
            ),
            codex_event({"type": "ImageView", "path": "file:///proj/logo.jpg"}),
            codex_event({"type": "Extension", "kind": "web.search", "query": "codex docs"}),
        ]
        session = peer.parse_codex(write_jsonl(rows))
        texts = [i.text for i in session.items]
        self.assertEqual(texts[0], "look\n[image: /tmp/shot.png]\n[image #0]")
        self.assertIn("view image: /proj/logo.jpg", texts)
        self.assertIn("web.search: codex docs", texts)
        self.assertEqual(len(session.images), 1)


if __name__ == "__main__":
    unittest.main()
