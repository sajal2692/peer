"""Runs the other tool headless, read-only unless --write, and reads its answer."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import common
from .common import TOOL_NAMES, PeerError
from .settings import Setting

# Codex ships inside the ChatGPT desktop app and is often newer than the CLI on PATH.
CODEX_BUNDLED = [
    "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex",
    "/Applications/Codex.app/Contents/Resources/codex",
]
CLAUDE_FALLBACKS = [str(Path.home() / ".local/bin/claude"), str(Path.home() / ".claude/local/claude")]

# The only tools the Claude peer has. None of them can change files or run commands.
CLAUDE_READ_ONLY_TOOLS = ["Read", "Grep", "Glob"]
# On by default; --no-web removes them. With web access, a prompt injection in the transcript
# or a file could have the peer put what it read into a URL, so --no-web is there for that.
CLAUDE_WEB_TOOLS = ["WebSearch", "WebFetch"]
# Added by --write: editing files and running commands, approved without asking.
CLAUDE_WRITE_TOOLS = ["Edit", "Write", "Bash"]

# Session-specific variables that must not leak into the peer process.
SCRUB_EXACT = {
    "CLAUDECODE",
    "CLAUDE_PID",
    "CLAUDE_EFFORT",
    "CLAUDE_PLUGIN_ROOT",
    "CLAUDE_PLUGIN_DATA",
    "CODEX_THREAD_ID",
    "AI_AGENT",
}
SCRUB_PREFIXES = ("CLAUDE_CODE_", "CLAUDE_AGENT_SDK", "CODEX_COMPANION_")
# The user's own Claude Code settings, which the CLI needs: sign-in tokens, provider choice,
# output limits. Everything else under CLAUDE_CODE_ is per-session and is dropped.
SCRUB_KEEP_PREFIXES = (
    "CLAUDE_CODE_USE_",
    "CLAUDE_CODE_SKIP_",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_API_KEY_HELPER_TTL_MS",
    "CLAUDE_CODE_CLIENT_CERT",
    "CLAUDE_CODE_CLIENT_KEY",
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
    "CLAUDE_CODE_GIT_BASH_PATH",
)


def child_env() -> dict:
    env = {}
    for key, value in os.environ.items():
        if key in SCRUB_EXACT:
            continue
        if key.startswith(SCRUB_PREFIXES) and not key.startswith(SCRUB_KEEP_PREFIXES):
            continue
        env[key] = value
    env["PEER_ACTIVE"] = "1"
    return env


def version_of(binary: str) -> tuple[int, ...]:
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return ()
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    return tuple(int(g) for g in match.groups()) if match else ()


def find_binary(tool: str) -> str:
    override = os.environ.get(f"PEER_{tool.upper()}_BIN")
    if override:
        return override
    candidates = []
    on_path = shutil.which(tool)
    if on_path:
        candidates.append(on_path)
    candidates += [c for c in (CODEX_BUNDLED if tool == "codex" else CLAUDE_FALLBACKS) if os.access(c, os.X_OK)]
    candidates = list(dict.fromkeys(candidates))
    if not candidates:
        raise PeerError(f"{TOOL_NAMES[tool]} CLI not found. Set PEER_{tool.upper()}_BIN to its path.")
    if len(candidates) == 1:
        return candidates[0]
    return max(candidates, key=version_of)


# Codex features that reach outside the folder: connected apps (mail, calendar, GitHub writes),
# plugins and their MCP servers, browser and computer control. The peer never needs them.
CODEX_RISKY_FEATURES = (
    "apps",
    "plugins",
    "browser_use",
    "browser_use_external",
    "computer_use",
    "in_app_browser",
    "in_app_local_automation",
    "image_generation",
    "hooks",
)
# MCP servers in config.toml, as tables ([mcp_servers.name]) or dotted keys (mcp_servers.name.command = ...).
MCP_SERVER_RE = re.compile(r'^\s*\[?\s*mcp_servers\.(?:"([^"]+)"|([A-Za-z0-9_-]+))[.\]\s=]', re.M)


def parse_feature_rows(out: str) -> list[list[str]]:
    """The rows of `codex features list`: a name first, whether it is on last."""
    rows = [line.split() for line in out.splitlines() if len(line.split()) >= 2]
    return [row for row in rows if row[-1] in ("true", "false")]


def codex_lockdown(binary: str, web: bool = False) -> list[str]:
    """Flags that switch off every Codex tool able to act outside the read-only sandbox.

    Feature names vary between Codex versions and an unknown name is an error, so only the
    features this Codex reports as enabled are turned off. If the list can't be read, peer
    stops rather than run Codex with those tools on.
    """
    flags: list[str] = []
    try:
        proc = subprocess.run(
            [binary, "features", "list"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=30,
            env=child_env(),
        )
        out = proc.stdout if proc.returncode == 0 else ""
    except FileNotFoundError as exc:
        raise PeerError(f"Could not start Codex: {exc}") from None
    except (OSError, subprocess.SubprocessError):
        out = ""
    rows = parse_feature_rows(out)
    if not rows:
        raise PeerError(
            "Could not read Codex's feature list (codex features list), so peer cannot switch off the Codex "
            "tools that act outside its read-only sandbox. Update Codex, or set PEER_CODEX_BIN to a newer one."
        )
    enabled = {row[0] for row in rows if row[-1] == "true"}
    for feature in CODEX_RISKY_FEATURES:
        if feature in enabled:
            flags += ["--disable", feature]
    try:
        config = (common.CODEX_HOME / "config.toml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        config = ""
    seen = set()
    for quoted, bare in MCP_SERVER_RE.findall(config):
        name = quoted or bare
        if name in seen:
            continue
        seen.add(name)
        key = f'"{name}"' if quoted else name
        flags += ["-c", f"mcp_servers.{key}.enabled=false"]
    if not web:
        flags += ["-c", 'web_search="disabled"']
    return flags


def codex_command(
    binary: str,
    cwd: str,
    out_file: Path,
    model: str | None,
    effort: str | None,
    ephemeral: bool,
    lockdown: list[str] | None = None,
    write: bool = False,
    resume: str | None = None,
) -> list[str]:
    """codex exec, read-only by default. --write lets it change files in the folder and reach
    the network. With resume, the run continues that earlier Codex session."""
    cmd = [
        binary,
        "exec",
        "--sandbox",
        "workspace-write" if write else "read-only",
        *(["-c", "sandbox_workspace_write.network_access=true"] if write else []),
        "--skip-git-repo-check",
        "--color",
        "never",
        "-C",
        cwd,
        "-o",
        str(out_file),
        *(lockdown or []),
    ]
    if model:
        cmd += ["-m", model]
    if effort:
        cmd += ["-c", f"model_reasoning_effort={effort}"]
    if ephemeral:
        cmd.append("--ephemeral")
    if resume:
        cmd += ["resume", resume]
    cmd.append("-")
    return cmd


def claude_command(
    binary: str,
    model: str | None,
    effort: str | None,
    ephemeral: bool,
    web: bool = False,
    write: bool = False,
    resume: str | None = None,
) -> list[str]:
    """Claude Code in print mode with only reading tools available, unless --write.

    --tools limits which tools exist at all, so permission rules in the user's settings cannot
    re-enable Bash or editing. MCP servers, hooks and skills are switched off for the same reason.
    """
    tools = ",".join(CLAUDE_READ_ONLY_TOOLS + (CLAUDE_WRITE_TOOLS if write else []) + (CLAUDE_WEB_TOOLS if web else []))
    cmd = [
        binary,
        "-p",
        "--output-format",
        "json",
        "--tools",
        tools,
        "--allowedTools",
        tools,
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--settings",
        json.dumps({"disableAllHooks": True}),
        "--permission-mode",
        "acceptEdits" if write else "default",
    ]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]
    if ephemeral:
        cmd.append("--no-session-persistence")
    if resume:
        cmd += ["--resume", resume]
    return cmd


@dataclass
class Answer:
    text: str
    session_id: str | None
    model: str | None
    effort: str | None
    resume: str | None


def parse_codex_run(returncode: int, stderr: str, text: str, ephemeral: bool) -> Answer:
    session = re.search(r"^session id:\s*(\S+)", stderr, re.M)
    model = re.search(r"^model:\s*(\S+)", stderr, re.M)
    effort = re.search(r"^reasoning effort:\s*(\S+)", stderr, re.M)
    if returncode != 0 or not text:
        errors = [line for line in stderr.splitlines() if line.startswith("ERROR")] or stderr.splitlines()[-5:]
        raise PeerError("Codex failed:\n" + ("\n".join(errors) or f"exit code {returncode}, no answer"))
    session_id = session.group(1) if session else None
    resume = f"codex resume {session_id}" if session_id and not ephemeral else None
    return Answer(text, session_id, model.group(1) if model else None, effort.group(1) if effort else None, resume)


def parse_claude_run(returncode: int, stdout: str, stderr: str, ephemeral: bool) -> Answer:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        raise PeerError("Claude Code failed:\n" + (stderr.strip() or stdout.strip())[-2000:])
    if returncode != 0 or data.get("is_error"):
        raise PeerError(f"Claude Code failed: {data.get('result') or stderr.strip() or f'exit code {returncode}'}")
    text = data.get("result")
    if not isinstance(text, str) or not text.strip():
        raise PeerError("Claude Code returned no answer.")
    session_id = data.get("session_id")
    usage = data.get("modelUsage") if isinstance(data.get("modelUsage"), dict) else {}
    # Claude Code may call a small model for side tasks; the main model writes the most.
    model = max(usage, key=lambda m: (usage[m] or {}).get("outputTokens", 0)) if usage else None
    resume = f"claude --resume {session_id}" if session_id and not ephemeral else None
    return Answer(text.strip(), session_id, model, None, resume)


def run_process(cmd: list[str], prompt: str, timeout: int, cwd: str | None, name: str) -> tuple[int, str, str]:
    """Runs cmd with prompt on stdin. On timeout the whole process group is stopped, so tools the
    peer started do not outlive it."""
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=child_env(),
            cwd=cwd,
            start_new_session=True,
        )
    except OSError as exc:
        raise PeerError(f"Could not start {name}: {exc}") from None
    try:
        stdout, stderr = proc.communicate(prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
            except OSError:
                break
            try:
                proc.wait(timeout=5)
                break
            except subprocess.TimeoutExpired:
                continue
        proc.communicate()
        raise PeerError(f"{name} did not finish within {timeout}s.") from None
    except BaseException:
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)
        raise
    return proc.returncode, stdout, stderr


class Run:
    """One peer call: the command it will run, and running it."""

    def __init__(
        self,
        peer: str,
        cwd: str,
        model: Setting,
        effort: Setting,
        args,
        web: bool = False,
        write: bool = False,
        resume: str | None = None,
        access: Setting | None = None,
    ):
        self.peer, self.cwd, self.model, self.effort, self.args, self.web = peer, cwd, model, effort, args, web
        self.write, self.resume = write, resume
        self.access = access or Setting("write" if write else "read-only", "--write" if write else "default")
        binary = find_binary(peer)
        if peer == "codex":
            # A dry run only shows the command, so it skips probing Codex's features.
            lockdown = ["<lockdown flags>"] if args.dry_run else codex_lockdown(binary, web)
            self.tmp = tempfile.TemporaryDirectory(prefix="peer-")
            self.out_file = Path(self.tmp.name) / "answer.md"
            self.cmd = codex_command(
                binary, cwd, self.out_file, model.value, effort.value, args.ephemeral, lockdown, write, resume
            )
        else:
            self.tmp, self.out_file = None, None
            self.cmd = claude_command(binary, model.value, effort.value, args.ephemeral, web, write, resume)

    def close(self) -> None:
        if self.tmp:
            self.tmp.cleanup()
            self.tmp = None

    def display(self, prompt_chars: int) -> str:
        where = "" if self.peer == "codex" else f"(in {self.cwd}) "
        return f"{where}{shlex.join(self.cmd)} < prompt on stdin ({prompt_chars:,} chars)"

    def __call__(self, prompt: str) -> Answer:
        name = TOOL_NAMES[self.peer]
        try:
            cwd = None if self.peer == "codex" else self.cwd
            code, stdout, stderr = run_process(self.cmd, prompt, self.args.timeout, cwd, name)
            text = ""
            if self.out_file and self.out_file.exists():
                text = self.out_file.read_text(encoding="utf-8", errors="replace").strip()
        finally:
            self.close()
        if self.peer == "codex":
            return parse_codex_run(code, stderr, text, self.args.ephemeral)
        return parse_claude_run(code, stdout, stderr, self.args.ephemeral)
