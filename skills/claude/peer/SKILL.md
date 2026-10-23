---
name: peer
description: Ask Codex to answer from this same conversation and bring its answer back here. Use when the user types /peer, or asks for Codex's take, a second opinion from Codex or GPT, or what Codex would have said.
argument-hint: "models | config ... | [--model M] [--effort E] [--assess] [--details] [--trim] [--no-web] [review [focus]] | [question]"
allowed-tools: Bash(~/.local/bin/peer:*)
---

# Peer: Codex's answer to the same conversation

`peer` sends this session's transcript to Codex (headless, with the same kind of access as this session: it may change files and run commands only when this session may do so without asking, in the same working directory) and prints Codex's answer under a header that says exactly what ran.

## Run it

Pick the form from what the user typed after `/peer`:

| User typed | Command |
|---|---|
| `/peer` | `~/.local/bin/peer --from claude` |
| `/peer review` or `/peer review <focus>` | `~/.local/bin/peer --from claude review '<focus>'` |
| `/peer <question>` | `~/.local/bin/peer --from claude '<question>'` |
| `/peer config` or `/peer config <tool> model/effort <value>` or `/peer config assess/details/trim/continue on/off` or `/peer config access match/read-only/write` or `/peer config budget <chars>` | `~/.local/bin/peer config <same words>` |
| `/peer models` or `/peer models codex` / `/peer models claude` | `~/.local/bin/peer models <same words>` |

- If the user gave `--model <name>` or `--effort <level>` (anywhere after `/peer`), pass them through right after `--from claude`, for example `~/.local/bin/peer --from claude --model gpt-5.5 --effort high review`. Without them, peer uses `PEER_CODEX_MODEL` / `PEER_CODEX_EFFORT`, then `~/.config/peer/config.json`, then Codex's own config.
- If the user gave `--assess`, `--details` (or `--no-assess`, `--no-details`), pass them through the same way. The same goes for `--no-web` (keeps the other tool off the web; web access is on by default), `--write` or `--read-only` (overrides matching this session's access), `--no-continue` (start a new session instead of continuing the other tool's session from its last answer here, the default), `--resume <id>`, and for the trimming flags `--trim`, `--max-message N`, `--max-tool-input N`, `--max-tool-output N`, `--budget N` and `--no-tools`. By default peer sends the whole conversation untrimmed. `--assess` asks for your assessment; `--details` prints the full run header.
- Pass `--write` only when the user typed it. Never add it yourself, even if Codex says it needs write access; if the header shows it was read-only, tell the user they can rerun with `--write`.
- `models` lists the model ids and effort levels each tool accepts, with a recommended and the current one. Like `config`, it asks no one.
- `config` changes peer's saved defaults; it does not ask Codex. Run it in the foreground and show its output verbatim, nothing else. `<tool>` is the tool that answers (`codex` or `claude`), and `default` as the value clears a setting.
- Put any user text in single quotes as one argument, escaping single quotes inside it, and put `--` before it so text starting with `-` is not read as an option. For example: `~/.local/bin/peer --from claude -- '<question>'`.
- Run the command with the Bash tool in the foreground, with `timeout: 600000`. Codex usually answers within that. If the command runs longer and is moved to the background, wait for its completion notification, then read the output. Do not poll.
- peer drops the user's `/peer` message and anything injected after it, so bare `/peer` makes Codex answer the latest input before it (usually the user's previous message, sometimes a task notification) with your reply to it withheld.
- If you are consulting Codex on your own about the user's current message, rather than because the user asked for a peer answer, add `--current-turn`.

## Report back

Show peer's entire stdout verbatim, from the `## Codex's answer` line to the end, including every header line and the `Continue with` line. Do not summarize, trim or reformat it.

By default the header is one line of stats (model and effort). It also has an `Answering:` line when Codex answered something the user did not type, such as a task notification. With `--details`, it also lists the commands that ran, the mode and the context size.

Add your own assessment only when the stats line says `Assessment: on`. Then, after the output, add:

````
## Claude Code's assessment

**Where we agree:** ...

**Where we differ:** ...

**What changes:** what, if anything, you would now change in your earlier answer or recommendation.

**Verdict:** an honest call on which answer is better and why. Say so plainly if Codex caught something you missed, or if it answered a different input than the user meant.
````

Otherwise add nothing after the output. Do not adopt Codex's suggestions or change files without the user's go-ahead.

If peer fails, show the command you ran, its stdout header and its error message, and stop. Do not retry with different flags unless the error says how.
