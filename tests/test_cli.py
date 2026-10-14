"""End to end: peer's command line against fake codex and claude binaries."""

import json
import os
import sys
import unittest
from pathlib import Path

from helpers import PNG_1PX, Sandbox, claude_assistant, claude_user, codex_event, human, peer, write_jsonl

# A stand-in for `codex exec`. It records its arguments, prompt and environment, writes an
# answer to the -o file, and prints the header lines real Codex prints on stderr.
FAKE_CODEX = r"""
import json, os, sys, time
args = sys.argv[1:]
prompt = sys.stdin.read()
record = {"args": args, "prompt": prompt, "env": {k: os.environ.get(k) for k in
          ("PEER_ACTIVE", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "CLAUDECODE")}}
images = [w.rstrip("]") for w in prompt.split() if w.startswith("/") and "peer-images-" in w]
record["images_exist"] = [os.path.exists(p) for p in images]
record["image_paths"] = images
open(os.environ["FAKE_RECORD"], "w").write(json.dumps(record))
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "fail":
    sys.stderr.write("ERROR: model not available\n")
    sys.exit(1)
if mode == "slow":
    time.sleep(5)
out = args[args.index("-o") + 1]
open(out, "w").write("Fake Codex answer.")
model = args[args.index("-m") + 1] if "-m" in args else "fake-model"
sys.stderr.write(f"model: {model}\nreasoning effort: low\nsession id: fake-session\n")
"""

# A stand-in for `claude -p --output-format json`.
FAKE_CLAUDE = r"""
import json, os, sys
args = sys.argv[1:]
prompt = sys.stdin.read()
open(os.environ["FAKE_RECORD"], "w").write(json.dumps({"args": args, "prompt": prompt, "cwd": os.getcwd()}))
if os.environ.get("FAKE_MODE") == "fail":
    print(json.dumps({"is_error": True, "result": "Not logged in"}))
    sys.exit(1)
model = args[args.index("--model") + 1] if "--model" in args else "claude-fake"
print(json.dumps({"result": "Fake Claude answer.", "session_id": "claude-session", "is_error": False,
                  "modelUsage": {model: {"outputTokens": 10}}}))
"""


class CliTestCase(Sandbox):
    def setUp(self):
        super().setUp()
        self.record = self.tmp / "record.json"
        os.environ["FAKE_RECORD"] = str(self.record)
        os.environ["PEER_CODEX_BIN"] = self.fake("codex", FAKE_CODEX)
        os.environ["PEER_CLAUDE_BIN"] = self.fake("claude", FAKE_CLAUDE)

    def fake(self, name, body):
        path = self.tmp / "bin" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(f"#!{sys.executable}\n{body}")
        path.chmod(0o755)
        return str(path)

    def recorded(self):
        return json.loads(self.record.read_text())

    def claude_session(self, rows, session_id="claude-1"):
        folder = peer.CLAUDE_HOME / "projects" / "-proj"
        folder.mkdir(parents=True)
        path = folder / f"{session_id}.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        return path

    def codex_session(self, rows, thread_id="0199-thread"):
        folder = peer.CODEX_HOME / "sessions" / "2026" / "10" / "14"
        folder.mkdir(parents=True)
        path = folder / f"rollout-2026-10-14T10-00-00-{thread_id}.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        return path


CLAUDE_CHAT = [
    human("Should I use SQLite or Postgres here?"),
    claude_assistant({"type": "text", "text": "SQLite is enough for one user."}),
    human("<command-name>/peer</command-name><command-args></command-args>"),
    claude_assistant({"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "peer --from claude"}}),
]

CODEX_CHAT = [
    {"type": "session_meta", "payload": {"cwd": str(Path.cwd())}},
    codex_event({"type": "UserMessage", "content": [{"type": "text", "text": "Is this regex safe?"}]}),
    codex_event({"type": "AgentMessage", "content": [{"type": "Text", "text": "It can backtrack badly."}]}),
    codex_event({"type": "UserMessage", "content": [{"type": "text", "text": "$peer"}]}),
]


class ClaudeToCodexTests(CliTestCase):
    def setUp(self):
        super().setUp()
        self.claude_session(CLAUDE_CHAT)
        os.environ["CLAUDE_CODE_SESSION_ID"] = "claude-1"

    def test_bare_peer_answers_the_previous_message(self):
        code, out, err = self.run_main("--from", "claude")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines()[:3], ["## Codex's answer", "", "Model: fake-model | Effort: low"])
        self.assertIn("Fake Codex answer.", out)
        self.assertTrue(out.rstrip().endswith("Continue with Codex: codex resume fake-session"))
        rec = self.recorded()
        self.assertIn('<latest_input from="User">\nShould I use SQLite or Postgres here?', rec["prompt"])
        self.assertNotIn("SQLite is enough", rec["prompt"])  # the host's reply is withheld
        self.assertNotIn("/peer", rec["prompt"])
        self.assertEqual(rec["args"][:3], ["exec", "--sandbox", "read-only"])

    def test_child_environment_is_scrubbed(self):
        self.run_main("--from", "claude")
        env = self.recorded()["env"]
        self.assertEqual(
            env, {"PEER_ACTIVE": "1", "CLAUDE_CODE_SESSION_ID": None, "CODEX_THREAD_ID": None, "CLAUDECODE": None}
        )

    def test_model_and_effort_reach_codex(self):
        code, out, _ = self.run_main("--from", "claude", "--model", "gpt-x", "--effort", "high")
        args = self.recorded()["args"]
        self.assertEqual(code, 0)
        self.assertIn("-m", args)
        self.assertEqual(args[args.index("-m") + 1], "gpt-x")
        self.assertIn("model_reasoning_effort=high", args)
        self.assertIn("Model: gpt-x", out)

    def test_review_and_question_modes(self):
        self.run_main("--from", "claude", "review", "cost")
        prompt = self.recorded()["prompt"]
        self.assertIn("SQLite is enough", prompt)
        self.assertIn("focus on: cost", prompt)
        self.run_main("--from", "claude", "what about backups?")
        self.assertIn("<question>\nwhat about backups?\n</question>", self.recorded()["prompt"])

    def test_details_header(self):
        code, out, _ = self.run_main("--from", "claude", "--details", "--assess")
        lines = out.splitlines()
        self.assertEqual(code, 0)
        self.assertTrue(lines[2].startswith("Peer command: peer --from claude --details --assess"))
        self.assertTrue(lines[3].startswith("Codex command: "))
        self.assertIn("Context: 1 turn from Claude Code", out)
        self.assertIn("everything, untrimmed", out)
        self.assertIn("Assessment: on (from --assess)", out)

    def test_failure_is_reported_with_the_header(self):
        os.environ["FAKE_MODE"] = "fail"
        code, out, err = self.run_main("--from", "claude")
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith("## Codex's answer"))
        self.assertIn("Codex failed:\nERROR: model not available", err)

    def test_timeout(self):
        os.environ["FAKE_MODE"] = "slow"
        code, _, err = self.run_main("--from", "claude", "--timeout", "1")
        self.assertEqual(code, 1)
        self.assertIn("did not finish within 1s", err)

    def test_dry_run_sends_nothing(self):
        code, out, _ = self.run_main("--from", "claude", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("--- prompt ---", out)
        self.assertFalse(self.record.exists())


class ImageTests(CliTestCase):
    def test_pasted_image_is_readable_during_the_run_and_removed_after(self):
        rows = [
            human(
                [
                    {"type": "text", "text": "What colour?"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG_1PX}},
                ]
            ),
            human("<command-name>/peer</command-name>"),
        ]
        self.claude_session(rows)
        os.environ["CLAUDE_CODE_SESSION_ID"] = "claude-1"
        code, _, _ = self.run_main("--from", "claude")
        rec = self.recorded()
        self.assertEqual(code, 0)
        self.assertEqual(rec["images_exist"], [True])
        self.assertFalse(os.path.exists(rec["image_paths"][0]))


class CodexToClaudeTests(CliTestCase):
    def setUp(self):
        super().setUp()
        self.codex_session(CODEX_CHAT)
        os.environ["CODEX_THREAD_ID"] = "0199-thread"

    def test_bare_peer_answers_the_previous_message(self):
        code, out, err = self.run_main("--from", "codex", "--model", "opus")
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.startswith("## Claude Code's answer\n\nModel: opus\n"))
        self.assertIn("Continue with Claude Code: claude --resume claude-session", out)
        rec = self.recorded()
        self.assertIn("Is this regex safe?", rec["prompt"])
        self.assertNotIn("backtrack badly", rec["prompt"])
        self.assertIn("--disallowedTools", rec["args"])
        self.assertEqual(rec["args"][rec["args"].index("--model") + 1], "opus")

    def test_claude_runs_in_the_session_folder(self):
        self.run_main("--from", "codex")
        self.assertEqual(os.path.realpath(self.recorded()["cwd"]), os.path.realpath(str(Path.cwd())))

    def test_claude_error_is_reported(self):
        os.environ["FAKE_MODE"] = "fail"
        code, _, err = self.run_main("--from", "codex")
        self.assertEqual(code, 1)
        self.assertIn("Claude Code failed: Not logged in", err)


class SessionDiscoveryTests(CliTestCase):
    def test_refuses_to_run_inside_a_peer_session(self):
        os.environ["PEER_ACTIVE"] = "1"
        code, _, err = self.run_main("--from", "claude")
        self.assertEqual(code, 2)
        self.assertIn("refusing", err)

    def test_both_session_variables_need_from(self):
        os.environ["CLAUDE_CODE_SESSION_ID"] = "a"
        os.environ["CODEX_THREAD_ID"] = "b"
        code, _, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("Pass --from claude or --from codex", err)

    def test_outside_a_session_a_question_needs_to(self):
        code, _, err = self.run_main("hello")
        self.assertEqual(code, 1)
        self.assertIn("pass --to claude or --to codex", err)
        code, _, _ = self.run_main("--to", "codex", "hello")
        self.assertEqual(code, 0)
        self.assertTrue(self.recorded()["prompt"].endswith("hello"))

    def test_last_skips_sessions_peer_started(self):
        folder = peer.claude_project_dir(os.getcwd())
        folder.mkdir(parents=True)
        real = folder / "real.jsonl"
        real.write_text(json.dumps(claude_user("my real question")) + "\n")
        mine = folder / "mine.jsonl"
        mine.write_text(json.dumps(claude_user(peer.MARKER + "\nYou are Claude Code")) + "\n")
        os.utime(real, (1, 1))  # the peer-started one is newer
        self.assertEqual(peer.find_latest("claude", os.getcwd()), real)

    def test_session_by_path_and_id(self):
        path = write_jsonl(CODEX_CHAT)
        code, _, _ = self.run_main("--from", "codex", "--session", str(path), "--dry-run")
        self.assertEqual(code, 0)
        self.codex_session(CODEX_CHAT, "0199-by-id")
        session, drop = peer.resolve_session(peer.parse_args(["--session", "0199-by-id"]))
        self.assertEqual((session.tool, drop), ("codex", False))
        code, _, err = self.run_main("--session", "no-such-id")
        self.assertEqual(code, 1)
        self.assertIn("No session file or id matches", err)


class VersionTests(unittest.TestCase):
    def test_version(self):
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            peer.main(["--version"])
        self.assertEqual(out.getvalue().strip(), f"peer {peer.__version__}")


if __name__ == "__main__":
    unittest.main()
