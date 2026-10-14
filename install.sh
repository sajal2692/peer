#!/usr/bin/env bash
# Links peer onto PATH and installs the /peer (Claude Code) and $peer (Codex) skills.
set -euo pipefail

# Run from a clone, this installs that clone. Piped from curl, it first clones peer into
# ~/.local/share/peer (or updates that clone) and then installs from there:
#   curl -fsSL https://raw.githubusercontent.com/sajal2692/peer/main/install.sh | bash
source_file="${BASH_SOURCE[0]:-}"
if [ -n "$source_file" ] && [ -f "$(dirname "$source_file")/peer.py" ]; then
  repo="$(cd "$(dirname "$source_file")" && pwd)"
else
  url="${PEER_REPO_URL:-https://github.com/sajal2692/peer.git}"
  dest="${PEER_INSTALL_DIR:-$HOME/.local/share/peer}"
  if ! command -v git >/dev/null 2>&1; then
    echo "error: git is needed to download peer" >&2
    exit 1
  fi
  if [ -d "$dest/.git" ]; then
    echo "Updating $dest"
    git -C "$dest" pull --ff-only --quiet
  elif [ -e "$dest" ]; then
    echo "error: $dest exists and is not a peer clone; move it aside or set PEER_INSTALL_DIR" >&2
    exit 1
  else
    echo "Downloading peer into $dest"
    mkdir -p "$(dirname "$dest")"
    git clone --quiet "$url" "$dest"
  fi
  exec bash "$dest/install.sh"
fi
bin_dir="$HOME/.local/bin"
claude_home="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
codex_home="${CODEX_HOME:-$HOME/.codex}"
failed=0

link() {
  local src="$1" dest="$2"
  mkdir -p "$(dirname "$dest")"
  if [ -e "$dest" ] && [ ! -L "$dest" ]; then
    echo "error: $dest exists and is not a symlink; move it aside and run install.sh again" >&2
    failed=1
    return
  fi
  ln -sfn "$src" "$dest"
  echo "linked $dest -> $src"
}

if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 is not on PATH; peer needs Python 3.9 or newer" >&2
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))'; then
  echo "error: peer needs Python 3.9 or newer; python3 is $(python3 --version 2>&1)" >&2
  exit 1
fi

chmod +x "$repo/peer.py"
link "$repo/peer.py" "$bin_dir/peer"
link "$repo/skills/claude/peer" "$claude_home/skills/peer"
link "$repo/skills/codex/peer" "$codex_home/skills/peer"

if [ "$failed" -ne 0 ]; then
  echo "peer is not fully installed; see the errors above." >&2
  exit 1
fi

case ":$PATH:" in
  *":$bin_dir:"*) ;;
  *) echo "note: $bin_dir is not on your PATH. The skills call peer by its full path, so they work;" \
          "to run peer yourself, add this to your shell profile: export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac
echo "Installed peer $("$repo/peer.py" --version | cut -d' ' -f2). Restart Claude Code and Codex to load the skills."
