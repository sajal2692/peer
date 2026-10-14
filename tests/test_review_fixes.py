"""Regression tests for the v0.1.0 full review."""

import os
import unittest

from helpers import Sandbox, claude_assistant, claude_user, codex_event, human, peer, write_jsonl
from test_cli import CliTestCase


def bash_call(call_id, command):
    return claude_assistant({"type": "tool_use", "id": call_id, "name": "Bash", "input": {"command": command}})


def notification(call_id, body="done"):
    return {
        "type": "attachment",
        "attachment": {
            "type": "queued_command",
            "commandMode": "task-notification",
            "origin": {"kind": "task-notification"},
            "prompt": f"<task-notification><tool-use-id>{call_id}</tool-use-id><summary>{body}</summary>"
            "</task-notification>",
        },
    }


FIRST_PEER_THEN_REVIEW = [
    human("How should I cache this?"),
    claude_assistant({"type": "text", "text": "Use an LRU cache."}),
    human("<command-name>/peer</command-name>"),
    bash_call("p1", "~/.local/bin/peer --from claude"),
    claude_assistant({"type": "text", "text": "Started Codex in the background."}),
    notification("p1", "peer finished"),
    claude_assistant({"type": "text", "text": "## Codex's answer ... (relayed)"}),
    human("<command-name>/peer</command-name><command-args>review</command-args>"),
    bash_call("p2", "~/.local/bin/peer --from claude review"),
]


class OwnPeerRunTests(unittest.TestCase):
    def test_a_follow_up_review_looks_past_the_first_peer_run(self):
        session = peer.parse_claude(write_jsonl(FIRST_PEER_THEN_REVIEW))
        flagged = [i for i in session.items if i.peer_run]
        self.assertEqual(len(flagged), 1)
        req = peer.build_request(session, "codex", "review", "", True, peer.Limits())
        self.assertEqual(req.target, "How should I cache this?")
        self.assertIn("Use an LRU cache.", req.prompt)
        self.assertNotIn("(relayed)", req.prompt)
        self.assertNotIn("peer finished", req.prompt)

    def test_a_bare_peer_after_a_peer_run_answers_the_real_message(self):
        rows = [*FIRST_PEER_THEN_REVIEW[:-2], human("<command-name>/peer</command-name>")]
        session = peer.parse_claude(write_jsonl(rows))
        req = peer.build_request(session, "codex", "ask", "", True, peer.Limits())
        self.assertEqual(req.target, "How should I cache this?")

    def test_a_genuine_notification_is_still_answered(self):
        rows = [
            human("Run the build"),
            bash_call("b1", "npm run build"),
            claude_assistant({"type": "text", "text": "Building."}),
            notification("b1", "build failed"),
            claude_assistant({"type": "text", "text": "The build failed on a type error."}),
            human("<command-name>/peer</command-name>"),
        ]
        session = peer.parse_claude(write_jsonl(rows))
        req = peer.build_request(session, "codex", "ask", "", True, peer.Limits())
        self.assertEqual(req.target_label, "Task notification")
        self.assertIn("build failed", req.target)

    def test_current_turn_keeps_everything(self):
        session = peer.parse_claude(write_jsonl(FIRST_PEER_THEN_REVIEW[:2]))
        req = peer.build_request(session, "codex", "review", "", False, peer.Limits())
        self.assertEqual(req.target, "How should I cache this?")

    def test_codex_peer_then_peer_review(self):
        rows = [
            {"type": "session_meta", "payload": {"cwd": "/proj"}},
            codex_event({"type": "UserMessage", "content": [{"type": "text", "text": "Is this regex safe?"}]}),
            codex_event({"type": "AgentMessage", "content": [{"type": "Text", "text": "Mostly."}]}),
            codex_event({"type": "UserMessage", "content": [{"type": "text", "text": "$peer"}]}),
            codex_event({"type": "AgentMessage", "content": [{"type": "Text", "text": "Claude says (relayed)"}]}),
            codex_event({"type": "UserMessage", "content": [{"type": "text", "text": "$peer review"}]}),
        ]
        session = peer.parse_codex(write_jsonl(rows))
        req = peer.build_request(session, "claude", "review", "", True, peer.Limits())
        self.assertEqual(req.target, "Is this regex safe?")
        self.assertNotIn("(relayed)", req.prompt)

    def test_only_peer_commands_with_from_count(self):
        self.assertTrue(peer.is_peer_command({"command": "~/.local/bin/peer --from claude review 'x'"}))
        self.assertTrue(peer.is_peer_command({"command": "cd /p && ./peer.py --from codex"}))
        self.assertFalse(peer.is_peer_command({"command": "cd peer && ls"}))


class ParsingFixTests(unittest.TestCase):
    def test_a_reminder_before_a_notification_does_not_hide_it(self):
        rows = [claude_user("<system-reminder>note</system-reminder>\n<task-notification>done</task-notification>")]
        items = peer.parse_claude(write_jsonl(rows)).items
        self.assertEqual([(i.role, i.label) for i in items], [("input", "Task notification")])

    def test_review_works_on_a_reply_made_only_of_tool_calls(self):
        rows = [human("open the PR"), bash_call("g1", "gh pr create"), human("<command-name>/peer</command-name>")]
        session = peer.parse_claude(write_jsonl(rows))
        req = peer.build_request(session, "codex", "review", "", True, peer.Limits())
        self.assertEqual(req.target, "open the PR")

    def test_a_boolean_budget_is_ignored(self):
        self.assertEqual(peer.config_budget({"budget": True}), 0)
        self.assertEqual(peer.config_budget({"budget": 5000}), 5000)


class EnvironmentTests(Sandbox):
    def test_the_users_claude_settings_reach_the_peer(self):
        os.environ.update(
            {
                "CLAUDE_CODE_OAUTH_TOKEN": "t",
                "CLAUDE_CODE_SESSION_ID": "s",
                "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "9",
                "CLAUDE_CODE_ENTRYPOINT": "cli",
            }
        )
        env = peer.child_env()
        self.assertEqual((env.get("CLAUDE_CODE_OAUTH_TOKEN"), env.get("CLAUDE_CODE_MAX_OUTPUT_TOKENS")), ("t", "9"))
        self.assertNotIn("CLAUDE_CODE_SESSION_ID", env)
        self.assertNotIn("CLAUDE_CODE_ENTRYPOINT", env)


class LockdownTests(CliTestCase):
    def setUp(self):
        super().setUp()
        self.claude_session([human("Is this safe?"), human("<command-name>/peer</command-name>")])
        os.environ["CLAUDE_CODE_SESSION_ID"] = "claude-1"

    def test_codex_has_web_unless_turned_off(self):
        self.run_main("--from", "claude")
        self.assertNotIn('web_search="disabled"', self.recorded()["args"])
        self.run_main("--from", "claude", "--no-web")
        self.assertIn('web_search="disabled"', self.recorded()["args"])
        self.run_main("config", "web", "off")
        self.run_main("--from", "claude")
        self.assertIn('web_search="disabled"', self.recorded()["args"])

    def test_claude_web_tools_follow_the_web_setting(self):
        rows = [codex_event({"type": "UserMessage", "content": [{"type": "text", "text": "is this safe?"}]})]
        session = str(write_jsonl(rows))
        code, _, err = self.run_main("--from", "codex", "--session", session)
        self.assertEqual((code, err), (0, ""))
        args = self.recorded()["args"]
        self.assertEqual(args[args.index("--tools") + 1], "Read,Grep,Glob,WebSearch,WebFetch")
        self.run_main("--from", "codex", "--session", session, "--no-web")
        args = self.recorded()["args"]
        self.assertEqual(args[args.index("--tools") + 1], "Read,Grep,Glob")

    def test_an_unreadable_feature_list_stops_the_run(self):
        os.environ["PEER_CODEX_BIN"] = self.fake("codex", "import sys\nsys.exit(1)\n")
        code, _, err = self.run_main("--from", "claude")
        self.assertEqual(code, 1)
        self.assertIn("Could not read Codex's feature list", err)

    def test_mcp_servers_in_dotted_keys_are_switched_off(self):
        peer.CODEX_HOME.mkdir(parents=True, exist_ok=True)
        (peer.CODEX_HOME / "config.toml").write_text(
            'mcp_servers.mail.command = "m"\nmcp_servers.mail.args = []\n[profiles.work]\nmodel = "x"\n'
        )
        self.run_main("--from", "claude")
        args = " ".join(self.recorded()["args"])
        self.assertEqual(args.count("mcp_servers.mail.enabled=false"), 1)


if __name__ == "__main__":
    unittest.main()
