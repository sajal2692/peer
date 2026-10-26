# Changelog

## Unreleased

**Changes**
- The other tool now gets the same kind of access as the session you ask from. When your session may change files and run commands on its own (Claude Code in auto, accept-edits or bypass-permissions mode, or Codex in a workspace-write or full-access sandbox), so may the other tool: Codex runs in its workspace-write sandbox with network access, and Claude Code gets `Edit`, `Write` and `Bash`. Otherwise it stays read-only. `--read-only`, `--write` and `peer config access` override this. Connected apps, plugins and MCP servers stay off either way.
- A later `/peer` in the same conversation continues the other tool's session from its last answer, sending only the turns since then. `--no-continue` or `peer config continue off` starts a new session, and `--resume <id>` picks one by id.
- peer now needs Python 3.12 or newer. macOS's own `python3` is 3.9, so install a newer one first (`brew install python` or python.org). The installer finds it, or uses `PEER_PYTHON`, and writes `~/.local/bin/peer` as a small script that runs peer with that Python, so the desktop apps use it too. Running peer with an older Python stops with a clear message.

## 0.1.1

**Install**
- One-line install: `curl -fsSL https://raw.githubusercontent.com/sajal2692/peer/main/install.sh | bash` downloads peer into `~/.local/share/peer` and links it. Running it again updates.

**Fixes**
- A follow-up `/peer` or `/peer review` now looks past earlier peer runs, including ones you asked for in plain words such as "what would Codex say?", so it judges the reply you asked about rather than the host relaying an earlier peer answer. In Claude Code, peer's own completion notification is recognised and skipped. A genuine latest input, such as a build finishing, is still answered as before.
- If peer can't read Codex's feature list, it stops instead of running Codex with apps, plugins and browser control on.
- `CLAUDE_CODE_OAUTH_TOKEN` and other user-set Claude Code settings now reach the Claude CLI, so token sign-ins work for `$peer`.
- A notification that starts with a system reminder is labelled correctly instead of being read as a typed message.
- `review` works when the reply was only tool calls, and the header says "Reviewing the reply to" in review mode.

**Changes**
- `--no-web` (or `peer config web off`) keeps both tools off the web. Web access stays on by default.
- The Claude Code skill runs peer in the foreground, falling back to the background for long runs.
- The README has a demo GIF and a Prerequisites section.
- The code is now split into modules in `src/peer`, with `peer.py` as a small launcher. Nothing changes in how you install or run peer.
- CI tests Python 3.9 through 3.15, plus the newest Python build available, pre-releases included.

## 0.1.0

First release.

- `/peer` in Claude Code and `$peer` in Codex ask the other tool to answer from the same conversation, and print its answer back in the tool you asked from.
- Three modes: answer your last message independently, `review` the last reply, or answer a new question with the conversation as context. A fresh session sends just the question.
- The whole thread is sent by default, untrimmed: messages, tool calls and results, messages sent while the agent was busy, attached files, pasted images (saved to temporary files the other tool can open), and compaction summaries. Inputs the system injected are labelled as what they are.
- `--trim` and the `--max-*`, `--budget` and `--no-tools` flags cut what is sent, all at once or one part at a time.
- `peer models` lists the models and effort levels each tool accepts. `peer config` saves defaults for the model, effort, trimming, the asking tool's assessment and the full header.
- The other tool runs headless in the same folder with read-only tools only. Codex runs in its read-only sandbox, with connected apps, plugins, MCP servers and browser and computer control switched off. Claude Code gets only Read, Grep, Glob, WebSearch and WebFetch, with MCP servers, hooks and skills off. Session variables are scrubbed, and peer will not run under itself.
