#!/usr/bin/env bash
# Links peer onto PATH and installs the /peer (Claude Code) and $peer (Codex) skills.
#
# Run from a clone, this installs that clone. Piped from curl, it first downloads peer into
# ~/.local/share/peer (or updates that copy) and then installs from there:
#   curl -fsSL https://raw.githubusercontent.com/sajal2692/peer/main/install.sh | bash
#
# Everything runs inside main(), called on the last line, so a download cut off part way
# through runs nothing.
set -euo pipefail

download() {
  local url="${PEER_REPO_URL:-https://github.com/sajal2692/peer.git}"
  local ref="${PEER_REF:-main}"
  local dest="${PEER_INSTALL_DIR:-$HOME/.local/share/peer}"
  if ! command -v git >/dev/null 2>&1; then
    echo "error: git is needed to download peer" >&2
    exit 1
  fi
  if [ -d "$dest/.git" ]; then
    local origin
    origin="$(git -C "$dest" remote get-url origin 2>/dev/null || true)"
    if [ "$origin" != "$url" ]; then
      echo "error: $dest is a clone of '$origin', not $url; move it aside or set PEER_INSTALL_DIR" >&2
      exit 1
    fi
    echo "Updating $dest"
    git -C "$dest" fetch --quiet origin "$ref"
    git -C "$dest" merge --ff-only --quiet FETCH_HEAD
  elif [ -e "$dest" ]; then
    echo "error: $dest exists and is not a peer clone; move it aside or set PEER_INSTALL_DIR" >&2
    exit 1
  else
    echo "Downloading peer into $dest"
    mkdir -p "$(dirname "$dest")"
    git clone --quiet --branch "$ref" "$url" "$dest"
  fi
  exec bash "$dest/install.sh"
}

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

# A Python 3.12 or later: PEER_PYTHON, then python3, then versioned names on PATH, then the
# usual Homebrew locations. PEER_PYTHON_SEARCH replaces that list (the tests use it).
# macOS's own python3 is 3.9, too old for peer.
find_python() {
  local candidate path
  local search="${PEER_PYTHON_SEARCH:-python3 python3.15 python3.14 python3.13 python3.12 /opt/homebrew/bin/python3 /usr/local/bin/python3}"
  # shellcheck disable=SC2086 # the search list is split on spaces on purpose
  for candidate in "${PEER_PYTHON:-}" $search; do
    [ -n "$candidate" ] || continue
    path="$(command -v "$candidate" 2>/dev/null)" || continue
    if "$path" -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then
      echo "$path"
      return 0
    fi
  done
  return 1
}

LAUNCHER_MARK="# peer launcher, written by install.sh"

# Writes the peer command as a small script that runs peer.py with the Python found above, so
# the desktop apps, which often don't see your shell's PATH, use that Python too.
write_launcher() {
  local python="$1" script="$2" dest="$3"
  mkdir -p "$(dirname "$dest")"
  if [ -e "$dest" ] && [ ! -L "$dest" ] && ! grep -qF "$LAUNCHER_MARK" "$dest"; then
    echo "error: $dest exists and was not written by peer; move it aside and run install.sh again" >&2
    failed=1
    return
  fi
  rm -f "$dest"
  printf '#!/bin/sh\n%s\nexec %q %q "$@"\n' "$LAUNCHER_MARK" "$python" "$script" > "$dest"
  chmod +x "$dest"
  echo "wrote $dest (runs $script with $python)"
}

main() {
  local source_file="$1"
  if [ -z "$source_file" ] || [ ! -f "$source_file" ] || [ ! -f "$(dirname "$source_file")/peer.py" ]; then
    download
  fi

  local repo bin_dir claude_home codex_home
  repo="$(cd "$(dirname "$source_file")" && pwd)"
  bin_dir="$HOME/.local/bin"
  claude_home="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
  codex_home="${CODEX_HOME:-$HOME/.codex}"
  failed=0

  local python
  if ! python="$(find_python)"; then
    echo "error: peer needs Python 3.12 or newer, and none was found." \
      "Install one (brew install python, or from python.org), or set PEER_PYTHON to its path." >&2
    exit 1
  fi

  chmod +x "$repo/peer.py"
  write_launcher "$python" "$repo/peer.py" "$bin_dir/peer"
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
  echo "Installed peer $("$python" "$repo/peer.py" --version | cut -d' ' -f2). Restart Claude Code and Codex to load the skills."
}

# Empty when the script arrives on stdin, as with curl | bash. Inside a function it would
# read "main" instead, so it is taken here.
main "${BASH_SOURCE[0]:-}"
