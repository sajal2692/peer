# peer

Get a second answer from the other coding agent, for the same conversation.

In Claude Code, type `/peer` and Codex answers. In Codex, type `$peer` and Claude Code answers. The other tool sees the same conversation history you have been working in and reads the same files. Its answer comes back into the tool you asked from, and, if you ask for it, that tool's own assessment of the two answers. The result is a two-sided review of the same question.

## Install

Requirements:
- macOS, with Python 3.9 or newer. No packages are needed. Linux should work when `codex` and `claude` are on your PATH, but it is untested.
- Claude Code and Codex both installed and signed in. peer uses the Codex inside the ChatGPT desktop app if that is newer than the `codex` on your PATH.

```bash
git clone https://github.com/sajal2692/peer.git
cd peer
./install.sh
```

`install.sh` creates three symlinks, so `git pull` updates peer in place:
- `~/.local/bin/peer` points to `peer.py`
- `~/.claude/skills/peer` is the `/peer` skill for Claude Code
- `~/.codex/skills/peer` is the `$peer` skill for Codex

Restart Claude Code or Codex after installing so they pick up the new skill. Check the install with `peer --version`.

## Use it

### From Claude Code

| Type | What happens |
|---|---|
| `/peer` | Codex answers your previous message itself. Claude Code's reply is withheld from it, so the two answers are independent. |
| `/peer review` | Codex reviews Claude Code's last reply. |
| `/peer review error handling` | Codex reviews the last reply, focusing on error handling. |
| `/peer is Redis overkill here?` | Codex answers a new question, using the conversation so far as context. |
| `/peer --model gpt-5.5 --effort high` | Any of the above, with a specific Codex model and reasoning effort. |
| `/peer --assess` | Any of the above, followed by Claude Code's assessment of the two answers. |
| `/peer --details` | Any of the above, with the full run header instead of the one-line stats. |

### From Codex

The same forms work with `$peer`: `$peer`, `$peer review`, `$peer <question>`, `$peer --model opus`.

### From a fresh session

In a new session with no history, `/peer <question>` or `$peer <question>` sends just the question. This is a plain second opinion.

### From a terminal

```bash
peer --to codex "explain the retry logic in client.py"   # plain question, no history
peer --from claude --last review                       # review the newest Claude Code session in this folder
peer --from codex --session <thread id or path>        # use a specific saved session
```

## What you get back

By default, you get the other tool's output, verbatim, under a single line of stats:

```
## Codex's answer

Model: gpt-6.1-sol | Effort: xhigh

<Codex's answer>

---
Continue with Codex: codex resume 01a137ed-...
```

If the other tool answered something you didn't type, such as a task notification that arrived after your last message, an `Answering:` line says so under the stats.

Two optional extras:

- **`--details`** replaces the stats line with the full run header. That header shows the peer command, the exact Codex or Claude command, where the model and effort came from, the mode, which input was answered, and the context size:

  ```
  Peer command: peer --from claude --details
  Codex command: .../codex exec --sandbox read-only ... - < prompt on stdin (137,451 chars)
  Model: gpt-6.1-sol (Codex default) | Effort: xhigh (Codex default)
  Mode: Codex answered the latest input itself; Claude Code's reply was withheld
  Answering: User: "..."
  Context: 75 turns from Claude Code, 137,451 characters sent (57 older turns left out to fit)
  Assessment: off (default)
  ```

- **`--assess`** adds the asking tool's own assessment after the output, under `## Claude Code's assessment` or `## Codex's assessment`. It covers where the answers agree, where they differ, what changes, and a verdict.

To keep talking to the other tool, run the `Continue with` command in a terminal. Neither tool changes files based on the other's answer unless you ask it to.

## Settings: model, effort and extras

The easiest way to set defaults is `config`, from either tool or a terminal. Settings are shared, so a value you set in Claude Code also applies in Codex.

| In Claude Code | In Codex | Effect |
|---|---|---|
| `/peer models` | `$peer models` | List the model ids and effort levels you can use, with a recommended and the current one. |
| `/peer config` | `$peer config` | Show the current settings and where each comes from. |
| `/peer config codex model gpt-5.5` | `$peer config codex model gpt-5.5` | Use this model whenever Codex answers. |
| `/peer config codex effort high` | `$peer config codex effort high` | Reasoning effort whenever Codex answers. |
| `/peer config claude model opus` | `$peer config claude model opus` | Use this model whenever Claude Code answers. |
| `/peer config claude effort max` | `$peer config claude effort max` | Effort whenever Claude Code answers. |
| `/peer config codex model default` | `$peer config codex model default` | Go back to Codex's own config. |
| `/peer config assess on` | `$peer config assess on` | Always add the asking tool's assessment. |
| `/peer config details on` | `$peer config details on` | Always print the full run header. |
| `/peer config trim on` | `$peer config trim on` | Always trim with the sensible limits. |
| `/peer config budget 600000` | `$peer config budget 600000` | Always cap the transcript at this many characters. |

`codex` and `claude` name the tool that answers, not the one you are in. In a terminal, use `peer config ...` and `peer models`.

`models` reads the lists live. Codex's list comes from its own model catalog (`codex debug models`), in Codex's order, and the first entry is marked recommended. For Claude Code, peer lists the aliases (`fable`, `opus`, `sonnet`, `haiku`), which always point to the newest version, plus any extra models Claude Code offers your account. Its recommended pick is `opus`. That is peer's own choice, because Claude Code has no ranking to read. Add `--model` with any listed id or alias. Add `[1m]` to a Claude alias, such as `opus[1m]`, for a 1M-token context.

`/peer config` shows something like this:

```
When Codex answers: model gpt-6.1-sol (Codex's own config), effort xhigh (Codex's own config)
When Claude Code answers: model opus (from peer config), effort high (Claude Code's own config)
Assessment: off (default)
Full header: off (default)
```

**For a single call**, use flags instead: `/peer --model gpt-5.5 --effort high`, `$peer --model opus`, `/peer --assess`, `/peer --details`. Use `--no-assess` or `--no-details` to turn an extra off for one call when it is on by default.

**Under the hood**, settings are stored in `~/.config/peer/config.json` (or the file `PEER_CONFIG` names). You can edit that file by hand:

```json
{"codex": {"model": "gpt-5.5", "effort": "high"}, "claude": {"model": "opus"}, "assess": false, "details": false}
```

The environment variables `PEER_CODEX_MODEL`, `PEER_CODEX_EFFORT`, `PEER_CLAUDE_MODEL`, `PEER_CLAUDE_EFFORT`, `PEER_ASSESS` and `PEER_DETAILS` also work, but the desktop apps usually don't read your shell profile.

When more than one source sets a value, the order is: flag, then environment variable, then the peer config, then the answering tool's own config.

## How it works

1. **Finds the conversation.** Claude Code and Codex each tell the commands they run which session those commands belong to (`CLAUDE_CODE_SESSION_ID`, `CODEX_THREAD_ID`). peer uses that ID to read the session's transcript from `~/.claude/projects` or `~/.codex/sessions`.
2. **Turns it into plain text.** See "What the other tool sees" below for exactly what is kept.
3. **Drops the request itself.** Your `/peer` message, and anything that arrived after it, is not sent.
4. **Asks the other tool**, headless and read-only, in the same folder:
   - Codex runs as `codex exec --sandbox read-only`.
   - Claude Code runs as `claude -p`, allowed only to read files, search and use read-only git commands.
5. **Prints the header and answer**, which the skill then shows you, followed by its assessment if that is turned on.

peer sends a plain-text reconstruction of the conversation rather than using each tool's own session import. This works the same in both directions and never writes into the other tool's history. The cost is that the other tool gets a summary-level replay, not an exact one.

## What the other tool sees

By default, the whole conversation is sent as it is, untrimmed. That includes every message, every tool call and its full output, and every compaction summary. A long session can run to hundreds of thousands of characters. A real 803,000-character session went through to Codex in 12 seconds at low effort.

**Included:**
- Every message you typed, including ones you sent while the agent was still working.
- The assistant's replies, and every tool call with its result.
- Inputs the system injected, each labelled as what it is: task notifications, messages from other sessions, subagent reports, scheduled tasks, your answers to multiple-choice questions, and compaction summaries.
- Images you pasted. They are saved to temporary files for the run, and the other tool opens them. This is verified both ways.
- Files you attached or @-mentioned, and context added by hooks.
- The paths of instruction files the asking tool loaded, such as `~/.claude/CLAUDE.md`, so the other tool can read them.

**Left out on purpose:** hidden reasoning, subagents' internal work, built-in commands such as `/model`, and the tools' own setup text (system prompts, tool lists, skill bodies, reminders). Images that come back from tools are sent only as a marker.

### Trimming

If a conversation is too big or too slow to send whole, trim it, either all at once or one part at a time:

| Flag | Effect |
|---|---|
| `--trim` | Sensible limits: messages 20,000 characters, tool calls 300, tool results 500, summaries 4,000, and the whole transcript 400,000. |
| `--max-message N` | Keep at most N characters of each message. |
| `--max-tool-input N` | Keep at most N characters of each tool call. |
| `--max-tool-output N` | Keep at most N characters of each tool result. |
| `--budget N` | Keep the transcript under N characters by dropping the oldest turns, while keeping the first one. |
| `--no-tools` | Leave tool calls and results out entirely. |

The individual flags override `--trim`. For example, `--trim --max-tool-output 0` uses the sensible limits but keeps tool output whole, because `0` means no limit. To trim every time, run `/peer config trim on`, or set a standing limit with `/peer config budget <chars>`. If an untrimmed run fails, peer says how large the conversation was and suggests `--trim`.

## Options

```
peer [review [focus] | question] [--from claude|codex] [--to claude|codex]
     [--session PATH|ID | --last] [--current-turn] [--model M] [--effort E]
     [--[no-]assess] [--[no-]details] [--[no-]trim] [--max-message N] [--max-tool-input N]
     [--max-tool-output N] [--budget N] [--no-tools] [--ephemeral] [--timeout S] [--dry-run]
peer models [codex|claude]
peer config [...]
```

| Option | Effect |
|---|---|
| `--from`, `--to` | Which tool you are in, and which to ask. Normally detected. |
| `--session`, `--last` | Read a saved session by path or ID, or the newest one for this folder. Nothing is dropped. |
| `--current-turn` | Answer the latest message, instead of treating it as the peer request. Useful when an agent consults its peer on its own. |
| `--trim`, `--max-*`, `--budget`, `--no-tools` | Trim what is sent; see Trimming above. Nothing is trimmed by default. |
| `--model`, `--effort` | Model and reasoning effort for the other tool. |
| `--assess`, `--no-assess` | Whether the asking tool adds its own assessment after the answer (default: off). |
| `--details`, `--no-details` | Full run header instead of the one-line stats (default: off). |
| `--ephemeral` | Don't keep the other tool's session (no `Continue with` line). |
| `--timeout` | Seconds to wait (default 1800). |
| `--dry-run` | Print the full header and the prompt without sending anything. |

Set `PEER_CODEX_BIN` or `PEER_CLAUDE_BIN` to force a particular binary.

## Limits and known issues

- **Scope.** peer works with Claude Code (the CLI and the desktop app's Code tab) and Codex (the CLI and the Codex inside ChatGPT). Regular ChatGPT and claude.ai chats are out of scope, because their history is not stored on disk.
- **Speed.** An answer takes from about 10 seconds to several minutes, depending on the model, the effort and the size of the conversation.
- **Codex to Claude Code needs the `claude` CLI signed in** to an account that allows Claude Code. The desktop app signs in separately, so `$peer` can fail even when the app works. To fix it, run `claude auth login` with the right account.
- **Older Claude Code transcripts** don't record where each message came from. For those, peer guesses from the message text, so an injected message in an unfamiliar format can show up as one you typed.
- **Codex-side notifications** are not labelled yet. In Codex, every user-role message is treated as typed.
- Very long sessions can be slow or exceed the other model's context window. If that happens, use `--trim` or `--budget`.
- **Platform.** Tested on macOS only. The Codex bundled inside the ChatGPT app is found at its macOS path.
- **The Claude Code skill runs peer in the background**, so the answer arrives as a separate step after the run finishes.
- peer never runs inside a session it started itself (it sets `PEER_ACTIVE`), so the two tools can't call each other in a loop.

## License

MIT. See [LICENSE](LICENSE).

## Development

```bash
python3 -m unittest discover -s tests          # the test suite, no packages needed
uvx ruff@0.17.0 check . && uvx ruff@0.17.0 format --check .
python3 scripts/check_dashes.py                # the project avoids em dashes
```

`peer.py` is a single file that uses only the Python standard library and runs on Python 3.9 or newer. The skills live in `skills/claude/peer` and `skills/codex/peer`. The tests in `tests/test_cli.py` run peer end to end against fake `codex` and `claude` binaries, so they need neither tool installed.

CI runs on every push and pull request: lint, the tests on macOS and Ubuntu with Python 3.9, 3.12 and 3.13, and a coverage check. Pushing a `v*` tag that matches `peer --version` runs the same checks and drafts a GitHub release from that version's section of `CHANGELOG.md`.

Update this README and `CHANGELOG.md` whenever behaviour, options or output change.
