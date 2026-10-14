#!/usr/bin/env bash
# Links peer onto PATH and installs the /peer (Claude Code) and $peer (Codex) skills.
set -euo pipefail

repo="$(cd "$(dirname "$0")" && pwd)"
claude_home="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
codex_home="${CODEX_HOME:-$HOME/.codex}"

link() {
  local src="$1" dest="$2"
  mkdir -p "$(dirname "$dest")"
  if [ -e "$dest" ] && [ ! -L "$dest" ]; then
    echo "skip: $dest exists and is not a symlink" >&2
    return
  fi
  ln -sfn "$src" "$dest"
  echo "linked $dest -> $src"
}

chmod +x "$repo/peer.py"
link "$repo/peer.py" "$HOME/.local/bin/peer"
link "$repo/skills/claude/peer" "$claude_home/skills/peer"
link "$repo/skills/codex/peer" "$codex_home/skills/peer"
