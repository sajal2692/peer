#!/usr/bin/env python3
"""Checks that the installed Codex and Claude Code CLIs still accept what peer sends them.

Needs no accounts, so it runs in CI against the newest releases as well as on a Mac. It checks:

- every flag peer passes to `codex exec` and `claude -p` is still in their --help;
- `codex features list` still parses, every feature peer switches off still exists, and no
  feature that is on by default is missing from scripts/codex_features_reviewed.txt;
- `codex debug models` still returns a model list, and `claude --help` still lists effort levels.

Transcript formats and real answers need a signed-in account; scripts/upstream_smoke.py
covers those on a Mac. Exits 1 if any check fails.

    python3 scripts/check_upstream.py [--codex PATH] [--claude PATH]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from peer.models import claude_efforts_from_help, codex_models  # noqa: E402
from peer.runner import (  # noqa: E402
    CODEX_RISKY_FEATURES,
    child_env,
    claude_command,
    codex_command,
    find_binary,
    parse_feature_rows,
)

REVIEWED_FEATURES = REPO / "scripts" / "codex_features_reviewed.txt"


def run(cmd: list[str], env: dict | None = None) -> str:
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        timeout=60,
        env=env or child_env(),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"`{' '.join(cmd[1:])}` exited {proc.returncode}: {proc.stderr.strip()[-500:]}")
    return proc.stdout


def flags_of(cmd: list[str]) -> list[str]:
    """The option names in a command line peer builds, without their values."""
    return list(dict.fromkeys(t for t in cmd if t.startswith("-") and t != "-"))


def missing_flags(flags: list[str], help_text: str) -> list[str]:
    return [f for f in flags if not re.search(rf"(?<![\w-]){re.escape(f)}(?![\w-])", help_text)]


def reviewed_features() -> set[str]:
    lines = REVIEWED_FEATURES.read_text(encoding="utf-8").splitlines()
    return {line.split("#")[0].strip() for line in lines if line.split("#")[0].strip()}


def feature_problems(rows: list[list[str]], reviewed: set[str]) -> tuple[list[str], list[str]]:
    """Failures and notes about Codex's feature list, from peer's point of view.

    A renamed risky feature shows up as a new feature that is on by default, so that is the
    failure. A risky feature that is simply absent is only a note: older Codex versions lack
    some of them, and peer only switches off the ones Codex lists.
    """
    if not rows:
        return ["`codex features list` printed no rows peer can read"], []
    names = {row[0] for row in rows}
    failures, notes = [], []
    new = sorted(row[0] for row in rows if row[-1] == "true" and row[0] not in reviewed | set(CODEX_RISKY_FEATURES))
    if new:
        failures.append(
            "new features on by default: "
            + ", ".join(new)
            + ". If one can act outside the read-only sandbox, add it to CODEX_RISKY_FEATURES in "
            "src/peer/runner.py; otherwise add it to scripts/codex_features_reviewed.txt."
        )
    gone = [f for f in CODEX_RISKY_FEATURES if f not in names]
    if gone:
        notes.append("not in this Codex (fine if it is older, check for a rename if newer): " + ", ".join(gone))
    return failures, notes


def check_codex(binary: str) -> tuple[list[str], list[str]]:
    failures, notes = [], []
    # An empty CODEX_HOME, so the defaults are checked rather than this machine's config.
    with tempfile.TemporaryDirectory(prefix="peer-check-") as home:
        env = {**child_env(), "CODEX_HOME": home}
        try:
            help_text = run([binary, "exec", "--help"], env)
            cmd = codex_command(binary, "/tmp", Path("/tmp/a"), "m", "e", True, ["--disable", "x", "-c", "k=v"])
            gone = missing_flags(flags_of(cmd), help_text)
            if gone:
                failures.append(f"`codex exec` no longer accepts: {', '.join(gone)}")
            if "read-only" not in help_text:
                failures.append("`codex exec --sandbox` no longer lists read-only")
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            failures.append(f"codex exec --help: {exc}")
        try:
            run([binary, "resume", "--help"], env)
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            failures.append(f"`codex resume` (peer's Continue with line): {exc}")
        try:
            rows = parse_feature_rows(run([binary, "features", "list"], env))
            found, notes = feature_problems(rows, reviewed_features())
            failures += found
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            failures.append(f"codex features list: {exc}")
    try:
        models = codex_models(binary)
        if not models or not all(m.get("supported_reasoning_levels") for m in models):
            failures.append("`codex debug models` lists no models with effort levels")
    except Exception as exc:  # any failure here is a finding to report
        failures.append(f"codex debug models: {exc}")
    return failures, notes


def check_claude(binary: str) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    try:
        help_text = run([binary, "--help"])
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        return [f"claude --help: {exc}"], []
    cmd = [*claude_command(binary, "m", "e", True, web=True), "--resume"]
    gone = missing_flags(flags_of(cmd), help_text)
    if gone:
        failures.append(f"`claude` no longer accepts: {', '.join(gone)}")
    if not claude_efforts_from_help(help_text):
        failures.append("`claude --help` no longer lists effort levels in the form peer models reads")
    if not re.search(r"--output-format.*?json", help_text, re.S):
        failures.append("`claude --output-format` no longer mentions json")
    return failures, []


def version(binary: str) -> str:
    try:
        return run([binary, "--version"]).strip()
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        return f"unknown ({exc})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--codex", help="Codex binary (default: the one peer would use)")
    parser.add_argument("--claude", help="Claude Code binary (default: the one peer would use)")
    args = parser.parse_args(argv)
    failures = 0
    for tool, path, check in (("codex", args.codex, check_codex), ("claude", args.claude, check_claude)):
        binary = path or find_binary(tool)
        print(f"{tool}: {binary} ({version(binary)})")
        problems, notes = check(binary)
        for problem in problems:
            print(f"  FAIL {problem}")
        for note in notes:
            print(f"  note: {note}")
        if not problems:
            print("  ok")
        failures += len(problems)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
