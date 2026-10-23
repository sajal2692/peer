"""Access (matching the host's permissions, or --write and --read-only) and continuing the peer's session."""

import os
import unittest

from helpers import claude_assistant, claude_user, codex_event, human
from test_cli import CODEX_CHAT, CliTestCase

from peer import common

ANSWER = "## Codex's answer\n\nProposed entry, not recorded.\n\n---\nContinue with Codex: codex resume 01a1-old"


def peer_call(command_id, words):
    return [
        human(f"<command-name>/peer</command-name><command-args>{words}</command-args>"),
        claude_assistant(
            {
                "type": "tool_use",
                "id": command_id,
                "name": "Bash",
                "input": {"command": f"peer --from claude -- '{words}'"},
            }
        ),
    ]


# A first /peer call that Codex answered, a normal exchange, then a second /peer call.
FOLLOW_UP_CHAT = [
    human("Log the domain renewal, 250 CAD on the Amex."),
    claude_assistant({"type": "text", "text": "Which category?"}),
    *peer_call("p1", "log it as a business expense"),
    claude_user([{"type": "tool_result", "tool_use_id": "p1", "content": ANSWER}]),
    claude_assistant({"type": "text", "text": ANSWER}),
    human("Make the memo say four years."),
    claude_assistant({"type": "text", "text": "Noted: four years."}),
    *peer_call("p2", "add it"),
]


def with_mode(rows, mode):
    """The rows with Claude Code's permission mode recorded on each user row, as it does."""
    return [{**r, "permissionMode": mode} if r.get("type") == "user" else r for r in rows]


def codex_rows(sandbox):
    return [CODEX_CHAT[0], {"type": "turn_context", "payload": {"sandbox_policy": {"type": sandbox}}}, *CODEX_CHAT[1:]]


class AccessTests(CliTestCase):
    def run_from_claude(self, mode, *flags):
        rows = FOLLOW_UP_CHAT if mode is None else with_mode(FOLLOW_UP_CHAT, mode)
        self.claude_session(rows)
        os.environ["CLAUDE_CODE_SESSION_ID"] = "claude-1"
        code, out, err = self.run_main("--from", "claude", "--no-continue", *flags, "add it")
        self.assertEqual((code, err), (0, ""))
        return self.recorded(), out

    def test_codex_writes_when_claude_code_may_write(self):
        for mode in ("auto", "acceptEdits", "bypassPermissions"):
            with self.subTest(mode=mode):
                rec, out = self.run_from_claude(mode, "--details")
                self.assertEqual(
                    rec["args"][:5],
                    ["exec", "--sandbox", "workspace-write", "-c", "sandbox_workspace_write.network_access=true"],
                )
                self.assertIn("--disable apps", " ".join(rec["args"]))  # connected apps and plugins stay off
                self.assertIn("you may change files and run commands", rec["prompt"])
                self.assertNotIn("do not modify anything", rec["prompt"])
                self.assertIn(f"Access: write (matches Claude Code's {mode} mode)", out)
                self.tearDown()
                self.setUp()

    def test_codex_stays_read_only_when_claude_code_asks_or_plans(self):
        for mode in ("default", "plan", "dontAsk"):
            with self.subTest(mode=mode):
                rec, out = self.run_from_claude(mode)
                self.assertEqual(rec["args"][:3], ["exec", "--sandbox", "read-only"])
                self.assertIn("do not modify anything", rec["prompt"])
                self.assertNotIn("Access: write", out)
                self.tearDown()
                self.setUp()

    def test_unrecorded_permissions_mean_read_only(self):
        rec, out = self.run_from_claude(None, "--details")
        self.assertEqual(rec["args"][:3], ["exec", "--sandbox", "read-only"])
        self.assertIn("Access: read-only (no Claude Code permissions to match)", out)

    def test_flags_and_config_override_the_match(self):
        rec, out = self.run_from_claude("auto", "--read-only", "--details")
        self.assertEqual(rec["args"][:3], ["exec", "--sandbox", "read-only"])
        self.assertIn("Access: read-only (--read-only)", out)
        self.tearDown()
        self.setUp()
        rec, _ = self.run_from_claude("plan", "--write")
        self.assertEqual(rec["args"][2], "workspace-write")
        self.tearDown()
        self.setUp()
        self.run_main("config", "access", "read-only")
        rec, out = self.run_from_claude("auto", "--details")
        self.assertEqual(rec["args"][2], "read-only")
        self.assertIn("Access: read-only (peer config)", out)

    def test_claude_gets_editing_and_shell_tools_when_codex_may_write(self):
        self.codex_session(codex_rows("workspace-write"))
        os.environ["CODEX_THREAD_ID"] = "0199-thread"
        self.run_main("--from", "codex", "--no-web")
        args = self.recorded()["args"]
        self.assertEqual(args[args.index("--tools") + 1], "Read,Grep,Glob,Edit,Write,Bash")
        self.assertEqual(args[args.index("--permission-mode") + 1], "acceptEdits")
        self.assertIn("--strict-mcp-config", args)

    def test_claude_stays_read_only_when_codex_is(self):
        self.codex_session(codex_rows("read-only"))
        os.environ["CODEX_THREAD_ID"] = "0199-thread"
        _, out, _ = self.run_main("--from", "codex", "--no-web", "--details")
        args = self.recorded()["args"]
        self.assertEqual(args[args.index("--tools") + 1], "Read,Grep,Glob")
        self.assertIn("Access: read-only (matches Codex's read-only sandbox)", out)

    def test_a_plain_question_outside_a_session_is_read_only_unless_asked(self):
        self.run_main("--to", "codex", "rename the file")
        self.assertIn("do not modify anything", self.recorded()["prompt"])
        code, _, _ = self.run_main("--to", "codex", "--write", "rename the file")
        self.assertEqual(code, 0)
        self.assertIn("You may change files and run commands", self.recorded()["prompt"])


class ContinueTests(CliTestCase):
    def setUp(self):
        super().setUp()
        self.claude_session(FOLLOW_UP_CHAT)
        os.environ["CLAUDE_CODE_SESSION_ID"] = "claude-1"

    def test_continue_resumes_codex_with_only_the_new_turns(self):
        code, out, err = self.run_main("--from", "claude", "--continue", "add it")
        rec = self.recorded()
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(rec["args"][-3:], ["resume", "01a1-old", "-"])
        prompt = rec["prompt"]
        self.assertIn("This continues your earlier answer", prompt)
        self.assertIn("Make the memo say four years.", prompt)
        self.assertIn("Noted: four years.", prompt)
        self.assertNotIn("Log the domain renewal", prompt)  # Codex already has it
        self.assertNotIn("Proposed entry", prompt)  # the relayed answer is its own
        self.assertIn("<question>\nadd it\n</question>", prompt)
        self.assertIn("Continued session: 01a1-old", out.splitlines()[2])

    def test_continue_straight_after_the_answer_sends_just_the_question(self):
        rows = [*FOLLOW_UP_CHAT[2:6], *peer_call("p2", "add it")]
        common.CLAUDE_HOME.joinpath("projects", "-proj", "claude-1.jsonl").unlink()
        common.CLAUDE_HOME.joinpath("projects", "-proj").rmdir()
        self.claude_session(rows)
        code, _, _ = self.run_main("--from", "claude", "--continue", "--write", "add it")
        prompt = self.recorded()["prompt"]
        self.assertEqual(code, 0)
        self.assertNotIn("<transcript>", prompt)
        self.assertTrue(prompt.endswith("The user now asks you directly:\n\n<question>\nadd it\n</question>"))
        self.assertIn("you may change files", prompt)

    def test_continuing_is_the_default(self):
        self.run_main("--from", "claude", "add it")
        self.assertEqual(self.recorded()["args"][-3:], ["resume", "01a1-old", "-"])
        self.run_main("--from", "claude", "--no-continue", "add it")
        self.assertNotIn("resume", self.recorded()["args"])
        self.assertIn("Log the domain renewal", self.recorded()["prompt"])
        self.run_main("config", "continue", "off")
        self.run_main("--from", "claude", "add it")
        self.assertNotIn("resume", self.recorded()["args"])

    def test_continue_without_an_earlier_answer_starts_a_new_session(self):
        rows = [human("Hi"), claude_assistant({"type": "text", "text": "Hello."}), *peer_call("p1", "")]
        common.CLAUDE_HOME.joinpath("projects", "-proj", "claude-1.jsonl").unlink()
        common.CLAUDE_HOME.joinpath("projects", "-proj").rmdir()
        self.claude_session(rows)
        code, _, err = self.run_main("--from", "claude", "--continue")
        self.assertEqual(code, 0)
        self.assertIn("no earlier Codex answer in this conversation; starting a new session", err)
        self.assertNotIn("resume", self.recorded()["args"])
        code, _, err = self.run_main("--from", "claude")  # by default, silently
        self.assertEqual((code, err), (0, ""))

    def test_bare_peer_continue_with_nothing_new_is_an_error(self):
        rows = [*FOLLOW_UP_CHAT[2:6], *peer_call("p2", "")]
        common.CLAUDE_HOME.joinpath("projects", "-proj", "claude-1.jsonl").unlink()
        common.CLAUDE_HOME.joinpath("projects", "-proj").rmdir()
        self.claude_session(rows)
        code, _, err = self.run_main("--from", "claude", "--continue")
        self.assertEqual(code, 1)
        self.assertIn("Nothing new since Codex's last answer", err)

    def test_resume_by_id_not_in_the_transcript_sends_everything(self):
        code, _, _ = self.run_main("--from", "claude", "--resume", "other-id", "add it")
        rec = self.recorded()
        self.assertEqual(code, 0)
        self.assertEqual(rec["args"][-3:], ["resume", "other-id", "-"])
        self.assertIn("Log the domain renewal", rec["prompt"])
        self.assertIn("You are Codex, asked for an independent answer", rec["prompt"])

    def test_resume_and_ephemeral_conflict(self):
        code, _, err = self.run_main("--from", "claude", "--resume", "x", "--ephemeral", "add it")
        self.assertEqual(code, 1)
        self.assertIn("cannot continue one", err)

    def test_details_header_names_the_session_and_access(self):
        _, out, _ = self.run_main("--from", "claude", "--continue", "--details", "add it")
        self.assertIn("Access: read-only", out)
        self.assertIn("Codex session: continued 01a1-old", out)
        self.assertIn("since Codex's last answer", out)


class ClaudePeerContinueTests(CliTestCase):
    def test_continue_resumes_claude(self):
        rows = [
            *CODEX_CHAT[:3],
            codex_event({"type": "UserMessage", "content": [{"type": "text", "text": "$peer"}]}),
            codex_event(
                {
                    "type": "AgentMessage",
                    "content": [{"type": "Text", "text": "Continue with Claude Code: claude --resume c-123"}],
                }
            ),
            codex_event({"type": "UserMessage", "content": [{"type": "text", "text": "$peer go on"}]}),
        ]
        self.codex_session(rows)
        os.environ["CODEX_THREAD_ID"] = "0199-thread"
        code, _, _ = self.run_main("--from", "codex", "--continue", "go on")
        args = self.recorded()["args"]
        self.assertEqual(code, 0)
        self.assertEqual(args[args.index("--resume") + 1], "c-123")


if __name__ == "__main__":
    unittest.main()
