#!/usr/bin/env bash
# Runs scripts/upstream_smoke.py every six hours and at login, through launchd. It does nothing
# unless the daily upstream workflow has opened an issue; then it runs the real round trip once
# per issue and set of installed versions, and shows a macOS notification with the result.
# Needs gh signed in. Output goes to ~/Library/Logs/peer-upstream.log.
#
#   scripts/install_upstream_watch.sh              # install or update
#   scripts/install_upstream_watch.sh --uninstall  # remove
set -euo pipefail

label="com.github.sajal2692.peer.upstream"
plist="$HOME/Library/LaunchAgents/$label.plist"
log="$HOME/Library/Logs/peer-upstream.log"
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ "$(uname)" != "Darwin" ]; then
  echo "error: this installs a launchd job and only works on macOS" >&2
  exit 1
fi

launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
if [ "${1:-}" = "--uninstall" ]; then
  rm -f "$plist"
  echo "removed $plist"
  exit 0
fi

python="$(command -v python3)"
# launchd starts jobs with a bare PATH; these are where codex and claude are usually installed.
path="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

mkdir -p "$(dirname "$plist")" "$(dirname "$log")"
cat >"$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>$python</string>
    <string>$repo/scripts/upstream_smoke.py</string>
    <string>--if-flagged</string>
    <string>--notify</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>$path</string>
  </dict>
  <key>StartInterval</key>
  <integer>21600</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$log</string>
  <key>StandardErrorPath</key>
  <string>$log</string>
</dict>
</plist>
EOF
launchctl bootstrap "gui/$(id -u)" "$plist"
echo "installed $plist"
echo "checks $repo every six hours; log: $log"
