import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import peer  # noqa: E402


def write_jsonl(rows):
    fh = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    for row in rows:
        fh.write(json.dumps(row) + "\n")
    fh.close()
    return Path(fh.name)


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
    claude_user("<command-name>/peer</command-name><command-message>peer</command-message><command-args></command-args>"),
    claude_user("Skill body", isMeta=True),
    claude_assistant({"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "peer --from claude"}}),
]


def codex_event(item):
    return {"type": "event_msg", "payload": {"type": "item_completed", "item": item}}


CODEX_ROWS = [
    {"type": "session_meta", "payload": {"id": "abc", "cwd": "/proj"}},
    {"type": "response_item", "payload": {"type": "message", "role": "user",
                                          "content": [{"type": "input_text", "text": "# AGENTS.md instructions"}]}},
    codex_event({"type": "UserMessage", "content": [{"type": "text", "text": "Why is the build failing?"}]}),
    codex_event({"type": "Reasoning", "summary_text": ["thinking"]}),
    codex_event({"type": "CommandExecution", "command": ["/bin/zsh", "-lc", "npm run build"],
                 "aggregated_output": "TypeError in a.ts", "exit_code": 1}),
    codex_event({"type": "AgentMessage", "content": [{"type": "Text", "text": "A type error in a.ts."}]}),
    codex_event({"type": "FileChange", "changes": {"/proj/a.ts": {"type": "update"}}}),
    codex_event({"type": "UserMessage", "content": [{"type": "text", "text": "$peer"}]}),
]


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


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.session = peer.parse_claude(write_jsonl(CLAUDE_ROWS))

    def test_ask_withholds_last_reply_and_drops_request(self):
        req = peer.build_request(self.session, "codex", "ask", "", True, peer.Limits.trimmed(budget=100_000))
        self.assertEqual(req.target, "What about invalidation?")
        self.assertIn("Use an LRU cache.", req.prompt)
        self.assertNotIn("Expire on write.", req.prompt)
        self.assertNotIn("/peer", req.prompt)
        self.assertTrue(req.prompt.startswith(peer.MARKER))

    def test_review_includes_last_reply(self):
        req = peer.build_request(self.session, "codex", "review", "edge cases", True, peer.Limits.trimmed(budget=100_000))
        self.assertIn("Expire on write.", req.prompt)
        self.assertIn("focus on: edge cases", req.prompt)

    def test_question_uses_history_as_context(self):
        req = peer.build_request(self.session, "codex", "question", "Is Redis overkill?", True, peer.Limits.trimmed(budget=100_000))
        self.assertIn("Expire on write.", req.prompt)
        self.assertIn("<question>\nIs Redis overkill?", req.prompt)

    def test_fresh_question(self):
        empty = peer.Session("claude", Path("."), "/proj", [])
        req = peer.build_request(empty, "codex", "question", "Explain CRDTs", False, peer.Limits.trimmed(budget=100_000))
        self.assertNotIn("<transcript>", req.prompt)
        self.assertTrue(req.prompt.endswith("Explain CRDTs"))

    def test_current_turn_is_answered_when_not_dropped(self):
        req = peer.build_request(self.session, "codex", "ask", "", False, peer.Limits.trimmed(budget=100_000))
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


class EnvTests(unittest.TestCase):
    def test_child_env_scrubs_session_variables(self):
        saved = dict(peer.os.environ)
        try:
            peer.os.environ.update({"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "s", "CODEX_THREAD_ID": "t",
                                    "CLAUDE_CODE_USE_BEDROCK": "1", "HOME_KEEP": "x"})
            env = peer.child_env()
        finally:
            peer.os.environ.clear()
            peer.os.environ.update(saved)
        self.assertNotIn("CLAUDECODE", env)
        self.assertNotIn("CLAUDE_CODE_SESSION_ID", env)
        self.assertNotIn("CODEX_THREAD_ID", env)
        self.assertEqual(env["CLAUDE_CODE_USE_BEDROCK"], "1")
        self.assertEqual(env["PEER_ACTIVE"], "1")

    def test_peer_sessions_are_recognized(self):
        mine = write_jsonl([claude_user(peer.MARKER + "\nYou are Claude Code")])
        theirs = write_jsonl([claude_user("normal question mentioning " + peer.MARKER)])
        self.assertTrue(peer.is_peer_session(mine))
        self.assertFalse(peer.is_peer_session(theirs))


def human(content):
    return claude_user(content, promptSource="sdk", origin={"kind": "human"}, turnOrigin="human")


INJECTED_ROWS = [
    human("Run the migration."),
    claude_assistant({"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "migrate"}}),
    claude_user([{"type": "tool_result", "tool_use_id": "b1", "content": "started in background"}]),
    claude_assistant({"type": "text", "text": "Started it."}),
    claude_user("<task-notification><status>completed</status></task-notification>", promptSource="system",
                origin={"kind": "task-notification"}, turnOrigin="task_notification"),
    claude_assistant({"type": "tool_use", "id": "q1", "name": "AskUserQuestion",
                      "input": {"questions": [{"question": "Apply to prod?"}]}}),
    claude_user([{"type": "tool_result", "tool_use_id": "q1", "content": 'The user answered: "Apply to prod?"="No"'}]),
    claude_assistant({"type": "text", "text": "Migration done; prod untouched."}),
    claude_user("This session is being continued from a previous conversation. Summary: migration",
                isCompactSummary=True),
    claude_user('<scheduled-task name="nightly">check logs</scheduled-task>', promptSource="system",
                turnOrigin="scheduled"),
    claude_assistant({"type": "text", "text": "Logs are clean."}),
    claude_user('<project_claude_message session="s">ping</project_claude_message>', promptSource="system",
                origin={"kind": "task-notification", "subkind": "projects-relay"}, turnOrigin="peer"),
    claude_assistant({"type": "text", "text": "Noted the ping."}),
    human("<command-name>/peer</command-name><command-args></command-args>"),
    claude_assistant({"type": "tool_use", "id": "p1", "name": "Bash", "input": {"command": "peer --from claude"}}),
    claude_user("<task-notification>peer finished</task-notification>", promptSource="system",
                origin={"kind": "task-notification"}, turnOrigin="task_notification"),
]


class InjectedInputTests(unittest.TestCase):
    def setUp(self):
        self.session = peer.parse_claude(write_jsonl(INJECTED_ROWS))

    def test_labels(self):
        labelled = [(i.role, i.label) for i in self.session.items if i.role in ("user", "input", "summary")]
        self.assertEqual(labelled, [
            ("user", ""),
            ("input", "Task notification"),
            ("input", peer.ANSWER_LABEL),
            ("summary", peer.SUMMARY_LABEL),
            ("input", "Scheduled task"),
            ("input", "Message from another Claude session"),
            ("user", ""),
            ("input", "Task notification"),
        ])
        self.assertIn(("tool", "AskUserQuestion: Apply to prod?"),
                      [(i.role, i.text) for i in self.session.items])

    def test_injected_inputs_mark_turns(self):
        turns = peer.split_turns(self.session.items)
        self.assertEqual([t[0].heading for t in turns], [
            "User", "Task notification", peer.ANSWER_LABEL, "Scheduled task",
            "Message from another Claude session", "User", "Task notification",
        ])

    def test_drop_from_last_typed_message(self):
        req = peer.build_request(self.session, "codex", "ask", "", True, peer.Limits.trimmed(budget=100_000))
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
        self.assertEqual(peer.injected_label({"promptSource": "sdk", "origin": {"kind": "task-notification"}}, "x"),
                         "System input")

    def test_codex_drop_unchanged(self):
        session = peer.parse_codex(write_jsonl(CODEX_ROWS))
        req = peer.build_request(session, "claude", "ask", "", True, peer.Limits.trimmed(budget=100_000))
        self.assertEqual(req.target, "Why is the build failing?")
        self.assertEqual(req.target_label, "User")


PNG_1PX = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="


def attachment(**fields):
    return {"type": "attachment", "cwd": "/proj", "attachment": fields}


CONTEXT_ROWS = [
    attachment(type="instructions", files=[{"path": "/home/u/.claude/CLAUDE.md", "content": "rules"}]),
    human([{"type": "text", "text": "What is in this screenshot?"},
           {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG_1PX}}]),
    attachment(type="file", filename="/proj/notes.md",
               content={"type": "text", "file": {"filePath": "/proj/notes.md", "content": "remember the milk"}}),
    claude_assistant({"type": "text", "text": "A login form."}),
    attachment(type="queued_command", commandMode="prompt", origin={"kind": "human"},
               prompt="also check the footer"),
    attachment(type="queued_command", commandMode="task-notification", origin={"kind": "task-notification"},
               prompt="<task-notification>build done</task-notification>"),
    # Older versions wrote queued messages twice, as an attachment and a user row.
    attachment(type="queued_command", commandMode="prompt", origin={"kind": "human"}, prompt="and the header"),
    human("and the header"),
    claude_assistant({"type": "text", "text": "Footer and header look fine."}),
]


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
        req = peer.build_request(self.session, "codex", "review", "", False, peer.Limits.trimmed(budget=100_000))
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
            codex_event({"type": "UserMessage", "content": [
                {"type": "text", "text": "look"}, {"type": "local_image", "path": "/tmp/shot.png"},
                {"type": "image", "image_url": "data:image/png;base64," + PNG_1PX}]}),
            codex_event({"type": "ImageView", "path": "file:///proj/logo.jpg"}),
            codex_event({"type": "Extension", "kind": "web.search", "query": "codex docs"}),
        ]
        session = peer.parse_codex(write_jsonl(rows))
        texts = [i.text for i in session.items]
        self.assertEqual(texts[0], "look\n[image: /tmp/shot.png]\n[image #0]")
        self.assertIn("view image: /proj/logo.jpg", texts)
        self.assertIn("web.search: codex docs", texts)
        self.assertEqual(len(session.images), 1)


class LimitTests(unittest.TestCase):
    def test_default_sends_everything(self):
        items = [peer.Item("user", "q " + "x" * 30_000), peer.Item("tool", "Bash: " + "y" * 5_000),
                 peer.Item("result", "z" * 9_000), peer.Item("assistant", "done")]
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


class SettingTests(unittest.TestCase):
    def setUp(self):
        self.saved = dict(peer.os.environ)
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Path(self.tmp.name) / "config.json"
        peer.os.environ["PEER_CONFIG"] = str(self.config)
        for key in ("PEER_CODEX_MODEL", "PEER_CODEX_EFFORT", "PEER_CLAUDE_MODEL"):
            peer.os.environ.pop(key, None)

    def tearDown(self):
        peer.os.environ.clear()
        peer.os.environ.update(self.saved)
        self.tmp.cleanup()

    def test_precedence(self):
        self.config.write_text(json.dumps({"codex": {"model": "from-file", "effort": "low"}}))
        config = peer.load_config()
        self.assertEqual(peer.resolve_setting("codex", "model", None, config).value, "from-file")
        peer.os.environ["PEER_CODEX_MODEL"] = "from-env"
        self.assertEqual(peer.resolve_setting("codex", "model", None, config).source, "PEER_CODEX_MODEL")
        flag = peer.resolve_setting("codex", "model", "from-flag", config)
        self.assertEqual((flag.value, flag.source), ("from-flag", "--model"))
        self.assertEqual(peer.resolve_setting("codex", "effort", None, config).value, "low")
        default = peer.resolve_setting("claude", "model", None, config)
        self.assertEqual((default.value, default.source), (None, "Claude Code default"))

    def test_assess_setting(self):
        peer.os.environ.pop("PEER_ASSESS", None)
        self.assertEqual(peer.resolve_assess(None, {}).value, "off")
        self.assertEqual(peer.resolve_assess(None, {"assess": True}).value, "on")
        peer.os.environ["PEER_ASSESS"] = "0"
        self.assertEqual(peer.resolve_assess(None, {"assess": True}).value, "off")
        on = peer.resolve_assess(True, {})
        self.assertEqual((on.value, on.source), ("on", "--assess"))
        self.assertIs(peer.parse_args(["--no-assess"]).assess, False)
        self.assertIsNone(peer.parse_args([]).assess)

    def test_bad_config(self):
        self.config.write_text("{not json")
        with self.assertRaises(peer.PeerError):
            peer.load_config()

    def test_commands_carry_model_and_effort(self):
        codex = peer.codex_command("codex", "/proj", Path("/t/a.md"), "gpt-x", "high", False)
        self.assertEqual(codex[-5:], ["-m", "gpt-x", "-c", "model_reasoning_effort=high", "-"])
        claude = peer.claude_command("claude", "opus", "max", True)
        self.assertEqual(claude[-5:], ["--model", "opus", "--effort", "max", "--no-session-persistence"])

    def test_run_output_parsing(self):
        stderr = "model: gpt-6.1-sol\nreasoning effort: xhigh\nsession id: abc-123\n"
        answer = peer.parse_codex_run(0, stderr, "Answer", False)
        self.assertEqual((answer.model, answer.effort, answer.resume),
                         ("gpt-6.1-sol", "xhigh", "codex resume abc-123"))
        data = {"result": "Hi", "session_id": "s1", "modelUsage": {
            "claude-haiku": {"outputTokens": 10}, "claude-opus": {"outputTokens": 900}}}
        answer = peer.parse_claude_run(0, json.dumps(data), "", False)
        self.assertEqual((answer.model, answer.resume), ("claude-opus", "claude --resume s1"))
        with self.assertRaises(peer.PeerError):
            peer.parse_codex_run(1, "ERROR: model not found\n", "", False)


class ConfigCommandTests(unittest.TestCase):
    def setUp(self):
        self.saved = dict(peer.os.environ)
        self.tmp = tempfile.TemporaryDirectory()
        peer.os.environ["PEER_CONFIG"] = str(Path(self.tmp.name) / "sub" / "config.json")
        for key in ("PEER_CODEX_MODEL", "PEER_ASSESS", "PEER_ACTIVE"):
            peer.os.environ.pop(key, None)

    def tearDown(self):
        peer.os.environ.clear()
        peer.os.environ.update(self.saved)
        self.tmp.cleanup()

    def run_main(self, *argv):
        import contextlib
        import io
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = peer.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_set_and_clear(self):
        code, out, _ = self.run_main("config", "codex", "model", "gpt-x")
        self.assertEqual(code, 0)
        self.assertIn("When Codex answers: model gpt-x (from peer config)", out)
        self.run_main("config", "assess", "on")
        self.assertEqual(peer.load_config(), {"codex": {"model": "gpt-x"}, "assess": True})
        self.run_main("config", "codex", "model", "default")
        self.assertEqual(peer.load_config(), {"assess": True})
        code, out, _ = self.run_main("config", "budget", "250000")
        self.assertIn("Context budget: 250,000 characters (from peer config)", out)
        code, out, _ = self.run_main("config", "trim", "on")
        self.assertIn("Trim: on (from peer config)", out)

    def test_bad_setting(self):
        code, _, err = self.run_main("config", "codex", "colour", "red")
        self.assertEqual(code, 1)
        self.assertIn("Not a setting", err)


class ModelListTests(unittest.TestCase):
    def test_codex_catalog_order_and_tags(self):
        catalog = {"models": [
            {"slug": "old", "visibility": "list", "priority": 5, "description": "Older.",
             "supported_reasoning_levels": [{"effort": "low"}]},
            {"slug": "hidden", "visibility": "hide", "priority": 0},
            {"slug": "best", "visibility": "list", "priority": 1, "description": "Best.",
             "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}]},
        ]}
        tmp = tempfile.TemporaryDirectory()
        fake = Path(tmp.name) / "codex"
        fake.write_text("#!/bin/sh\ncat <<'JSON'\n" + json.dumps(catalog) + "\nJSON\n")
        fake.chmod(0o755)
        saved = dict(peer.os.environ)
        try:
            peer.os.environ["PEER_CODEX_BIN"] = str(fake)
            peer.os.environ["PEER_CONFIG"] = str(Path(tmp.name) / "c.json")
            peer.os.environ["PEER_CODEX_MODEL"] = "old"
            text = peer.list_models(["codex"])
        finally:
            peer.os.environ.clear()
            peer.os.environ.update(saved)
            tmp.cleanup()
        self.assertNotIn("hidden", text)
        self.assertLess(text.index("best"), text.index("old"))
        self.assertIn("Best.  [recommended]", text)
        self.assertIn("Older.  [current]", text)
        self.assertIn("effort: low/high", text)


class HeaderTests(unittest.TestCase):
    def test_short_header(self):
        session = peer.parse_claude(write_jsonl(INJECTED_ROWS))
        args = peer.parse_args([])
        saved = dict(peer.os.environ)
        try:
            peer.os.environ["PEER_CODEX_BIN"] = "/bin/codex-test"
            run = peer.Run("codex", "/proj", peer.Setting(None, "Codex default"),
                           peer.Setting(None, "Codex default"), args)
        finally:
            peer.os.environ.clear()
            peer.os.environ.update(saved)
        run.close()
        answer = peer.Answer("Hi", "s", "gpt-x", "high", None)
        typed = peer.build_request(peer.parse_codex(write_jsonl(CODEX_ROWS)), "claude", "ask", "", True,
                                   peer.Limits.trimmed(budget=100_000))
        off = peer.Setting("off", "default")
        self.assertEqual(peer.short_header(run, typed, answer, off),
                         "## Codex's answer\n\nModel: gpt-x | Effort: high")
        injected = peer.build_request(session, "codex", "ask", "", True, peer.Limits.trimmed(budget=100_000))
        text = peer.short_header(run, injected, answer, peer.Setting("on", "--assess"))
        self.assertIn("Model: gpt-x | Effort: high | Assessment: on", text)
        self.assertIn("Answering: Message from another Claude session:", text)
        self.assertEqual(peer.short_header(run, typed, None, off), "## Codex's answer\n\nModel: Codex default")

    def test_details_switch(self):
        saved = dict(peer.os.environ)
        try:
            peer.os.environ.pop("PEER_DETAILS", None)
            self.assertEqual(peer.resolve_switch("details", None, {}).value, "off")
            self.assertEqual(peer.resolve_switch("details", None, {"details": True}).value, "on")
            self.assertIs(peer.parse_args(["--details"]).details, True)
        finally:
            peer.os.environ.clear()
            peer.os.environ.update(saved)

    def test_dry_run_header_on_stdout(self):
        import contextlib
        import io
        path = write_jsonl(INJECTED_ROWS)
        saved = dict(peer.os.environ)
        out, err = io.StringIO(), io.StringIO()
        try:
            peer.os.environ.pop("PEER_ACTIVE", None)
            peer.os.environ["PEER_CODEX_BIN"] = "/bin/codex-test"
            peer.os.environ["PEER_CONFIG"] = "/nonexistent/peer.json"
            peer.os.environ.pop("PEER_CODEX_MODEL", None)
            peer.os.environ.pop("PEER_CODEX_EFFORT", None)
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = peer.main(["--from", "claude", "--session", str(path), "--model", "gpt-x", "--dry-run"])
        finally:
            peer.os.environ.clear()
            peer.os.environ.update(saved)
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
        self.assertEqual(lines[8], "Assessment: off (default)")
        self.assertEqual(err.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
