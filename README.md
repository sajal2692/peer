# peer

Get a second answer from the other coding agent, for the same conversation.

In Claude Code, type `/peer` and Codex answers. In Codex, type `$peer` and Claude Code answers. It works in the terminal and in the desktop apps: the Claude Code CLI and the Code tab of the Claude desktop app, and the Codex CLI and Codex in the ChatGPT desktop app. The other tool sees your conversation so far and can read the same files. Its answer comes back into the tool you asked from.

![peer demo: /peer in Claude Code and $peer in Codex](docs/peer-demo.gif)

## Prerequisites

- **macOS.** Linux should work, but it is untested.
- **Python 3.9 or newer and git.** Check with `python3 --version` and `git --version`. Both come with the Xcode Command Line Tools: `xcode-select --install`.
- **Codex, signed in.** Either the Codex CLI or Codex in the ChatGPT desktop app.
- **The Claude Code CLI, signed in.** `$peer` runs the `claude` command even if you use the Claude desktop app, and that command signs in separately from the app. Check with `claude auth status`, and run `claude auth login` if needed.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/sajal2692/peer/main/install.sh | bash
```

This downloads peer into `~/.local/share/peer` and links the `peer` command and the `/peer` and `$peer` skills. Run the same command again to update.

Or, to install from a clone you keep yourself:

```bash
git clone https://github.com/sajal2692/peer.git
cd peer
./install.sh
```

To update a clone, run `git pull` in it.

Restart Claude Code and Codex, then check the install with `peer --version`. If the installer says `~/.local/bin` is not on your PATH, the skills still work. To run `peer` from a terminal, add `export PATH="$HOME/.local/bin:$PATH"` to your shell profile.

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

Your next `/peer` in the same conversation goes back to that same session of the other tool, with only what happened since its last answer. To keep talking to it yourself, run the `Continue with` command in a terminal.

### When to use what

| You want | Use | Example |
|---|---|---|
| An independent second answer to what you just asked | `/peer` | You asked Claude Code how to speed up a slow query. `/peer` gets Codex's own answer without it seeing Claude's, so you can compare two fresh takes. |
| A check on the answer you already got | `/peer review` | Claude Code wrote a migration plan. `/peer review data loss` has Codex look for holes in that plan. |
| A side question, with the conversation as background | `/peer <question>` | Mid-way through a refactor, `/peer is there a simpler way to do this?` |
| A quick opinion on something new | `/peer <question>` in a new session | `/peer SQLite or Postgres for a single-user app?` |
| Your tool to weigh in on the other's answer | add `--assess` | `/peer --assess` gets Codex's answer, then Claude Code says where they agree, where they differ and which is better. |
| A stronger or faster model for this one call | add `--model` or `--effort` | `/peer --effort max review` for a careful review of a risky change. |
| A faster answer on a very long session | add `--trim` | `/peer --trim` sends a shortened conversation. |
| A follow-up to the other tool's last answer | `/peer <follow-up>` | Codex drafted a budget entry. `/peer add it` goes back to that same Codex session, which can record it if your session can write. |
| A fresh take, without the other tool's earlier answers | add `--no-continue` | `/peer --no-continue` starts a new session. |

### Options and settings

Add options after `/peer` or `$peer` for one call:

| Option | Effect |
|---|---|
| `--model <id>`, `--effort <level>` | Pick the other tool's model and reasoning effort. |
| `--assess` | After the answer, your own tool compares the two answers and gives a verdict. (`review` is the reverse: the other tool judges your tool's reply.) |
| `--details` | Show the full run: the exact commands, which message was answered, and how much was sent. |
| `--trim` | Shorten long messages and tool output. By default the whole conversation is sent. |
| `--no-web` | Keep the other tool off the web for this call. Web access is on by default. |
| `--no-continue` | Start a new session in the other tool instead of continuing the one that gave its last answer here. |
| `--read-only`, `--write` | Keep the other tool read-only, or let it change files and run commands, whatever your session may do. |

Or save defaults for every call. Saved settings are shared between both tools:

| Command | Effect |
|---|---|
| `/peer models` | List the models and effort levels you can use. |
| `/peer config` | Show the current settings. |
| `/peer config codex model gpt-5.5` | Use this model whenever Codex answers (`claude model` for Claude Code). |
| `/peer config assess on` | Always add the assessment. `details`, `trim` and `continue` work the same way. |
| `/peer config access read-only` | Always keep the other tool read-only (`write` to always allow it, `match` for the default). |

See [docs/configuration.md](docs/configuration.md) for every option, the config file and environment variables.

### From a terminal

```bash
peer --to codex "explain the retry logic in client.py"   # a plain question
peer --from claude --last review                       # review the newest Claude Code session here
peer --from codex --session <thread id or path>        # use a specific Codex session
peer --help                                            # every option
```

## Good to know

- **What is sent.** Your messages, the assistant's replies, tool calls and their output, attached files, pasted images, and context added by hooks. The paths of instruction files such as `CLAUDE.md` are listed so the other tool can read them. Hidden reasoning and the tools' own setup text are not sent.
- **Sign-in and billing.** peer has no account or API keys of its own. It runs the `codex` and `claude` command-line tools, so each answer uses whatever those tools are signed in with: your ChatGPT plan or an OpenAI API key for Codex, and your Claude subscription or an Anthropic API key for Claude Code. If `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` is set, the tool may bill that key instead.
- **Access matches your session.** By default the other tool gets the same kind of access as the session you ask from. If Claude Code is in auto, accept-edits or bypass-permissions mode, or Codex runs in its workspace-write or full-access sandbox, the other tool may change files and run commands. Otherwise it is read-only: Claude Code's default mode asks before each edit, plan mode doesn't edit, and the headless peer can't ask you, so it reads only. With write access, Codex runs in its workspace-write sandbox with network access, and Claude Code gets its editing and shell tools, approved without asking. Read-only, it can read files but cannot change them or run commands outside a read-only sandbox. Either way, connected apps, plugins and MCP servers stay off, so a tool the other side reaches through MCP is out of reach; one it runs as a command, such as a CLI, works. It can search and fetch web pages. If you work with untrusted content, `--no-web` (or `/peer config web off`) keeps it offline, and `--read-only` (or `/peer config access read-only`) keeps it from acting.
- **Sessions.** The first `/peer` in a conversation starts a session in the other tool, and later ones continue it, sending only the turns since its last answer. `--no-continue` (or `/peer config continue off`) starts a new one each time. peer never writes into your own sessions in either tool.
- **Speed.** An answer takes from a few seconds to several minutes, depending on the model, the effort and the length of the conversation. If a very long conversation fails or is slow, try `--trim`.
- **Scope.** Regular ChatGPT and claude.ai chats are not supported, because their history is not stored on your computer.

## License

MIT. See [LICENSE](LICENSE). To work on peer itself, see [CONTRIBUTING.md](CONTRIBUTING.md).
