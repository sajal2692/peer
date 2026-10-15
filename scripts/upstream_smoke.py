#!/usr/bin/env python3
"""Checks peer end to end in both directions with this Mac's signed-in Codex and Claude Code.

For each direction it starts a short session in one tool that holds a random code word, checks
that peer parses that session's transcript, then asks peer to have the other tool recall the
code word. Only the transcript holds the word, so a correct answer shows the history arrived.
It first runs scripts/check_upstream.py on the same binaries.

Each run makes four small model calls: Claude Code on Haiku, Codex on its configured model
at low effort. The sessions it starts are deleted afterwards.

    python3 scripts/upstream_smoke.py               # run now
    python3 scripts/upstream_smoke.py --if-flagged  # run only while the upstream workflow has an issue open
    python3 scripts/upstream_smoke.py --notify      # also show the result as a macOS notification

With --if-flagged it makes no model calls unless the daily upstream workflow has opened an
issue, and then runs once per issue and set of installed versions. Checking for the issue
uses the gh CLI. scripts/install_upstream_watch.sh runs it on a schedule with --if-flagged --notify.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import check_upstream  # noqa: E402

from peer import common  # noqa: E402
from peer.common import TOOL_NAMES, PeerError  # noqa: E402
from peer.runner import (  # noqa: E402
    CLAUDE_FALLBACKS,
    CODEX_BUNDLED,
    child_env,
    claude_command,
    codex_command,
    codex_lockdown,
    find_binary,
    parse_claude_run,
    parse_codex_run,
    run_process,
)
from peer.sessions import claude_project_dir, find_claude_by_id, find_codex_by_id, parse_session  # noqa: E402

STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "peer" / "upstream.json"
TIMEOUT = 600
CLAUDE_MODEL = os.environ.get("PEER_SMOKE_CLAUDE_MODEL", "haiku")
CODEX_MODEL = os.environ.get("PEER_SMOKE_CODEX_MODEL") or None
ISSUE_TITLE = "Upstream check failed"
QUESTION = "What is the code word from earlier in this conversation? Reply with the code word only."


def installed_versions() -> dict[str, str]:
    """Every Codex and Claude Code binary peer might pick, with its version."""
    paths = []
    for tool, extra in (("codex", CODEX_BUNDLED), ("claude", CLAUDE_FALLBACKS)):
        paths += [p for p in [shutil.which(tool), *extra] if p and os.access(p, os.X_OK)]
    return {p: check_upstream.version(p) for p in dict.fromkeys(paths)}


def seed_prompt(word: str) -> str:
    return f"The code word is {word}. Remember it. Reply with just: noted."


def seed_claude(binary: str, cwd: str, word: str) -> str:
    cmd = claude_command(binary, CLAUDE_MODEL, None, ephemeral=False, web=False)
    code, stdout, stderr = run_process(cmd, seed_prompt(word), TIMEOUT, cwd, "Claude Code")
    answer = parse_claude_run(code, stdout, stderr, ephemeral=False)
    if not answer.session_id:
        raise PeerError("Claude Code's JSON output has no session_id.")
    return answer.session_id


def seed_codex(binary: str, cwd: str, word: str) -> str:
    with tempfile.TemporaryDirectory(prefix="peer-smoke-out-") as out:
        out_file = Path(out) / "answer.md"
        cmd = codex_command(binary, cwd, out_file, CODEX_MODEL, "low", False, codex_lockdown(binary))
        code, _, stderr = run_process(cmd, seed_prompt(word), TIMEOUT, None, "Codex")
        text = out_file.read_text(encoding="utf-8").strip() if out_file.exists() else ""
    answer = parse_codex_run(code, stderr, text, ephemeral=False)
    if not answer.session_id:
        raise PeerError("Codex no longer prints a `session id:` line, which peer's Continue with line needs.")
    return answer.session_id


def check_transcript(tool: str, path: Path | None, cwd: str, word: str) -> None:
    name = TOOL_NAMES[tool]
    if not path:
        raise PeerError(f"peer could not find the {name} transcript by its session id.")
    session = parse_session(tool, path)
    if not any(i.role == "user" and word in i.text for i in session.items):
        raise PeerError(f"peer read no user message with the code word from {path}.")
    if not any(i.role == "assistant" for i in session.items):
        raise PeerError(f"peer read no assistant reply from {path}.")
    if session.cwd and os.path.realpath(session.cwd) != cwd:
        raise PeerError(f"peer read the folder as {session.cwd}, expected {cwd}.")


def ask_peer(source: str, session_id: str, cwd: str, word: str) -> None:
    target = "codex" if source == "claude" else "claude"
    cmd = [sys.executable, str(REPO / "peer.py"), "--from", source, "--session", session_id, "--ephemeral"]
    cmd += ["--model", CLAUDE_MODEL] if target == "claude" else (["--model", CODEX_MODEL] if CODEX_MODEL else [])
    cmd += ["--effort", "low"] if target == "codex" else []
    cmd += ["--no-web", "--no-assess", "--", QUESTION]
    env = child_env()
    env.pop("PEER_ACTIVE", None)
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=TIMEOUT + 60)
    if proc.returncode != 0:
        raise PeerError(f"peer exited {proc.returncode}: {proc.stderr.strip()[-1000:]}")
    if f"## {TOOL_NAMES[target]}'s answer" not in proc.stdout:
        raise PeerError(f"peer's output has no answer header:\n{proc.stdout[-1000:]}")
    if word.lower() not in proc.stdout.lower():
        raise PeerError(
            f"{TOOL_NAMES[target]} did not recall the code word, so the history may not have arrived:\n"
            + proc.stdout[-1000:]
        )


def direction(source: str) -> str | None:
    """Runs one direction. Returns None on success, or what went wrong."""
    target = "codex" if source == "claude" else "claude"
    label = f"{TOOL_NAMES[source]} to {TOOL_NAMES[target]}"
    cwd = os.path.realpath(tempfile.mkdtemp(prefix="peer-smoke-"))
    word = f"PEERSMOKE{secrets.token_hex(4).upper()}"
    binary = session_id = None
    try:
        binary = find_binary(source)
        if source == "claude":
            session_id = seed_claude(binary, cwd, word)
            transcript = find_claude_by_id(session_id)
        else:
            session_id = seed_codex(binary, cwd, word)
            transcript = find_codex_by_id(session_id)
        check_transcript(source, transcript, cwd, word)
        ask_peer(source, session_id, cwd, word)
        print(f"  ok {label}")
        return None
    except (PeerError, OSError, subprocess.SubprocessError) as exc:
        print(f"  FAIL {label}: {exc}")
        return f"{label}: {str(exc).splitlines()[0]}"
    finally:
        shutil.rmtree(cwd, ignore_errors=True)
        cleanup(cwd, binary if source == "codex" else None, session_id)


def cleanup(cwd: str, codex: str | None, codex_session: str | None) -> None:
    """Deletes what this run left in each tool's history: the Claude Code project folder for the
    temporary directory (both directions create one), and the Codex session it started."""
    folder = claude_project_dir(cwd)
    if "peer-smoke-" in folder.name and folder.parent == common.CLAUDE_HOME / "projects":
        shutil.rmtree(folder, ignore_errors=True)
    if codex and codex_session:
        subprocess.run(
            [codex, "delete", "--force", codex_session],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=60,
            env=child_env(),
            check=False,
        )


def notify(message: str) -> None:
    script = f'display notification {json.dumps(message)} with title "peer"'
    subprocess.run(["osascript", "-e", script], capture_output=True, check=False)


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def flagged_issue() -> int | None:
    """The open issue the upstream workflow filed, if any. Needs gh, signed in."""
    proc = subprocess.run(
        ["gh", "issue", "list", "--state", "open", "--search", f'in:title "{ISSUE_TITLE}"', "--json", "number"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        raise PeerError(f"gh issue list failed: {proc.stderr.strip()[-500:]}")
    issues = json.loads(proc.stdout or "[]")
    return issues[0]["number"] if issues else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--if-flagged", action="store_true", help="skip unless the upstream workflow has an issue open")
    parser.add_argument("--notify", action="store_true", help="show the result as a macOS notification")
    args = parser.parse_args(argv)

    versions = installed_versions()
    issue = None
    if args.if_flagged:
        try:
            issue = flagged_issue()
        except (PeerError, OSError, subprocess.SubprocessError, ValueError) as exc:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} could not check for an upstream issue: {exc}")
            return 1
        previous = load_state()
        if issue is None or (previous.get("issue") == issue and previous.get("versions") == versions):
            return 0

    print(time.strftime("%Y-%m-%d %H:%M:%S"), "versions:", json.dumps(versions))
    failures = []
    if check_upstream.main([]) != 0:
        failures.append("contract check failed")
    for source in ("claude", "codex"):
        problem = direction(source)
        if problem:
            failures.append(problem)

    ok = not failures
    save_state({"issue": issue, "versions": versions, "ok": ok, "checked": time.strftime("%Y-%m-%dT%H:%M:%S")})
    if args.notify:
        where = f"CI flagged a Codex or Claude Code update (issue #{issue}). " if issue else ""
        if ok:
            notify(where + "peer still works on this Mac.")
        else:
            notify(where + "peer is broken on this Mac: " + "; ".join(failures))
    print("ok" if ok else "FAILED: " + "; ".join(failures))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
