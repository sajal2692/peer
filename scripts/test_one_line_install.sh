#!/usr/bin/env bash
# Checks the one-line installer end to end: a first install from a git remote, then an
# update after a new commit lands on that remote. Runs in throwaway folders.
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
export HOME="$work/home"
mkdir -p "$HOME"
git config --global user.email test@example.com
git config --global user.name test

# A stand-in for GitHub: a bare remote whose main branch holds the current code.
remote="$work/remote.git"
git init --quiet --bare "$remote"
git -C "$remote" symbolic-ref HEAD refs/heads/main
# A snapshot of the committed files, so a shallow CI checkout works too.
mkdir "$work/snapshot"
git -C "$repo" archive HEAD | tar -x -C "$work/snapshot"
git -C "$work/snapshot" init --quiet
git -C "$work/snapshot" add -A
git -C "$work/snapshot" commit --quiet -m "snapshot"
git -C "$work/snapshot" push --quiet "$remote" HEAD:refs/heads/main
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

# A download cut off inside main() runs nothing and fails.
fresh="$work/fresh-home"
mkdir -p "$fresh"
cut_at=$(( $(grep -n '^main()' "$repo/install.sh" | cut -d: -f1) + 5 ))
if head -n "$cut_at" "$repo/install.sh" | HOME="$fresh" bash 2>/dev/null; then
  echo "expected a cut-off installer to fail" >&2
  exit 1
fi
if [ -e "$fresh/.local/bin/peer" ] || [ -e "$fresh/.local/share/peer" ]; then
  echo "a cut-off installer must not install anything" >&2
  exit 1
fi
# An install from before the launcher (a symlink to peer.py) is replaced by the launcher.
upgrade="$work/upgrade-home"
mkdir -p "$upgrade/.local/bin"
ln -s "$repo/peer.py" "$upgrade/.local/bin/peer"
HOME="$upgrade" bash "$repo/install.sh" >/dev/null
grep -q "peer launcher, written by install.sh" "$upgrade/.local/bin/peer"
HOME="$upgrade" "$upgrade/.local/bin/peer" --version

# Without Python 3.12 or newer, the installer stops with a clear error.
old="$work/old-python"
mkdir -p "$old/bin" "$work/old-home"
printf '#!/bin/sh\nexit 1\n' > "$old/bin/python3"
chmod +x "$old/bin/python3"
if out="$(HOME="$work/old-home" PEER_PYTHON_SEARCH="$old/bin/python3" bash "$repo/install.sh" 2>&1)"; then
  echo "expected the installer to refuse without Python 3.12 or newer" >&2
  exit 1
fi
case "$out" in
  *"needs Python 3.12 or newer"*) ;;
  *) echo "unexpected installer output without Python 3.12: $out" >&2; exit 1 ;;
esac

echo "one-line installer: ok"
