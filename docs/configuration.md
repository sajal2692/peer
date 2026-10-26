# Configuration

Everything you can change about how peer runs. The [README](../README.md) covers the basics.

## Per-call options

Add these after `/peer` or `$peer`, or pass them to `peer` in a terminal.

| Option | Effect |
|---|---|
| `--model <id>` | The other tool's model for this call. `/peer models` lists the IDs. |
| `--effort <level>` | The other tool's reasoning effort for this call. |
| `--assess`, `--no-assess` | Whether your tool adds its own assessment after the answer: where the answers agree and differ, what changes, and a verdict. Off by default. |
| `--details`, `--no-details` | Whether to show the full run header instead of the one-line stats. Off by default. |
| `--web`, `--no-web` | Whether the other tool may search and fetch web pages. On by default. `--no-web` keeps it offline, so a prompt injection in the conversation or a file can't send what the tool reads to a website. |
| `--trim`, `--no-trim` | Whether to shorten long content before sending. Off by default, so the whole conversation is sent. |
| `--max-message N` | Keep at most N characters of each message. |
| `--max-tool-input N` | Keep at most N characters of each tool call. |
| `--max-tool-output N` | Keep at most N characters of each tool result. |
| `--budget N` | Keep the transcript under N characters. The oldest turns go first, but the first turn is always kept. |
| `--no-tools` | Leave tool calls and results out. |
| `--current-turn` | Answer the latest message instead of treating it as the request for peer. For an agent that consults its peer on its own. |
| `--write`, `--read-only` | Let the other tool change files in the folder and run commands with side effects, such as a CLI that writes to a service, or keep it read-only. Without either, it matches your session (see [Access](#access)). |
| `--continue`, `--no-continue` | Whether to continue the other tool's session from its last answer in this conversation (found from the `Continue with` line) instead of starting a new one. Only the turns since that answer are sent, without the turns that ran peer. With no earlier answer, a new session starts. On by default. |
| `--resume <id>` | Continue this session of the other tool. If its answer is in the conversation, only the turns since are sent; otherwise the whole conversation is. |
| `--ephemeral` | Don't keep the other tool's session, so there is no `Continue with` line. Can't be combined with `--continue` or `--resume`. |
| `--timeout S` | Seconds to wait for the answer. The default is 1800. |
| `--dry-run` | Print the full header and the prompt without sending anything. |

For the `--max-*` options and `--budget`, `0` means no limit.

### What `--trim` sets

| Part | Limit, in characters |
|---|---|
| Each message | 20,000 |
| Each tool call | 300 |
| Each tool result | 500 |
| Each compaction summary | 4,000 |
| The whole transcript | 400,000 |

Options you add alongside `--trim` override its limits. For example, `--trim --max-tool-output 0` keeps tool output whole.

### Terminal-only options

| Option | Effect |
|---|---|
| `--from claude\|codex` | Which tool's session to read. Detected inside a session. |
| `--to claude\|codex` | Which tool to ask. By default, the other one. |
| `--session PATH\|ID` | Read a saved session by file path or session ID. |
| `--last` | Read the newest session for the current folder. |

With `--session` or `--last`, the last message is kept, since it is not a request for peer.

## Saved settings

Settings you save apply to every call, from both tools and the terminal.

| Command | Effect |
|---|---|
| `/peer config` | Show the current settings and where each one comes from. |
| `/peer config codex model <id>` | Model whenever Codex answers. |
| `/peer config codex effort <level>` | Effort whenever Codex answers. |
| `/peer config claude model <alias>` | Model whenever Claude Code answers. |
| `/peer config claude effort <level>` | Effort whenever Claude Code answers. |
| `/peer config assess on\|off` | Always, or never, add the assessment. |
| `/peer config details on\|off` | Always, or never, show the full header. |
| `/peer config trim on\|off` | Always, or never, trim. |
| `/peer config web on\|off` | Allow web access (the default), or keep the other tool offline. |
| `/peer config budget <chars>` | Always cap the transcript at this many characters. |
| `/peer config continue on\|off` | Continue the other tool's session from its last answer here (the default), or start a new one each time. |
| `/peer config access match\|read-only\|write` | Match your session's permissions (the default), always stay read-only, or always allow writing. |

`codex` and `claude` name the tool that answers, not the one you are in. In Codex, type `$peer config ...`, and in a terminal, `peer config ...`. Use `default` as the value to clear a setting, for example `/peer config codex model default`.

### The config file

Settings live in `~/.config/peer/config.json`. `peer config` writes it, and you can also edit it by hand:

```json
{
  "codex": {"model": "gpt-5.5", "effort": "high"},
  "claude": {"model": "opus", "effort": "max"},
  "assess": false,
  "details": false,
  "trim": false,
  "continue": true,
  "access": "read-only",
  "budget": 600000
}
```

Every key is optional.

### Environment variables

| Variable | Effect |
|---|---|
| `PEER_CODEX_MODEL`, `PEER_CODEX_EFFORT` | Model and effort when Codex answers. |
| `PEER_CLAUDE_MODEL`, `PEER_CLAUDE_EFFORT` | Model and effort when Claude Code answers. |
| `PEER_ASSESS`, `PEER_DETAILS`, `PEER_TRIM`, `PEER_WEB`, `PEER_CONTINUE` | `1` for on, `0` for off. |
| `PEER_ACCESS` | `match`, `read-only` or `write`. |
| `PEER_CONFIG` | Use a different config file. |
| `PEER_CODEX_BIN`, `PEER_CLAUDE_BIN` | Use a specific `codex` or `claude` binary. By default peer picks the newest it can find, including the Codex inside the ChatGPT app. |
| `PEER_PYTHON` | The Python 3.12 or newer for the installer to use. By default it tries `python3`, then `python3.15` down to `python3.12`, then Homebrew's. |
| `PEER_INSTALL_DIR` | Where the one-line installer keeps its copy of peer. The default is `~/.local/share/peer`. |
| `CLAUDE_CONFIG_DIR`, `CODEX_HOME` | Where Claude Code and Codex keep their sessions, if not `~/.claude` and `~/.codex`. |

The desktop apps usually don't read your shell profile, so the config file is the more reliable choice there.

### Which value wins

When a setting is given in more than one place, peer uses the first it finds:

1. An option on the call, such as `--model`
2. An environment variable, such as `PEER_CODEX_MODEL`
3. The config file
4. The other tool's own configuration

## Access

By default the other tool gets the same kind of access as the session you ask from. If Claude Code is in auto, accept-edits or bypass-permissions mode, or Codex runs in its workspace-write or full-access sandbox, the other tool may change files and run commands. Otherwise it is read-only: Claude Code's default mode asks before each edit, plan mode doesn't edit, and the headless peer can't ask you, so it reads only.

| Your session | The other tool |
|---|---|
| Claude Code in `auto`, `acceptEdits` or `bypassPermissions` mode | Codex with `--sandbox workspace-write` and network access |
| Claude Code in `default`, `plan` or `dontAsk` mode | Codex with `--sandbox read-only` |
| Codex with the `workspace-write` or `danger-full-access` sandbox | Claude Code with `Edit`, `Write` and `Bash`, in `acceptEdits` mode |
| Codex with the `read-only` sandbox | Claude Code with `Read`, `Grep` and `Glob` |
| A terminal, or a transcript that doesn't record its permissions | read-only |

peer reads your session's current setting from its transcript: Claude Code records the permission mode on each message, and Codex records the sandbox at the start of each turn. If you switch modes mid-session, the next call follows the latest one.

Write access is never broader than this: Codex never gets full access, only the folder (plus temporary folders) and the network, and in both tools connected apps, plugins, MCP servers and hooks stay off.

## Models

`/peer models` (or `$peer models`, or `peer models`) shows a table of the models and effort levels each tool accepts, with the recommended model and the one in use marked.

- **Codex:** the list comes from Codex's own model catalog, in Codex's order, and the first entry is its recommended model.
- **Claude Code:** it has no command that lists models, so peer shows the aliases `fable`, `opus`, `sonnet` and `haiku`, which always point to the newest version, plus any extra model Claude Code offers your account. The recommendation, `opus`, is peer's own pick. Add `[1m]` to an alias, such as `opus[1m]`, for a 1M-token context. Full IDs such as `claude-opus-5-5` also work.

Use `/peer models codex` or `/peer models claude` to show one tool only.
