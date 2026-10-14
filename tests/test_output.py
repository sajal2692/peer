"""The environment passed to the other tool and the header peer prints."""

import os
import unittest

from helpers import CODEX_ROWS, INJECTED_ROWS, TRIMMED, claude_user, write_jsonl

from peer.claude_transcript import parse_claude
from peer.cli import main, parse_args
from peer.codex_transcript import parse_codex
from peer.common import MARKER
from peer.output import short_header
from peer.request import build_request
from peer.runner import Answer, Run, child_env
from peer.sessions import is_peer_session
from peer.settings import Setting, resolve_switch


class EnvTests(unittest.TestCase):
    def test_child_env_scrubs_session_variables(self):
        saved = dict(os.environ)
        try:
            os.environ.update(
                {
                    "CLAUDECODE": "1",
                    "CLAUDE_CODE_SESSION_ID": "s",
                    "CODEX_THREAD_ID": "t",
                    "CLAUDE_CODE_USE_BEDROCK": "1",
                    "HOME_KEEP": "x",
                }
            )
            env = child_env()
        finally:
            os.environ.clear()
            os.environ.update(saved)
        self.assertNotIn("CLAUDECODE", env)
        self.assertNotIn("CLAUDE_CODE_SESSION_ID", env)
        self.assertNotIn("CODEX_THREAD_ID", env)
        self.assertEqual(env["CLAUDE_CODE_USE_BEDROCK"], "1")
        self.assertEqual(env["PEER_ACTIVE"], "1")

    def test_peer_sessions_are_recognized(self):
        mine = write_jsonl([claude_user(MARKER + "\nYou are Claude Code")])
        theirs = write_jsonl([claude_user("normal question mentioning " + MARKER)])
        self.assertTrue(is_peer_session(mine))
        self.assertFalse(is_peer_session(theirs))


class HeaderTests(unittest.TestCase):
    def test_short_header(self):
        session = parse_claude(write_jsonl(INJECTED_ROWS))
        args = parse_args(["--dry-run"])
        saved = dict(os.environ)
        try:
            os.environ["PEER_CODEX_BIN"] = "/bin/codex-test"
            run = Run("codex", "/proj", Setting(None, "Codex default"), Setting(None, "Codex default"), args)
        finally:
            os.environ.clear()
            os.environ.update(saved)
        run.close()
        answer = Answer("Hi", "s", "gpt-x", "high", None)
        typed = build_request(parse_codex(write_jsonl(CODEX_ROWS)), "claude", "ask", "", True, TRIMMED)
        off = Setting("off", "default")
        self.assertEqual(short_header(run, typed, answer, off), "## Codex's answer\n\nModel: gpt-x | Effort: high")
        injected = build_request(session, "codex", "ask", "", True, TRIMMED)
        text = short_header(run, injected, answer, Setting("on", "--assess"))
        self.assertIn("Model: gpt-x | Effort: high | Assessment: on", text)
        self.assertIn("Answering: Message from another Claude session:", text)
        self.assertEqual(short_header(run, typed, None, off), "## Codex's answer\n\nModel: Codex default")

    def test_details_switch(self):
        saved = dict(os.environ)
        try:
            os.environ.pop("PEER_DETAILS", None)
            self.assertEqual(resolve_switch("details", None, {}).value, "off")
            self.assertEqual(resolve_switch("details", None, {"details": True}).value, "on")
            self.assertIs(parse_args(["--details"]).details, True)
        finally:
            os.environ.clear()
            os.environ.update(saved)

    def test_dry_run_header_on_stdout(self):
        import contextlib
        import io

        path = write_jsonl(INJECTED_ROWS)
        saved = dict(os.environ)
        out, err = io.StringIO(), io.StringIO()
        try:
            os.environ.pop("PEER_ACTIVE", None)
            os.environ["PEER_CODEX_BIN"] = "/bin/codex-test"
            os.environ["PEER_CONFIG"] = "/nonexistent/peer.json"
            os.environ.pop("PEER_CODEX_MODEL", None)
            os.environ.pop("PEER_CODEX_EFFORT", None)
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(["--from", "claude", "--session", str(path), "--model", "gpt-x", "--dry-run"])
        finally:
            os.environ.clear()
            os.environ.update(saved)
        self.assertEqual(code, 0)
        lines = out.getvalue().splitlines()
        self.assertEqual(lines[0], "## Codex's answer")
        self.assertTrue(lines[2].startswith("Peer command: peer --from claude --session "))
        self.assertTrue(lines[3].startswith("Codex command: /bin/codex-test exec --sandbox read-only"))
        self.assertIn("-m gpt-x", lines[3])
        self.assertEqual(lines[4], "Model: gpt-x (from --model)")
        self.assertTrue(lines[5].startswith("Mode: Codex answered the latest input itself"))
        self.assertTrue(lines[6].startswith('Answering: Task notification: "<task-notification>peer finished'))
        self.assertTrue(lines[7].startswith("Context: 7 turns from Claude Code"))
        self.assertEqual(lines[8], "Web access: on")
        self.assertEqual(lines[9], "Assessment: off (default)")
        self.assertEqual(err.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
