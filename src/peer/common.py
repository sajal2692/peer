"""Names and paths every part of peer shares."""

from __future__ import annotations

import os
from pathlib import Path

TOOL_NAMES = {"claude": "Claude Code", "codex": "Codex"}
MARKER = "[peer request]"

CLAUDE_HOME = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


class PeerError(Exception):
    pass
