#!/usr/bin/env bash
# Checks the one-line installer end to end: a first install from a git remote, then an
# update after a new commit lands on that remote. Runs in throwaway folders.
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"
export HOME="$work/home"
mkdir -p "$HOME"
git config --global user.email test@example.com
git config --global user.name test

# A stand-in for GitHub: a bare remote whose main branch holds the current code.
remote="$work/remote.git"
git init --quiet --bare "$remote"
git -C "$remote" symbolic-ref HEAD refs/heads/main
git -C "$repo" push --quiet "$remote" HEAD:refs/heads/main
export PEER_REPO_URL="$remote"

bash < "$repo/install.sh"
"$HOME/.local/bin/peer" --version
test -L "$HOME/.claude/skills/peer"
test -L "$HOME/.codex/skills/peer"

# A new commit on the remote reaches the installed copy on the next run.
git clone --quiet "$remote" "$work/dev"
echo "update check" > "$work/dev/UPDATE_MARKER"
git -C "$work/dev" add UPDATE_MARKER
git -C "$work/dev" commit --quiet -m "update"
git -C "$work/dev" push --quiet origin HEAD:main
bash < "$repo/install.sh"
test -f "$HOME/.local/share/peer/UPDATE_MARKER"

# A copy cloned from somewhere else is left alone.
git -C "$HOME/.local/share/peer" remote set-url origin https://example.com/other.git
if bash < "$repo/install.sh" 2>/dev/null; then
  echo "expected the installer to refuse a clone of another repository" >&2
  exit 1
fi

# A cut-off download runs nothing.
head -c 400 "$repo/install.sh" | bash
echo "one-line installer: ok"
