"""Settings, peer config and peer models."""

import json
import tempfile
import unittest
from pathlib import Path

from helpers import peer


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
        self.assertEqual((answer.model, answer.effort, answer.resume), ("gpt-6.1-sol", "xhigh", "codex resume abc-123"))
        data = {
            "result": "Hi",
            "session_id": "s1",
            "modelUsage": {"claude-haiku": {"outputTokens": 10}, "claude-opus": {"outputTokens": 900}},
        }
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
        catalog = {
            "models": [
                {
                    "slug": "old",
                    "visibility": "list",
                    "priority": 5,
                    "description": "Older.",
                    "supported_reasoning_levels": [{"effort": "low"}],
                },
                {"slug": "hidden", "visibility": "hide", "priority": 0},
                {
                    "slug": "best",
                    "visibility": "list",
                    "priority": 1,
                    "description": "Best.",
                    "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}],
                },
            ]
        }
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
        self.assertIn("| `best` | low, high | Best. | recommended |", text)
        self.assertIn("| `old` | low | Older. | current |", text)
        self.assertIn("| Model | Effort levels | Description | Note |", text)


if __name__ == "__main__":
    unittest.main()
