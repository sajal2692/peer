# Changelog

## Unreleased

- A follow-up `/peer` or `/peer review` now looks past earlier peer runs, including ones you asked for in plain words such as "what would Codex say?", so it judges the reply you asked about rather than the host relaying an earlier peer answer. In Claude Code, peer's own completion notification is recognised and skipped. A genuine latest input, such as a build finishing, is still answered as before.
- `--no-web` (or `peer config web off`) keeps both tools off the web. Web access stays on by default.
- If peer can't read Codex's feature list, it stops instead of running Codex with apps, plugins and browser control on.
- `CLAUDE_CODE_OAUTH_TOKEN` and other user-set Claude Code settings now reach the Claude CLI, so token sign-ins work for `$peer`.
- A notification that starts with a system reminder is labelled correctly instead of being read as a typed message.
- `review` works when the reply was only tool calls, and the header says "Reviewing the reply to" in review mode.
- The Claude Code skill runs peer in the foreground, falling back to the background for long runs.
- One-line install: `curl -fsSL https://raw.githubusercontent.com/sajal2692/peer/main/install.sh | bash` downloads peer into `~/.local/share/peer` and links it. Running it again updates.
- The code is now split into modules in `src/peer`, with `peer.py` as a small launcher. Nothing changes in how you install or run peer.

## 0.1.0

First release.

- `/peer` in Claude Code and `$peer` in Codex ask the other tool to answer from the same conversation, and print its answer back in the tool you asked from.
- Three modes: answer your last message independently, `review` the last reply, or answer a new question with the conversation as context. A fresh session sends just the question.
- The whole thread is sent by default, untrimmed: messages, tool calls and results, messages sent while the agent was busy, attached files, pasted images (saved to temporary files the other tool can open), and compaction summaries. Inputs the system injected are labelled as what they are.
- `--trim` and the `--max-*`, `--budget` and `--no-tools` flags cut what is sent, all at once or one part at a time.
- `peer models` lists the models and effort levels each tool accepts. `peer config` saves defaults for the model, effort, trimming, the asking tool's assessment and the full header.
- The other tool runs headless in the same folder with read-only tools only. Codex runs in its read-only sandbox, with connected apps, plugins, MCP servers and browser and computer control switched off. Claude Code gets only Read, Grep, Glob, WebSearch and WebFetch, with MCP servers, hooks and skills off. Session variables are scrubbed, and peer will not run under itself.
