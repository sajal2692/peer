"""Shared fixtures for the peer tests: transcript builders and a sandboxed environment."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from peer import common
from peer.cli import main
from peer.request import Limits

TRIMMED = Limits.trimmed(budget=100_000)


def write_jsonl(rows):
    fd, name = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    return Path(name)


def claude_user(content, **extra):
    return {"type": "user", "cwd": "/proj", "message": {"role": "user", "content": content}, **extra}


def claude_assistant(*blocks):
    return {"type": "assistant", "cwd": "/proj", "message": {"role": "assistant", "content": list(blocks)}}


CLAUDE_ROWS = [
    claude_user("How should I cache this?<system-reminder>ignore me</system-reminder>"),
    claude_assistant({"type": "thinking", "thinking": "hidden"}),
    claude_assistant({"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/proj/app.py"}}),
    claude_user([{"type": "tool_result", "tool_use_id": "t1", "content": "def handler(): ..."}]),
    claude_assistant({"type": "text", "text": "Use an LRU cache."}),
    claude_user("<local-command-caveat>Caveat</local-command-caveat>", isMeta=True),
    claude_user("<command-name>/model</command-name><command-args>opus</command-args>"),
    claude_user("<local-command-stdout>Set model</local-command-stdout>"),
    claude_user("Summary of earlier turns", isCompactSummary=True),
    claude_user("What about invalidation?"),
    claude_assistant({"type": "text", "text": "Expire on write."}),
    {"type": "assistant", "isSidechain": True, "message": {"content": [{"type": "text", "text": "subagent"}]}},
    claude_user(
        "<command-name>/peer</command-name><command-message>peer</command-message><command-args></command-args>"
    ),
    claude_user("Skill body", isMeta=True),
    claude_assistant({"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "peer --from claude"}}),
]


def codex_event(item):
    return {"type": "event_msg", "payload": {"type": "item_completed", "item": item}}


CODEX_ROWS = [
    {"type": "session_meta", "payload": {"id": "abc", "cwd": "/proj"}},
    {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "# AGENTS.md instructions"}],
        },
    },
    codex_event({"type": "UserMessage", "content": [{"type": "text", "text": "Why is the build failing?"}]}),
    codex_event({"type": "Reasoning", "summary_text": ["thinking"]}),
    codex_event(
        {
            "type": "CommandExecution",
            "command": ["/bin/zsh", "-lc", "npm run build"],
            "aggregated_output": "TypeError in a.ts",
            "exit_code": 1,
        }
    ),
    codex_event({"type": "AgentMessage", "content": [{"type": "Text", "text": "A type error in a.ts."}]}),
    codex_event({"type": "FileChange", "changes": {"/proj/a.ts": {"type": "update"}}}),
    codex_event({"type": "UserMessage", "content": [{"type": "text", "text": "$peer"}]}),
]


def human(content):
    return claude_user(content, promptSource="sdk", origin={"kind": "human"}, turnOrigin="human")


INJECTED_ROWS = [
    human("Run the migration."),
    claude_assistant({"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "migrate"}}),
    claude_user([{"type": "tool_result", "tool_use_id": "b1", "content": "started in background"}]),
    claude_assistant({"type": "text", "text": "Started it."}),
    claude_user(
        "<task-notification><status>completed</status></task-notification>",
        promptSource="system",
        origin={"kind": "task-notification"},
        turnOrigin="task_notification",
    ),
    claude_assistant(
        {
            "type": "tool_use",
            "id": "q1",
            "name": "AskUserQuestion",
            "input": {"questions": [{"question": "Apply to prod?"}]},
        }
    ),
    claude_user([{"type": "tool_result", "tool_use_id": "q1", "content": 'The user answered: "Apply to prod?"="No"'}]),
    claude_assistant({"type": "text", "text": "Migration done; prod untouched."}),
    claude_user(
        "This session is being continued from a previous conversation. Summary: migration", isCompactSummary=True
    ),
    claude_user(
        '<scheduled-task name="nightly">check logs</scheduled-task>', promptSource="system", turnOrigin="scheduled"
    ),
    claude_assistant({"type": "text", "text": "Logs are clean."}),
    claude_user(
        '<project_claude_message session="s">ping</project_claude_message>',
        promptSource="system",
        origin={"kind": "task-notification", "subkind": "projects-relay"},
        turnOrigin="peer",
    ),
    claude_assistant({"type": "text", "text": "Noted the ping."}),
    human("<command-name>/peer</command-name><command-args></command-args>"),
    claude_assistant({"type": "tool_use", "id": "p1", "name": "Bash", "input": {"command": "peer --from claude"}}),
    claude_user(
        "<task-notification>peer finished</task-notification>",
        promptSource="system",
        origin={"kind": "task-notification"},
        turnOrigin="task_notification",
    ),
]


PNG_1PX = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="


def attachment(**fields):
    return {"type": "attachment", "cwd": "/proj", "attachment": fields}


CONTEXT_ROWS = [
    attachment(type="instructions", files=[{"path": "/home/u/.claude/CLAUDE.md", "content": "rules"}]),
    human(
        [
            {"type": "text", "text": "What is in this screenshot?"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG_1PX}},
        ]
    ),
    attachment(
        type="file",
        filename="/proj/notes.md",
        content={"type": "text", "file": {"filePath": "/proj/notes.md", "content": "remember the milk"}},
    ),
    claude_assistant({"type": "text", "text": "A login form."}),
    attachment(type="queued_command", commandMode="prompt", origin={"kind": "human"}, prompt="also check the footer"),
    attachment(
        type="queued_command",
        commandMode="task-notification",
        origin={"kind": "task-notification"},
        prompt="<task-notification>build done</task-notification>",
    ),
    # Older versions wrote queued messages twice, as an attachment and a user row.
    attachment(type="queued_command", commandMode="prompt", origin={"kind": "human"}, prompt="and the header"),
    human("and the header"),
    claude_assistant({"type": "text", "text": "Footer and header look fine."}),
]


class Sandbox(unittest.TestCase):
    """Runs each test with a clean peer environment: no session variables, an empty config,
    and empty Claude Code and Codex homes under a temporary folder."""

    def setUp(self):
        self._env = dict(os.environ)
        self._homes = (common.CLAUDE_HOME, common.CODEX_HOME)
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        for key in list(os.environ):
            if key.startswith(("PEER_", "CLAUDE_CODE_", "CODEX_")) or key == "CLAUDECODE":
                del os.environ[key]
        os.environ["PEER_CONFIG"] = str(self.tmp / "config" / "peer.json")
        common.CLAUDE_HOME = self.tmp / "claude"
        common.CODEX_HOME = self.tmp / "codex"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        common.CLAUDE_HOME, common.CODEX_HOME = self._homes
        self._tmp.cleanup()

    def run_main(self, *argv):
        """Runs peer's main() and returns (exit code, stdout, stderr)."""
        import contextlib
        import io

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()
