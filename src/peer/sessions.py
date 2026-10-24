"""Finds the session to read: the current one, one named by --session, or the newest for a folder."""

from __future__ import annotations

import glob
import itertools
import os
import re
import sys
from pathlib import Path

from . import common
from .claude_transcript import parse_claude
from .codex_transcript import parse_codex
from .common import MARKER, TOOL_NAMES, PeerError
from .transcript import Session, blocks_text, read_jsonl


def parse_session(tool: str, path: Path) -> Session:
    return parse_claude(path) if tool == "claude" else parse_codex(path)


def claude_project_dir(cwd: str) -> Path:
    return common.CLAUDE_HOME / "projects" / re.sub(r"[^A-Za-z0-9]", "-", cwd)


def find_claude_by_id(session_id: str) -> Path | None:
    hits = glob.glob(str(common.CLAUDE_HOME / "projects" / "*" / f"{session_id}.jsonl"))
    return Path(max(hits, key=os.path.getmtime)) if hits else None


def find_codex_by_id(thread_id: str) -> Path | None:
    for root in ("sessions", "archived_sessions"):
        hits = glob.glob(str(common.CODEX_HOME / root / "**" / f"rollout-*{thread_id}.jsonl"), recursive=True)
        if hits:
            return Path(max(hits, key=os.path.getmtime))
    return None


def is_peer_session(path: Path) -> bool:
    """True for sessions that peer itself started in the other tool."""
    try:
        rows = read_jsonl(path)
        for row in itertools.islice(rows, 40):
            if row.get("type") == "user":  # Claude Code
                return blocks_text((row.get("message") or {}).get("content")).startswith(MARKER)
            payload = row.get("payload") or {}
            is_user_message = payload.get("type") == "message" and payload.get("role") == "user"  # Codex
            if is_user_message and blocks_text(payload.get("content")).startswith(MARKER):
                return True
    except OSError:
        pass
    return False


def codex_session_cwd(path: Path) -> str | None:
    try:
        first = next(read_jsonl(path))
    except (StopIteration, OSError):
        return None
    return (first.get("payload") or {}).get("cwd")


def find_latest(tool: str, cwd: str) -> Path | None:
    if tool == "claude":
        files = sorted(claude_project_dir(cwd).glob("*.jsonl"), key=os.path.getmtime, reverse=True)
        return next((f for f in files if not is_peer_session(f)), None)
    files = sorted(glob.glob(str(common.CODEX_HOME / "sessions" / "*" / "*" / "*" / "rollout-*.jsonl")), reverse=True)
    for name in files[:500]:
        path = Path(name)
        if codex_session_cwd(path) == cwd and not is_peer_session(path):
            return path
    return None


def sniff_format(path: Path) -> str:
    """Which tool wrote a transcript file, from its rows; the folder decides only as a fallback."""
    try:
        for row in itertools.islice(read_jsonl(path), 20):
            if row.get("type") in ("session_meta", "response_item", "event_msg", "turn_context"):
                return "codex"
            if row.get("type") in ("user", "assistant", "attachment", "summary", "system"):
                return "claude"
    except (OSError, UnicodeDecodeError):
        pass
    return "codex" if str(path.resolve()).startswith(str(common.CODEX_HOME.resolve())) else "claude"


def detect_host() -> str:
    in_claude = bool(os.environ.get("CLAUDE_CODE_SESSION_ID"))
    in_codex = bool(os.environ.get("CODEX_THREAD_ID"))
    if in_claude and in_codex:
        raise PeerError("Both Claude Code and Codex session variables are set. Pass --from claude or --from codex.")
    if in_claude:
        return "claude"
    if in_codex:
        return "codex"
    raise PeerError("Not inside a Claude Code or Codex session. Pass --from with --last, or --session.")


def resolve_session(args) -> tuple[Session, bool]:
    """Returns the host session and whether its last turn is the peer request itself.

    Inside a session the last turn is the request that ran peer, so it is dropped.
    A session picked with --session or --last is read as it stands.
    """
    cwd = os.getcwd()
    if args.session:
        path = Path(args.session).expanduser()
        if path.exists():
            tool = args.source or sniff_format(path)
        else:
            found = [
                (t, p)
                for t, p in (("claude", find_claude_by_id(args.session)), ("codex", find_codex_by_id(args.session)))
                if p
            ]
            found = [(t, p) for t, p in found if not args.source or t == args.source]
            if not found:
                raise PeerError(f"No session file or id matches {args.session!r}.")
            tool, path = found[0]
        return parse_session(tool, path), False

    tool = args.source or detect_host()
    if args.last:
        path = find_latest(tool, cwd)
        if not path:
            raise PeerError(f"No {TOOL_NAMES[tool]} session found for {cwd}.")
        return parse_session(tool, path), False

    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID" if tool == "claude" else "CODEX_THREAD_ID")
    path = None
    if session_id:
        path = find_claude_by_id(session_id) if tool == "claude" else find_codex_by_id(session_id)
    if not path:
        path = find_latest(tool, cwd)
        if path:
            print(
                f"peer: no session id from {TOOL_NAMES[tool]}; using the newest session for this folder.",
                file=sys.stderr,
            )
    if not path:
        raise PeerError(f"Could not find the current {TOOL_NAMES[tool]} session transcript.")
    return parse_session(tool, path), True
