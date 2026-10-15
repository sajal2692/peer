---
name: peer
description: Ask Claude Code to answer from this same conversation and bring its answer back here. Use when the user types $peer, or asks for Claude's take, a second opinion from Claude, or what Claude would have said.
---

# Peer: Claude Code's answer to the same conversation

`peer` sends this thread's transcript to Claude Code (headless, read-only unless the user adds `--write`, in the same working directory) and prints Claude's answer under a header that says exactly what ran.

## Run it

Pick the form from what the user wrote alongside `$peer`:

| User wrote | Command |
|---|---|
| just `$peer` | `~/.local/bin/peer --from codex` |
| `$peer review` or `$peer review <focus>` | `~/.local/bin/peer --from codex review '<focus>'` |
| `$peer <question>` | `~/.local/bin/peer --from codex '<question>'` |
| `$peer config` or `$peer config <tool> model/effort <value>` or `$peer config assess/details/trim/continue on/off` or `$peer config budget <chars>` | `~/.local/bin/peer config <same words>` |
| `$peer models` or `$peer models codex` / `$peer models claude` | `~/.local/bin/peer models <same words>` |

- If the user gave `--model <name>` or `--effort <level>`, pass them through right after `--from codex`, for example `~/.local/bin/peer --from codex --model opus review`. Without them, peer uses `PEER_CLAUDE_MODEL` / `PEER_CLAUDE_EFFORT`, then `~/.config/peer/config.json`, then Claude Code's own config.
- If the user gave `--assess`, `--details` (or `--no-assess`, `--no-details`), pass them through the same way. The same goes for `--no-web` (keeps the other tool off the web; web access is on by default), `--write` (lets the other tool change files and run commands with side effects), `--continue` or `--no-continue` (continue the other tool's session from its last answer here, instead of starting a new one), `--resume <id>`, and for the trimming flags `--trim`, `--max-message N`, `--max-tool-input N`, `--max-tool-output N`, `--budget N` and `--no-tools`. By default peer sends the whole conversation untrimmed. `--assess` asks for your assessment; `--details` prints the full run header.
- Pass `--write` only when the user typed it. Never add it yourself, even if Claude Code says it needs write access; tell the user they can rerun with `--write`.
- If the user asks Claude Code to act on its own earlier answer ("add it", "go ahead", "apply that") without `--continue`, pass the request as typed. Then, after the output, say in one line that `$peer --continue` sends it to the same Claude Code session and `--write` lets it make changes.
- `models` lists the model ids and effort levels each tool accepts, with a recommended and the current one. Like `config`, it asks no one.
- `config` changes peer's saved defaults; it does not ask Claude Code. Run it in the foreground and show its output verbatim, nothing else. `<tool>` is the tool that answers (`codex` or `claude`), and `default` as the value clears a setting.
- Put any user text in single quotes as one argument, escaping single quotes inside it, and put `--` before it so text starting with `-` is not read as an option. For example: `~/.local/bin/peer --from codex -- '<question>'`.
- Claude can take several minutes. Use a command timeout longer than peer's own, which is 30 minutes by default (35 minutes works), and wait for the command to finish.
- The command needs network access and writes Claude's session files under `~/.claude`. If the sandbox blocks it, rerun it with escalated permissions.
- peer drops the message that asked for it, so bare `$peer` makes Claude answer the user's previous message with your reply to it withheld.
- If you are consulting Claude on your own about the user's current message, rather than because the user asked for a peer answer, add `--current-turn`.

## Report back

Show peer's entire stdout verbatim, from the `## Claude Code's answer` line to the end, including every header line and the `Continue with` line. Do not summarize, trim or reformat it.

By default the header is one line of stats (model and effort). It also has an `Answering:` line when Claude Code answered something the user did not type, such as a task notification. With `--details`, it also lists the commands that ran, the mode and the context size.

Add your own assessment only when the stats line says `Assessment: on`. Then, after the output, add:

````
## Codex's assessment

**Where we agree:** ...

**Where we differ:** ...

**What changes:** what, if anything, you would now change in your earlier answer or recommendation.

**Verdict:** an honest call on which answer is better and why. Say so plainly if Claude Code caught something you missed, or if it answered a different input than the user meant.
````

Otherwise add nothing after the output. Do not adopt Claude Code's suggestions or change files without the user's go-ahead.

If peer fails, show the command you ran, its stdout header and its error message, and stop. Do not retry with different flags unless the error says how.
