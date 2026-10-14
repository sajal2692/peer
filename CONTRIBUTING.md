# Contributing

## Checks

```bash
python3 -m unittest discover -s tests          # the test suite, no packages needed
uvx ruff@0.17.0 check . && uvx ruff@0.17.0 format --check .
python3 scripts/check_dashes.py                # the project avoids em dashes
```

peer uses only the Python standard library and runs on Python 3.9 or newer. `peer.py` is a small launcher that `install.sh` links onto PATH; the code is the `peer` package in `src/peer`:

| Module | What it does |
|---|---|
| `cli.py` | The command line: flags, the modes, and printing the answer |
| `sessions.py` | Finds the session to read: the current one, `--session`, or `--last` |
| `transcript.py` | The conversation model (`Item`, `Session`) and helpers both readers share |
| `claude_transcript.py` | Reads a Claude Code transcript |
| `codex_transcript.py` | Reads a Codex rollout |
| `request.py` | Turns a session into the prompt: turns, rendering, trimming, the three modes |
| `runner.py` | Runs the other tool headless and read-only, and parses its answer |
| `settings.py` | Flags, `PEER_*` variables and the config file, and `peer config` |
| `models.py` | `peer models` |
| `output.py` | The header printed above the answer |
| `common.py` | Tool names, the Claude Code and Codex home folders, `PeerError` |

The skills live in `skills/claude/peer` and `skills/codex/peer`. `tests/test_cli.py` runs peer end to end against fake `codex` and `claude` binaries, so the tests need neither tool installed.

CI runs on every push and pull request: lint, the tests on Ubuntu with Python 3.9, 3.12, 3.13, 3.14 and 3.15 and on macOS with Python 3.9, a non-blocking run on the newest Python build including pre-releases, a coverage floor of 80 percent, and a test install into an empty home folder.

## How it works

1. Claude Code and Codex tell the commands they run which session they belong to (`CLAUDE_CODE_SESSION_ID`, `CODEX_THREAD_ID`). peer reads that session's transcript from `~/.claude/projects` or `~/.codex/sessions`.
2. It renders the transcript as plain text. Inputs the system injected (task notifications, messages from other sessions, subagent reports, compaction summaries) are labelled, not shown as the user. Pasted images are written to temporary files for the run.
3. It drops the message that invoked peer, and everything after it.
4. It runs the other tool headless in the same folder:
   - Codex: `codex exec --sandbox read-only`, with connected apps, plugins, MCP servers, and browser and computer control switched off.
   - Claude Code: `claude -p --tools Read,Grep,Glob,WebSearch,WebFetch`, with MCP servers, hooks and skills off.
5. It prints a header and the answer. The skill shows that output in the asking tool.

Plain text, rather than each tool's own session import, works the same in both directions and never changes the user's existing sessions.

## Known limits

- Tested on macOS only. Linux should work when `codex` and `claude` are on PATH.
- Older Claude Code transcripts don't record where each message came from, so peer guesses from the text.
- Codex-side notifications are not labelled yet.
- Codex keeps its compaction summaries encrypted, so only the raw history is sent.
- peer reads the whole transcript into memory.

## Releasing

1. Update `__version__` in `src/peer/__init__.py` and add a section to `CHANGELOG.md`.
2. Commit, then push a tag that matches: `git tag v0.1.1 && git push origin v0.1.1`.
3. The release workflow runs the checks and drafts a GitHub release from the changelog section. Review and publish it.

Keep `README.md` about installing and using peer, `docs/configuration.md` complete for every option and setting, and implementation notes here.
