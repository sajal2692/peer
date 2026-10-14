# peer

Get a second answer from the other coding agent, for the same conversation.

In Claude Code, type `/peer` and Codex answers. In Codex, type `$peer` and Claude Code answers. The other tool sees your conversation so far and can read the same files. Its answer comes back into the tool you asked from.

## Install

You need macOS, Python 3.9 or newer, and both Claude Code and Codex installed and signed in. The Codex inside the ChatGPT desktop app works too.

```bash
git clone https://github.com/sajal2692/peer.git
cd peer
./install.sh
```

Restart Claude Code and Codex, then check the install with `peer --version`. If the installer says `~/.local/bin` is not on your PATH, the skills still work. To run `peer` from a terminal, add `export PATH="$HOME/.local/bin:$PATH"` to your shell profile.

To update, run `git pull` in the `peer` folder.

## Usage

In Claude Code:

| Type | What happens |
|---|---|
| `/peer` | Codex answers your last message on its own, without seeing Claude Code's reply. |
| `/peer review` | Codex reviews Claude Code's last reply. |
| `/peer review error handling` | The same, focused on error handling. |
| `/peer is Redis overkill here?` | Codex answers a new question, with the conversation as context. |

In Codex, use `$peer` in the same ways: `$peer`, `$peer review`, `$peer <question>`.

In a new session with no history, `/peer <question>` or `$peer <question>` is a plain second opinion.

The answer looks like this:

```
## Codex's answer

Model: gpt-5.5 | Effort: high

<Codex's answer>

---
Continue with Codex: codex resume 01a137ed-...
```

Run the `Continue with` command in a terminal to keep talking to the other tool.

### Options

Add these after `/peer` or `$peer`:

| Option | Effect |
|---|---|
| `--model <id>`, `--effort <level>` | Pick the other tool's model and reasoning effort for this call. |
| `--assess` | After the answer, your tool compares the two answers and gives a verdict. |
| `--details` | Show the full run: the exact commands, which message was answered, and how much context was sent. |
| `--trim` | Shorten long messages and tool output before sending. By default the whole conversation is sent. |
| `--max-message N`, `--max-tool-input N`, `--max-tool-output N`, `--budget N`, `--no-tools` | Trim one part only. `--budget` caps the whole transcript in characters. |

### Settings

Save defaults so you don't repeat flags. Settings are shared between both tools.

| Command | Effect |
|---|---|
| `/peer models` | List the models and effort levels you can use. |
| `/peer config` | Show the current settings. |
| `/peer config codex model gpt-5.5` | Use this model whenever Codex answers. |
| `/peer config claude model opus` | Use this model whenever Claude Code answers. |
| `/peer config codex effort high` | Set the effort whenever Codex answers (`claude effort` for Claude Code). |
| `/peer config codex model default` | Go back to the tool's own default. |
| `/peer config assess on` | Always add the assessment. `details` and `trim` work the same way. |
| `/peer config budget 600000` | Always cap the transcript at this many characters. |

`codex` and `claude` name the tool that answers, not the one you are in. In Codex, type `$peer config ...`. Settings are saved in `~/.config/peer/config.json`.

### From a terminal

```bash
peer --to codex "explain the retry logic in client.py"   # a plain question
peer --from claude --last review                       # review the newest Claude Code session here
peer --from codex --session <thread id or path>        # use a specific Codex session
peer --help                                            # every option
```

## Good to know

- **What is sent.** Your messages, the assistant's replies, tool calls and their output, attached files and pasted images. Hidden reasoning and the tools' own setup text are not sent.
- **Read-only.** The other tool can read files in the folder but cannot change them, run commands outside a read-only sandbox, or use connected apps such as mail or GitHub.
- **Speed.** An answer takes from a few seconds to several minutes, depending on the model, the effort and the length of the conversation. If a very long conversation fails or is slow, try `--trim`.
- **Scope.** Claude Code (CLI and desktop app) and Codex (CLI and the ChatGPT app). Regular ChatGPT and claude.ai chats are not supported.

## Troubleshooting

- **`$peer` says Claude Code access is disabled.** The `claude` command-line tool is signed in to an account without Claude Code. The desktop app signs in separately. Run `claude auth login` with the account you use for Claude Code.
- **Codex says a model is not available.** Run `/peer models` to see the IDs your Codex accepts.
- **The skill isn't found.** Restart the tool after installing. Check that `~/.claude/skills/peer` and `~/.codex/skills/peer` exist.

## License

MIT. See [LICENSE](LICENSE). To work on peer itself, see [CONTRIBUTING.md](CONTRIBUTING.md).
