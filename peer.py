#!/usr/bin/env python3
"""peer: ask the other coding agent to answer from the same conversation.

Run it inside a Claude Code or Codex session, usually through the /peer (Claude
Code) or $peer (Codex) skill. peer reads the current session's transcript,
renders it as plain text, sends it to the other tool in headless read-only mode,
and prints that tool's answer so it lands back in the session that asked.

Modes:
  peer                      the other tool answers your last message itself
  peer review [focus]       the other tool critiques the last reply
  peer "question"           the other tool answers a new question, with the
                            conversation so far as context (or none, in a new
                            session)
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import glob
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

__version__ = "0.1.0"

TOOL_NAMES = {"claude": "Claude Code", "codex": "Codex"}
# Limits that --trim applies, in characters. Without --trim nothing is cut.
TRIM_DEFAULTS = {"message": 20_000, "tool_input": 300, "tool_output": 500, "summary": 4_000, "budget": 400_000}
MARKER = "[peer request]"

CLAUDE_HOME = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))

# Codex ships inside the ChatGPT desktop app and is often newer than the CLI on PATH.
CODEX_BUNDLED = [
    "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex",
    "/Applications/Codex.app/Contents/Resources/codex",
]
CLAUDE_FALLBACKS = [str(Path.home() / ".local/bin/claude"), str(Path.home() / ".claude/local/claude")]

# The only tools the Claude peer has. None of them can change files or run commands.
CLAUDE_READ_ONLY_TOOLS = ["Read", "Grep", "Glob", "WebSearch", "WebFetch"]

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
SCRUB_KEEP_PREFIXES = ("CLAUDE_CODE_USE_",)

# Codex injects these as user-role messages; they are not things the user typed.
CODEX_WRAPPER_PREFIXES = (
    "# AGENTS.md instructions",
    "<environment_context>",
    "<user_instructions>",
    "<permissions instructions>",
    "<external_",
    "<turn_aborted>",
    "<skills_instructions>",
    "<collaboration_mode>",
)

REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
COMMAND_RE = re.compile(r"<command-name>(.*?)</command-name>", re.S)
COMMAND_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.S)


class PeerError(Exception):
    pass


@dataclass
class Item:
    # "user" (typed by the user), "input" (injected by the system, see label),
    # "summary" (a compaction summary), "assistant", "tool" (a call) or "result" (its output)
    role: str
    text: str
    label: str = ""
    # The untrimmed form, when text is a short summary (for example a tool call's full input).
    detail: str = ""
    # Supplied by the user (an attached file, a hook's context), so it is never withheld.
    user_supplied: bool = False

    @property
    def starts_turn(self) -> bool:
        return self.role in ("user", "input")

    @property
    def heading(self) -> str:
        return self.label or "User"


@dataclass
class Session:
    tool: str
    path: Path
    cwd: str | None
    items: list[Item]
    # Inline images, referenced from item text as "[image #N]" (N is the index here).
    images: list = field(default_factory=list)
    # Instruction files (CLAUDE.md and the like) the host loaded into its context.
    instruction_files: list = field(default_factory=list)


# ---------------------------------------------------------------- reading


def read_jsonl(path: Path) -> Iterator[dict]:
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue  # a partially written last line
            if isinstance(row, dict):
                yield row


IMAGE_REF_RE = re.compile(r"\[image #(\d+)\]")
DATA_URL_RE = re.compile(r"^data:(image/[\w.+-]+);base64,(.*)$", re.S)


def image_ref(block: dict, images: list | None) -> str:
    """A placeholder for an image block. Inline images are kept so they can be saved to files."""
    path = block.get("path") or ""
    if path:
        return f"[image: {path}]"
    source = block.get("source") if isinstance(block.get("source"), dict) else {}
    media, data = source.get("media_type"), source.get("data")
    url = block.get("image_url")
    if isinstance(url, dict):
        url = url.get("url")
    if isinstance(url, str):
        match = DATA_URL_RE.match(url)
        if match:
            media, data = match.groups()
    if images is not None and isinstance(data, str) and data:
        images.append((media or "image/png", data))
        return f"[image #{len(images) - 1}]"
    return "[image]"


def blocks_text(content, images: list | None = None) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type", "")
        if kind.lower() in ("text", "input_text", "output_text", "inputtext"):
            parts.append(block.get("text", ""))
        elif "image" in kind.lower():
            parts.append(image_ref(block, images))
    return "\n".join(p for p in parts if p)


def save_images(prompt: str, images: list, folder: Path) -> str:
    """Writes the images the prompt refers to into folder and points the prompt at the files."""

    def write(match):
        index = int(match.group(1))
        if index >= len(images):
            return "[image]"
        media, data = images[index]
        target = folder / f"image-{index}.{media.split('/')[-1].replace('jpeg', 'jpg')}"
        try:
            target.write_bytes(base64.b64decode(data))
        except (ValueError, OSError):
            return "[image]"
        return f"[image: {target}]"

    return IMAGE_REF_RE.sub(write, prompt)


def full_input(name: str, value) -> str:
    return f"{name}: {value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)}"


def tool_item(name: str, value, brief: str | None = None) -> Item:
    """A tool call: a one-line summary for trimmed output, and the full input otherwise."""
    return Item("tool", f"{name}: {brief if brief is not None else brief_input(value)}", detail=full_input(name, value))


def brief_input(value) -> str:
    if isinstance(value, dict):
        for key in ("command", "cmd", "file_path", "path", "pattern", "url", "query", "description", "prompt"):
            if isinstance(value.get(key), str):
                return value[key]
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def merge_assistant(items: list[Item]) -> list[Item]:
    merged: list[Item] = []
    for item in items:
        if merged and item.role == "assistant" and merged[-1].role == "assistant":
            merged[-1] = Item("assistant", merged[-1].text + "\n\n" + item.text)
        else:
            merged.append(item)
    return merged


ANSWER_LABEL = "User's answer to AskUserQuestion"
SUMMARY_LABEL = "Compaction summary"


def injected_label(row: dict, text: str) -> str | None:
    """Names a Claude Code user row that the system injected, or None if the user typed it."""
    origin = row.get("origin") if isinstance(row.get("origin"), dict) else {}
    if row.get("promptSource") == "sdk" and origin.get("kind") == "human":
        return None
    turn_origin = row.get("turnOrigin")
    start = text.lstrip()
    if turn_origin == "scheduled" or start.startswith("<scheduled-task"):
        return "Scheduled task"
    if turn_origin == "peer" or start.startswith("<project_claude_message"):
        return "Message from another Claude session"
    if turn_origin == "task_notification" or start.startswith("<task-notification"):
        return "Task notification"
    if start.startswith("<agent-message"):
        return "Report from a subagent"
    if start.startswith("<relay "):
        return "Message from the coordinating Claude session"
    if row.get("promptSource") == "system" or origin.get("kind") not in (None, "human"):
        return "System input"
    return None


def ask_brief(value) -> str:
    questions = value.get("questions") if isinstance(value, dict) else None
    if isinstance(questions, list):
        return " | ".join(str(q.get("question", "")) for q in questions if isinstance(q, dict))
    return brief_input(value)


def queued_text(attachment: dict, images: list | None = None) -> str:
    prompt = attachment.get("prompt")
    return REMINDER_RE.sub("", prompt if isinstance(prompt, str) else blocks_text(prompt, images)).strip()


def claude_attachment(attachment: dict, duplicate: bool, images: list, instruction_files: list) -> list[Item]:
    """Context Claude Code adds through attachment rows rather than user messages."""
    kind = attachment.get("type")
    if kind == "queued_command":
        # A message sent while Claude Code was busy. Older versions also wrote it as a user row.
        text = queued_text(attachment, images)
        if not text or duplicate:
            return []
        origin = attachment.get("origin") if isinstance(attachment.get("origin"), dict) else {}
        if origin.get("kind") == "human":
            return [Item("user", text)]
        row = {"origin": origin}
        if origin:
            row["promptSource"] = "system"
        if attachment.get("commandMode") == "task-notification":
            row["turnOrigin"] = "task_notification"
        label = injected_label(row, text)
        return [Item("input", text, label)] if label else [Item("user", text)]
    if kind == "file":
        content = attachment.get("content") if isinstance(attachment.get("content"), dict) else {}
        name = attachment.get("filename") or ""
        if content.get("type") == "image":
            ref = image_ref(
                {
                    "source": {
                        "media_type": (content.get("file") or {}).get("type"),
                        "data": (content.get("file") or {}).get("base64"),
                    }
                },
                images,
            )
            return [
                Item("tool", f"attached image: {name}", user_supplied=True),
                Item("result", ref if ref != "[image]" else name, user_supplied=True),
            ]
        body = (content.get("file") or {}).get("content") if isinstance(content.get("file"), dict) else None
        return [
            Item("tool", f"attached file: {name}", user_supplied=True),
            Item("result", body if isinstance(body, str) else "", user_supplied=True),
        ]
    if kind == "at_mention_reference":
        mentions = attachment.get("mentions") or []
        text = "mentioned file (not read): " + ", ".join(map(str, mentions))
        return [Item("tool", text, user_supplied=True)] if mentions else []
    if kind == "hook_additional_context":
        texts = attachment.get("content") or []
        text = "\n".join(t for t in texts if isinstance(t, str)) if isinstance(texts, list) else str(texts)
        if not text.strip():
            return []
        return [Item("tool", "context added by a hook", user_supplied=True), Item("result", text, user_supplied=True)]
    if kind == "instructions":
        for entry in attachment.get("files") or []:
            if isinstance(entry, dict) and entry.get("path") and entry["path"] not in instruction_files:
                instruction_files.append(entry["path"])
    if kind == "nested_memory" and attachment.get("path") and attachment["path"] not in instruction_files:
        instruction_files.append(attachment["path"])
    return []


def queued_copy_follows(rows: list[dict], index: int, attachment: dict, window: int = 40) -> bool:
    """True when a queued message is also written as a user row shortly after it, as older
    Claude Code versions did. Matching nearby rows, rather than any earlier text, keeps a
    message the user sends twice."""
    text = " ".join(queued_text(attachment).split())
    for row in rows[index + 1 : index + 1 + window]:
        if row.get("type") == "user":
            content = (row.get("message") or {}).get("content")
            if " ".join(REMINDER_RE.sub("", blocks_text(content)).split()) == text:
                return True
    return False


def parse_claude(path: Path) -> Session:
    items: list[Item] = []
    images: list = []
    instruction_files: list = []
    cwd = None
    local_command_next = False
    ask_ids: set = set()
    rows = [r for r in read_jsonl(path) if not r.get("isSidechain")]  # sidechains are subagent traffic
    for index, row in enumerate(rows):
        cwd = row.get("cwd") or cwd
        content = (row.get("message") or {}).get("content")
        kind = row.get("type")
        if kind == "attachment" and isinstance(row.get("attachment"), dict):
            attachment = row["attachment"]
            duplicate = attachment.get("type") == "queued_command" and queued_copy_follows(rows, index, attachment)
            items += claude_attachment(attachment, duplicate, images, instruction_files)
            continue
        if kind == "user":
            if row.get("isCompactSummary"):
                # The raw history it summarizes is still in the file; this shows what the
                # session carried forward after compaction.
                text = REMINDER_RE.sub("", blocks_text(content)).strip()
                if text:
                    items.append(Item("summary", text, SUMMARY_LABEL))
                continue
            if row.get("isMeta"):
                # Built-in commands (/model, /compact) are announced by a caveat.
                text = blocks_text(content)
                if "<local-command-caveat>" in text:
                    local_command_next = True
                elif row.get("promptSource") == "system":
                    label = injected_label(row, text)
                    text = REMINDER_RE.sub("", text).strip()
                    if label and text:
                        items.append(Item("input", text, label))
                continue
            if (
                isinstance(content, list)
                and content
                and all(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
            ):
                for block in content:
                    text = blocks_text(block.get("content"))
                    if block.get("tool_use_id") in ask_ids:
                        items.append(Item("input", text, ANSWER_LABEL))
                    else:
                        items.append(Item("result", text))
                continue
            text = blocks_text(content, images)
            label = injected_label(row, text)
            if label:
                text = REMINDER_RE.sub("", text).strip()
                if text:
                    items.append(Item("input", text, label))
                local_command_next = False
                continue
            if "<local-command-stdout>" in text or "<local-command-stderr>" in text:
                continue
            command = COMMAND_RE.search(text)
            if command:
                if local_command_next:
                    local_command_next = False
                    continue
                args = COMMAND_ARGS_RE.search(text)
                text = f"{command.group(1).strip()} {args.group(1).strip() if args else ''}"
            local_command_next = False
            text = REMINDER_RE.sub("", text).strip()
            if text:
                items.append(Item("user", text))
        elif kind == "assistant" and isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text", "").strip():
                    items.append(Item("assistant", block["text"]))
                elif block.get("type") == "tool_use":
                    if block.get("name") == "AskUserQuestion":
                        ask_ids.add(block.get("id"))
                        items.append(tool_item("AskUserQuestion", block.get("input"), ask_brief(block.get("input"))))
                    else:
                        items.append(tool_item(str(block.get("name")), block.get("input")))
    return Session("claude", path, cwd, merge_assistant(items), images, instruction_files)


def codex_item(item: dict, images: list | None = None) -> list[Item]:
    kind = item.get("type")
    if kind == "UserMessage":
        text = blocks_text(item.get("content"), images)
        return [Item("user", text)] if text.strip() else []
    if kind == "AgentMessage":
        text = blocks_text(item.get("content"))
        return [Item("assistant", text)] if text.strip() else []
    if kind == "CommandExecution":
        command = item.get("command")
        if isinstance(command, list):
            shell_wrapped = len(command) >= 3 and command[-2] in ("-lc", "-c")
            command = command[-1] if shell_wrapped else " ".join(map(str, command))
        output = item.get("aggregated_output") or item.get("formatted_output") or ""
        exit_code = item.get("exit_code")
        if exit_code not in (None, 0, "0"):
            output = f"(exit {exit_code}) {output}"
        return [Item("tool", f"shell: {command}"), Item("result", output)]
    if kind == "McpToolCall":
        result = item.get("result")
        result_text = blocks_text(result.get("content")) if isinstance(result, dict) else str(result or "")
        return [
            tool_item(f"{item.get('server')}.{item.get('tool')}", item.get("arguments")),
            Item("result", result_text or str(item.get("error") or "")),
        ]
    if kind == "FileChange":
        changes = item.get("changes") or {}
        if not isinstance(changes, dict):
            changes = {}
        lines = [f"{(c or {}).get('type', 'change')} {p}" for p, c in changes.items()]
        return [tool_item("edit files", changes, "; ".join(lines))] if lines else []
    if kind == "WebSearch":
        return [Item("tool", f"web search: {item.get('query') or brief_input(item.get('action'))}")]
    if kind == "ImageView":
        return [Item("tool", f"view image: {str(item.get('path') or '').replace('file://', '', 1)}")]
    if kind == "Extension":
        action = item.get("action") if isinstance(item.get("action"), dict) else {}
        target = item.get("query") or action.get("url") or brief_input(action)
        return [Item("tool", f"{item.get('kind') or 'extension'}: {target}")]
    if kind == "DynamicToolCall":
        call = tool_item(f"{item.get('namespace')}.{item.get('tool')}", item.get("arguments"))
        output = item.get("content_items") or item.get("output") or item.get("result")
        if output in (None, "", []):
            return [call]
        return [call, Item("result", output if isinstance(output, str) else json.dumps(output, ensure_ascii=False))]
    return []  # reasoning and compaction markers


def codex_legacy(rows: list[dict]) -> list[Item]:
    """Older rollouts and imported threads, which have no item_completed events.

    Messages come from user_message and agent_message events when there are any, since the
    matching response_item messages include context Codex injects. Tool calls only exist as
    response_items, so they are always read from there, in file order.
    """
    has_events = any(
        r.get("type") == "event_msg" and (r.get("payload") or {}).get("type") in ("user_message", "agent_message")
        for r in rows
    )
    items: list[Item] = []
    for r in rows:
        p = r.get("payload") if isinstance(r.get("payload"), dict) else {}
        if r.get("type") == "event_msg" and p.get("message"):
            if p.get("type") == "user_message":
                items.append(Item("user", p["message"]))
            elif p.get("type") == "agent_message":
                items.append(Item("assistant", p["message"]))
        if r.get("type") != "response_item":
            continue
        if p.get("type") == "message" and p.get("role") in ("user", "assistant") and not has_events:
            text = blocks_text(p.get("content"))
            if p["role"] == "user" and text.lstrip().startswith(CODEX_WRAPPER_PREFIXES):
                continue
            if text.strip():
                items.append(Item(p["role"], text))
        elif p.get("type") in ("function_call", "custom_tool_call"):
            items.append(tool_item(str(p.get("name")), p.get("arguments") or p.get("input")))
        elif p.get("type") in ("function_call_output", "custom_tool_call_output"):
            out = p.get("output")
            items.append(Item("result", out if isinstance(out, str) else blocks_text(out)))
    return items


def is_completed_item(row: dict) -> bool:
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    return (
        row.get("type") == "event_msg"
        and payload.get("type") == "item_completed"
        and isinstance(payload.get("item"), dict)
    )


def parse_codex(path: Path) -> Session:
    rows = list(read_jsonl(path))
    cwd = None
    for r in rows:
        if r.get("type") in ("session_meta", "turn_context"):
            cwd = (r.get("payload") or {}).get("cwd") or cwd
    images: list = []
    # A thread started on an older Codex and continued on a newer one has both formats: the
    # old one up to the first item_completed event, the new one after it.
    first = next((i for i, r in enumerate(rows) if is_completed_item(r)), len(rows))
    items = codex_legacy(rows[:first])
    for row in rows[first:]:
        if is_completed_item(row):
            items += codex_item(row["payload"]["item"], images)
    return Session("codex", path, cwd, merge_assistant(items), images)


def parse_session(tool: str, path: Path) -> Session:
    return parse_claude(path) if tool == "claude" else parse_codex(path)


# ---------------------------------------------------------------- finding sessions


def claude_project_dir(cwd: str) -> Path:
    return CLAUDE_HOME / "projects" / re.sub(r"[^A-Za-z0-9]", "-", cwd)


def find_claude_by_id(session_id: str) -> Path | None:
    hits = glob.glob(str(CLAUDE_HOME / "projects" / "*" / f"{session_id}.jsonl"))
    return Path(max(hits, key=os.path.getmtime)) if hits else None


def find_codex_by_id(thread_id: str) -> Path | None:
    for root in ("sessions", "archived_sessions"):
        hits = glob.glob(str(CODEX_HOME / root / "**" / f"rollout-*{thread_id}.jsonl"), recursive=True)
        if hits:
            return Path(max(hits, key=os.path.getmtime))
    return None


def is_peer_session(path: Path) -> bool:
    """True for sessions that peer itself started in the other tool."""
    try:
        rows = read_jsonl(path)
        for _, row in zip(range(40), rows):
            if row.get("type") == "user":  # Claude Code
                return blocks_text((row.get("message") or {}).get("content")).startswith(MARKER)
            payload = row.get("payload") or {}
            is_user_message = payload.get("type") == "message" and payload.get("role") == "user"  # Codex
            if is_user_message and blocks_text(payload.get("content")).startswith(MARKER):
                return True
    except OSError:
        pass
    return False


def codex_session_cwd(path: Path) -> str | None:
    try:
        first = next(read_jsonl(path))
    except (StopIteration, OSError):
        return None
    return (first.get("payload") or {}).get("cwd")


def find_latest(tool: str, cwd: str) -> Path | None:
    if tool == "claude":
        files = sorted(claude_project_dir(cwd).glob("*.jsonl"), key=os.path.getmtime, reverse=True)
        return next((f for f in files if not is_peer_session(f)), None)
    files = sorted(glob.glob(str(CODEX_HOME / "sessions" / "*" / "*" / "*" / "rollout-*.jsonl")), reverse=True)
    for name in files[:500]:
        path = Path(name)
        if codex_session_cwd(path) == cwd and not is_peer_session(path):
            return path
    return None


def sniff_format(path: Path) -> str:
    """Which tool wrote a transcript file, from its rows; the folder decides only as a fallback."""
    try:
        for _, row in zip(range(20), read_jsonl(path)):
            if row.get("type") in ("session_meta", "response_item", "event_msg", "turn_context"):
                return "codex"
            if row.get("type") in ("user", "assistant", "attachment", "summary", "system"):
                return "claude"
    except (OSError, UnicodeDecodeError):
        pass
    return "codex" if str(path.resolve()).startswith(str(CODEX_HOME.resolve())) else "claude"


def detect_host() -> str:
    in_claude = bool(os.environ.get("CLAUDE_CODE_SESSION_ID"))
    in_codex = bool(os.environ.get("CODEX_THREAD_ID"))
    if in_claude and in_codex:
        raise PeerError("Both Claude Code and Codex session variables are set. Pass --from claude or --from codex.")
    if in_claude:
        return "claude"
    if in_codex:
        return "codex"
    raise PeerError("Not inside a Claude Code or Codex session. Pass --from with --last, or --session.")


def resolve_session(args) -> tuple[Session, bool]:
    """Returns the host session and whether its last turn is the peer request itself.

    Inside a session the last turn is the request that ran peer, so it is dropped.
    A session picked with --session or --last is read as it stands.
    """
    cwd = os.getcwd()
    if args.session:
        path = Path(args.session).expanduser()
        if path.exists():
            tool = args.source or sniff_format(path)
        else:
            found = [
                (t, p)
                for t, p in (("claude", find_claude_by_id(args.session)), ("codex", find_codex_by_id(args.session)))
                if p
            ]
            found = [(t, p) for t, p in found if not args.source or t == args.source]
            if not found:
                raise PeerError(f"No session file or id matches {args.session!r}.")
            tool, path = found[0]
        return parse_session(tool, path), False

    tool = args.source or detect_host()
    if args.last:
        path = find_latest(tool, cwd)
        if not path:
            raise PeerError(f"No {TOOL_NAMES[tool]} session found for {cwd}.")
        return parse_session(tool, path), False

    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID" if tool == "claude" else "CODEX_THREAD_ID")
    path = None
    if session_id:
        path = find_claude_by_id(session_id) if tool == "claude" else find_codex_by_id(session_id)
    if not path:
        path = find_latest(tool, cwd)
        if path:
            print(
                f"peer: no session id from {TOOL_NAMES[tool]}; using the newest session for this folder.",
                file=sys.stderr,
            )
    if not path:
        raise PeerError(f"Could not find the current {TOOL_NAMES[tool]} session transcript.")
    return parse_session(tool, path), True


# ---------------------------------------------------------------- building the request


def split_turns(items: list[Item]) -> list[list[Item]]:
    turns: list[list[Item]] = []
    for item in items:
        if item.starts_turn or not turns:
            turns.append([item])
        else:
            turns[-1].append(item)
    return turns


def clip(text: str, limit: int) -> str:
    text = text.strip()
    if limit <= 0 or len(text) <= limit:
        return text
    head = limit * 2 // 3
    tail = limit - head
    return f"{text[:head]}\n[... {len(text) - limit} characters cut ...]\n{text[-tail:]}"


@dataclass
class Limits:
    """How much of the transcript to send, in characters. 0 means no limit; the default sends it all."""

    message: int = 0
    tool_input: int = 0
    tool_output: int = 0
    summary: int = 0
    budget: int = 0  # the whole transcript; past it the middle turns are dropped
    tools: bool = True

    @classmethod
    def trimmed(cls, **overrides) -> Limits:
        return cls(**{**TRIM_DEFAULTS, **overrides})

    def describe(self) -> str:
        if not self.tools and not any((self.message, self.summary, self.budget)):
            return "everything except tool activity"
        if not any((self.message, self.tool_input, self.tool_output, self.summary, self.budget)):
            return "everything, untrimmed"
        parts = [
            f"{name.replace('_', ' ')} {value:,}"
            for name, value in (
                ("message", self.message),
                ("tool_input", self.tool_input),
                ("tool_output", self.tool_output),
                ("summary", self.summary),
                ("budget", self.budget),
            )
            if value
        ]
        return "trimmed: " + ", ".join(parts) + ("" if self.tools else ", no tool activity")


def render_turn(turn: list[Item], host_name: str, limits: Limits, withhold_reply: bool = False) -> str:
    out = []
    for item in turn:
        if item.starts_turn:
            out.append(f"### {item.heading}\n{clip(item.text, limits.message)}")
        elif withhold_reply and not item.user_supplied:
            continue
        elif item.role == "summary":
            out.append(
                f"### {SUMMARY_LABEL}\n({host_name} wrote this summary of the turns above when "
                f"the session was compacted; it carried on from it.)\n{clip(item.text, limits.summary)}"
            )
        elif item.role == "assistant":
            out.append(f"### {host_name}\n{clip(item.text, limits.message)}")
        elif not limits.tools:
            continue
        elif item.role == "tool":
            call = item.text if limits.tool_input else (item.detail or item.text)
            out.append(f"- tool call: {clip(call, limits.tool_input)}")
        elif item.role == "result" and item.text.strip():
            body = clip(item.text, limits.tool_output).replace("\n", "\n  ")
            out.append(f"  result: {body}")
    return "\n\n".join(out)


def render_transcript(turns: list[list[Item]], host_name: str, limits: Limits) -> tuple[str, int]:
    """Renders turns, dropping the oldest (but keeping the first) to fit the budget, if there is one."""
    budget = limits.budget
    rendered = [render_turn(t, host_name, limits) for t in turns]
    if budget <= 0 or sum(len(r) for r in rendered) <= budget or len(rendered) <= 2:
        return "\n\n".join(rendered), 0
    first, rest = rendered[0], rendered[1:]
    kept: list[str] = []
    used = len(first)
    for chunk in reversed(rest):
        if used + len(chunk) > budget and kept:
            break
        kept.insert(0, chunk)
        used += len(chunk)
    omitted = len(rest) - len(kept)
    note = f"[... {omitted} earlier turns omitted to fit the context budget ...]"
    transcript = "\n\n".join([first, note, *kept])
    # The first and newest turns can be larger than the budget on their own.
    return clip(transcript, budget), omitted


@dataclass
class Request:
    mode: str  # "ask", "review" or "question"
    prompt: str
    target: str  # the input being answered or reviewed, for the report
    target_label: str
    turns_sent: int
    turns_omitted: int
    limits: Limits = field(default_factory=Limits)


# How the request shows up in the transcript: "/peer ..." in Claude Code, "$peer ..." in the
# Codex CLI, and "[$peer](.../SKILL.md) ..." in the Codex app.
PEER_INVOCATION_RE = re.compile(r"^\s*(?:/peer\b|\$peer\b|\[\$peer\]\()")


def drop_from_last_typed(turns: list[list[Item]]) -> list[list[Item]]:
    """Drops the message that asked for peer, and everything after it.

    That is the newest typed message that invokes peer, looking back over the last few typed
    messages in case the user sent another while peer was starting. Without one (the agent
    called peer on its own, or the user asked in plain words) it is the last typed message.
    Inputs injected after it (task notifications, messages from other sessions) go too.
    """
    typed = [index for index, turn in enumerate(turns) if turn[0].role == "user"]
    for index in reversed(typed[-3:]):
        if PEER_INVOCATION_RE.match(turns[index][0].text):
            return turns[:index]
    return turns[: typed[-1]] if typed else turns


def build_request(session: Session, peer: str, mode: str, text: str, drop_current: bool, limits: Limits) -> Request:
    host_name, peer_name = TOOL_NAMES[session.tool], TOOL_NAMES[peer]
    cwd = session.cwd or os.getcwd()
    turns = split_turns(session.items)
    if drop_current:
        turns = drop_from_last_typed(turns)

    intro = (
        f"{MARKER}\n"
        f"You are {peer_name}, asked for an independent answer. The transcript below is a session "
        f"between a user and {host_name}, another AI assistant, working in {cwd}. You share the "
        f"working directory: read files to check facts, but do not modify anything, do not run "
        f"commands with side effects, and do not ask the user questions. If something is ambiguous, "
        f"state your assumption and continue. Do not comment on this setup or the transcript format. "
        f'Headings other than "User" and "{host_name}" mark inputs the system injected, such as '
        f"task notifications or messages from other sessions; the user did not type them. Images "
        f"appear as [image: path]; open the file when it matters."
    )
    if session.instruction_files:
        intro += (
            f"\n{host_name} was also following these instruction files; read them when they bear on "
            f"the answer: " + ", ".join(session.instruction_files)
        )

    if mode == "ask":
        if not turns or not turns[-1][0].starts_turn:
            raise PeerError('No earlier message to answer. Ask a question instead: peer "your question"')
        last = turns[-1][0]
        context, omitted = render_transcript(turns[:-1], host_name, limits)
        # Files and context the user attached to that message go along with it.
        attached = render_turn([last, *[i for i in turns[-1][1:] if i.user_supplied]], host_name, limits)
        attached = attached.split("\n", 1)[1] if "\n" in attached else clip(last.text, limits.message)
        if last.role == "user":
            what = "the user's latest message"
        else:
            what = f"the latest input, a {last.heading.lower()} injected by the system rather than typed by the user"
        prompt = (
            f"{intro}\n\n{host_name}'s reply to {what} has been withheld. Write your own reply to it, "
            f"as the assistant in this session would.\n\n"
            f"<transcript>\n{context or '(no earlier turns)'}\n</transcript>\n\n"
            f'<latest_input from="{last.heading}">\n{attached}\n</latest_input>'
        )
        return Request("ask", prompt, last.text, last.heading, len(turns), omitted)

    if mode == "review":
        if not turns:
            raise PeerError("No earlier reply to review.")
        last = turns[-1]
        if not any(i.role == "assistant" for i in last):
            raise PeerError(f"The last turn has no {host_name} reply to review.")
        transcript, omitted = render_transcript(turns, host_name, limits)
        focus = f"\n\nThe user wants you to focus on: {text}" if text else ""
        prompt = (
            f"{intro}\n\n<transcript>\n{transcript}\n</transcript>\n\n"
            f"Review {host_name}'s latest reply, the last one in the transcript. Lead with your verdict. "
            f"Then say what is correct, what is wrong or missing, the risks, and what you would do "
            f"differently. Check its claims against the files where you can.{focus}"
        )
        return Request("review", prompt, last[0].text, last[0].heading, len(turns), omitted)

    # A direct question, with whatever history exists as context.
    if not text:
        raise PeerError("Nothing to ask.")
    if not turns:
        prompt = (
            f"{MARKER}\nYou are {peer_name}. Working directory: {cwd}. Read files to check facts, but "
            f"do not modify anything or ask questions; state assumptions instead.\n\n{text}"
        )
        return Request("question", prompt, text, "Question", 0, 0)
    transcript, omitted = render_transcript(turns, host_name, limits)
    prompt = (
        f"{intro}\n\n<transcript>\n{transcript}\n</transcript>\n\n"
        f"The user now asks you directly, with the session above as context:\n\n"
        f"<question>\n{text}\n</question>"
    )
    return Request("question", prompt, text, "Question", len(turns), omitted)


# ---------------------------------------------------------------- running the peer


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


@dataclass
class Setting:
    value: str | None
    source: str  # where the value came from, for the report


def config_path() -> Path:
    return Path(os.environ.get("PEER_CONFIG") or Path.home() / ".config" / "peer" / "config.json").expanduser()


def load_config() -> dict:
    path = config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PeerError(f"Could not read {path}: {exc}") from None
    if not isinstance(data, dict):
        raise PeerError(f"{path} must hold a JSON object.")
    return data


def resolve_setting(tool: str, key: str, flag: str | None, config: dict) -> Setting:
    """The flag wins, then PEER_<TOOL>_<KEY>, then the config file, then the tool's own default."""
    if flag:
        return Setting(flag, f"--{key}")
    env_name = f"PEER_{tool.upper()}_{key.upper()}"
    if os.environ.get(env_name):
        return Setting(os.environ[env_name], env_name)
    section = config.get(tool)
    if isinstance(section, dict) and section.get(key):
        return Setting(str(section[key]), "peer config")
    return Setting(None, f"{TOOL_NAMES[tool]} default")


def resolve_switch(key: str, flag: bool | None, config: dict) -> Setting:
    """An on/off option, off unless the flag, PEER_<KEY> or the config file turns it on."""
    if flag is not None:
        return Setting("on" if flag else "off", f"--{key}" if flag else f"--no-{key}")
    env_name = f"PEER_{key.upper()}"
    env = os.environ.get(env_name, "").strip().lower()
    if env:
        return Setting("on" if env in ("1", "true", "yes", "on") else "off", env_name)
    if isinstance(config.get(key), bool):
        return Setting("on" if config[key] else "off", "peer config")
    return Setting("off", "default")


def resolve_assess(flag: bool | None, config: dict) -> Setting:
    """Whether the asking tool should add its own assessment after the answer."""
    return resolve_switch("assess", flag, config)


def own_default(tool: str, key: str) -> str | None:
    """The model or effort the tool itself is configured with, for display only."""
    try:
        if tool == "codex":
            name = "model" if key == "model" else "model_reasoning_effort"
            text = (CODEX_HOME / "config.toml").read_text(encoding="utf-8")
            # Top-level keys only: stop at the first [table].
            top = text.split("\n[", 1)[0]
            match = re.search(rf'^\s*{name}\s*=\s*"([^"]+)"', top, re.M)
            return match.group(1) if match else None
        data = json.loads((CLAUDE_HOME / "settings.json").read_text(encoding="utf-8"))
        value = data.get("model" if key == "model" else "effortLevel")
        return str(value) if value else None
    except (OSError, ValueError):
        return None


CONFIG_USAGE = """Change a setting:
  peer config codex model <name>      model when Codex answers ("default" to clear)
  peer config codex effort <level>    reasoning effort when Codex answers
  peer config claude model <name>     model when Claude Code answers
  peer config claude effort <level>   effort when Claude Code answers
  peer config assess on|off           your tool's assessment after the answer
  peer config details on|off          full run header instead of the stats line
  peer config trim on|off             cut long messages and tool output with sensible limits
  peer config budget <chars>          most transcript characters to send ("default" for no limit)"""


def show_config() -> str:
    config = load_config()
    shown = str(config_path()).replace(str(Path.home()), "~", 1)
    lines = [f"peer settings ({shown})", ""]
    for tool in ("codex", "claude"):
        parts = []
        for key in ("model", "effort"):
            setting = resolve_setting(tool, key, None, config)
            if setting.value:
                parts.append(f"{key} {setting.value} (from {setting.source})")
            else:
                own = own_default(tool, key)
                parts.append(
                    f"{key} {own} ({TOOL_NAMES[tool]}'s own config)" if own else f"{key}: {TOOL_NAMES[tool]}'s default"
                )
        lines.append(f"When {TOOL_NAMES[tool]} answers: " + ", ".join(parts))
    for key, label in (("assess", "Assessment"), ("details", "Full header")):
        setting = resolve_switch(key, None, config)
        source = "default" if setting.source == "default" else f"from {setting.source}"
        lines.append(f"{label}: {setting.value} ({source})")
    trim = resolve_switch("trim", None, config)
    lines.append(f"Trim: {trim.value} ({'default' if trim.source == 'default' else 'from ' + trim.source})")
    budget = config.get("budget") if isinstance(config.get("budget"), int) else None
    if budget:
        lines.append(f"Context budget: {budget:,} characters (from peer config)")
    return "\n".join([*lines, "", CONFIG_USAGE])


def config_command(words: list[str]) -> int:
    """peer config [codex|claude model|effort VALUE] [assess|details on|off]"""
    try:
        if not words:
            print(show_config())
            return 0
        config = load_config()
        if len(words) == 3 and words[0] in TOOL_NAMES and words[1] in ("model", "effort"):
            tool, key, value = words
            section = config.get(tool) if isinstance(config.get(tool), dict) else {}
            if value.lower() in ("default", "none", "unset"):
                section.pop(key, None)
            else:
                section[key] = value
            if section:
                config[tool] = section
            else:
                config.pop(tool, None)
        elif len(words) == 2 and words[0] in ("assess", "details", "trim") and words[1].lower() in ("on", "off"):
            config[words[0]] = words[1].lower() == "on"
        elif len(words) == 2 and words[0] == "budget" and (words[1].isdigit() or words[1] == "default"):
            if words[1] == "default":
                config.pop("budget", None)
            else:
                config["budget"] = int(words[1])
        else:
            raise PeerError("Not a setting.\n" + CONFIG_USAGE)
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".peer-config-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(config, indent=2) + "\n")
        os.replace(tmp, path)
        print(show_config())
        return 0
    except (PeerError, OSError) as exc:
        print(f"peer: {exc}", file=sys.stderr)
        return 1


CLAUDE_ALIASES = [
    ("fable", "latest Fable model"),
    ("opus", "latest Opus model"),
    ("sonnet", "latest Sonnet model"),
    ("haiku", "latest Haiku model, fastest"),
]


def codex_models(binary: str) -> list[dict]:
    """Codex's own model catalog, the list its model picker shows, best first."""
    try:
        out = subprocess.run(
            [binary, "debug", "models"], capture_output=True, text=True, timeout=60, env=child_env()
        ).stdout
        models = json.loads(out).get("models") or []
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        raise PeerError("Could not read Codex's model list (codex debug models failed).") from None
    shown = [m for m in models if isinstance(m, dict) and m.get("slug") and m.get("visibility") == "list"]
    return sorted(shown, key=lambda m: m.get("priority", 999))


def claude_efforts(binary: str) -> list[str]:
    try:
        out = subprocess.run([binary, "--help"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    match = re.search(r"--effort.*?\(([a-z, ]+)\)", out, re.S)
    return [e.strip() for e in match.group(1).split(",")] if match else ["low", "medium", "high", "xhigh", "max"]


def claude_extra_models() -> list[tuple[str, str]]:
    """Models Claude Code offers this account beyond the aliases, from its own cache."""
    try:
        data = json.loads((Path.home() / ".claude.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    extra = []
    for option in data.get("additionalModelOptionsCache") or []:
        if isinstance(option, dict) and option.get("value"):
            extra.append((str(option["value"]), str(option.get("description") or option.get("label") or "")))
    return extra


def table(headers: list[str], rows: list[list[str]]) -> list[str]:
    """A Markdown table, which both tools render when they show peer's output."""

    def cell(value: str) -> str:
        return value.replace("|", "\\|").replace("\n", " ").strip()

    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return lines


def list_models(tools: list[str]) -> str:
    config = load_config()
    out: list[str] = []
    for tool in tools:
        current = resolve_setting(tool, "model", None, config).value or own_default(tool, "model")
        if tool == "codex":
            models = codex_models(find_binary("codex"))
            rows = []
            for index, m in enumerate(models):
                tags = (["recommended"] if index == 0 else []) + (["current"] if m["slug"] == current else [])
                efforts = ", ".join(r.get("effort", "") for r in m.get("supported_reasoning_levels") or [])
                rows.append([f"`{m['slug']}`", efforts, m.get("description", ""), ", ".join(tags)])
            out += ["### When Codex answers", ""]
            out += table(["Model", "Effort levels", "Description", "Note"], rows)
            out += ["", "From Codex's own model catalog, best first."]
        else:
            extra = [(v, d) for v, d in claude_extra_models() if v not in dict(CLAUDE_ALIASES)]
            rows = []
            for value, note in CLAUDE_ALIASES + extra:
                # peer's pick for a second opinion: strong, and quicker than Fable.
                tags = (["recommended"] if value == "opus" else []) + (["current"] if value == current else [])
                rows.append([f"`{value}`", note, ", ".join(tags)])
            out += ["### When Claude Code answers", ""]
            out += table(["Model", "Description", "Note"], rows)
            out += [
                "",
                f"Effort levels: {', '.join(claude_efforts(find_binary('claude')))}. Aliases always point at the "
                "newest version. Add `[1m]` for a 1M-token context, such as `opus[1m]`; full IDs such as "
                "`claude-opus-5-5` also work." + ("" if current else " Current: Claude Code's default."),
            ]
        out.append("")
    out.append(
        "Set one with `peer config codex model <id>` or `peer config claude model <alias>`, or for one call "
        "with `--model <id>`."
    )
    return "\n".join(out)


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
MCP_SERVER_RE = re.compile(r'^\[mcp_servers\.(?:"([^"]+)"|([A-Za-z0-9_-]+))\]\s*$', re.M)


def codex_lockdown(binary: str) -> list[str]:
    """Flags that switch off every Codex tool able to act outside the read-only sandbox.

    Feature names vary between Codex versions and an unknown name is an error, so only the
    features this Codex reports as enabled are turned off.
    """
    flags: list[str] = []
    try:
        out = subprocess.run(
            [binary, "features", "list"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=30,
            env=child_env(),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    enabled = {line.split()[0] for line in out.splitlines() if line.split() and line.split()[-1] == "true"}
    for feature in CODEX_RISKY_FEATURES:
        if feature in enabled:
            flags += ["--disable", feature]
    try:
        config = (CODEX_HOME / "config.toml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        config = ""
    for quoted, bare in MCP_SERVER_RE.findall(config):
        name = quoted or bare
        key = f'"{name}"' if quoted else name
        flags += ["-c", f"mcp_servers.{key}.enabled=false"]
    return flags


def codex_command(
    binary: str,
    cwd: str,
    out_file: Path,
    model: str | None,
    effort: str | None,
    ephemeral: bool,
    lockdown: list[str] | None = None,
) -> list[str]:
    cmd = [
        binary,
        "exec",
        "--sandbox",
        "read-only",
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
    cmd.append("-")
    return cmd


def claude_command(binary: str, model: str | None, effort: str | None, ephemeral: bool) -> list[str]:
    """Claude Code in print mode with only reading tools available.

    --tools limits which tools exist at all, so permission rules in the user's settings cannot
    re-enable Bash or editing. MCP servers, hooks and skills are switched off for the same reason.
    """
    tools = ",".join(CLAUDE_READ_ONLY_TOOLS)
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
        "default",
    ]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]
    if ephemeral:
        cmd.append("--no-session-persistence")
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

    def __init__(self, peer: str, cwd: str, model: Setting, effort: Setting, args):
        self.peer, self.cwd, self.model, self.effort, self.args = peer, cwd, model, effort, args
        binary = find_binary(peer)
        self.tmp = tempfile.TemporaryDirectory(prefix="peer-") if peer == "codex" else None
        self.out_file = Path(self.tmp.name) / "answer.md" if self.tmp else None
        if peer == "codex":
            lockdown = codex_lockdown(binary)
            self.cmd = codex_command(binary, cwd, self.out_file, model.value, effort.value, args.ephemeral, lockdown)
        else:
            self.cmd = claude_command(binary, model.value, effort.value, args.ephemeral)

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


# ---------------------------------------------------------------- command line


def resolve_limits(args, config: dict) -> Limits:
    """Everything by default; --trim (or the trim setting) starts from sensible limits; each
    --max-* flag, --budget and --no-tools then overrides one part."""
    limits = Limits.trimmed() if resolve_switch("trim", args.trim, config).value == "on" else Limits()
    if isinstance(config.get("budget"), int) and args.budget is None:
        limits.budget = config["budget"]
    for flag, name in (
        ("max_message", "message"),
        ("max_tool_input", "tool_input"),
        ("max_tool_output", "tool_output"),
        ("budget", "budget"),
    ):
        value = getattr(args, flag)
        if value is not None:
            setattr(limits, name, max(value, 0))
    if args.no_tools:
        limits.tools = False
    return limits


def mode_label(mode: str, host: str, peer: str) -> str:
    if mode == "ask":
        return f"{peer} answered the latest input itself; {host}'s reply was withheld"
    if mode == "review":
        return f"{peer} reviewed {host}'s last reply"
    return f"{peer} answered a new question"


def setting_text(name: str, requested: Setting, actual: str | None) -> str:
    if requested.value and actual and actual != requested.value:
        return f"{name}: {actual} (asked for {requested.value} via {requested.source})"
    if requested.value:
        return f"{name}: {requested.value} (from {requested.source})"
    if actual:
        return f"{name}: {actual} ({requested.source})"
    return f"{name}: {requested.source}"


def short_header(run: Run, request: Request, answer: Answer | None, assess: Setting) -> str:
    """The default header: the model and effort, plus anything the reader must not miss."""
    peer = TOOL_NAMES[run.peer]
    model = (answer and answer.model) or run.model.value or f"{peer} default"
    stats = [f"Model: {model}"]
    effort = (answer and answer.effort) or run.effort.value
    if effort:
        stats.append(f"Effort: {effort}")
    if assess.value == "on":
        stats.append("Assessment: on")
    lines = [f"## {peer}'s answer", "", " | ".join(stats)]
    if request.target_label not in ("User", "Question"):
        # The peer answered something the user did not type; say so even in short form.
        lines.append(f'Answering: {request.target_label}: "{" ".join(clip(request.target, 200).split())}"')
    return "\n".join(lines)


def header(
    argv: list[str], run: Run, request: Request, session: Session, mode: str, answer: Answer | None, assess: Setting
) -> str:
    host, peer = TOOL_NAMES[session.tool], TOOL_NAMES[run.peer]
    sent = request.turns_sent - request.turns_omitted
    turns = "1 turn" if sent == 1 else f"{sent} turns"
    if request.turns_omitted:
        turns = f"{sent} of {request.turns_sent} turns"
    context = f"{turns} from {host}, {len(request.prompt):,} characters sent"
    if not request.turns_sent:
        context = f"no history, {len(request.prompt):,} characters sent"
    if request.turns_omitted:
        context += " (the oldest turns after the first were left out to fit the budget)"
    context += f"; {request.limits.describe()}"
    settings = setting_text("Model", run.model, answer.model if answer else None)
    if run.effort.value or (answer and answer.effort):
        settings += " | " + setting_text("Effort", run.effort, answer.effort if answer else None)
    lines = [
        f"## {peer}'s answer",
        "",
        f"Peer command: {shlex.join(['peer', *argv])}",
        f"{peer} command: {run.display(len(request.prompt))}",
        settings,
        f"Mode: {mode_label(mode, host, peer)}",
        f'Answering: {request.target_label}: "{" ".join(clip(request.target, 300).split())}"',
        f"Context: {context}",
        f"Assessment: {assess.value} ({assess.source if assess.source == 'default' else 'from ' + assess.source})",
    ]
    return "\n".join(lines)


def parse_args(argv: list[str]):
    parser = argparse.ArgumentParser(
        prog="peer",
        description="Ask the other coding agent (Claude Code or Codex) to answer from the same conversation.",
    )
    parser.add_argument("--version", action="version", version=f"peer {__version__}")
    parser.add_argument("words", nargs="*", help='"review [focus]", or a question; empty answers your last message')
    parser.add_argument("--from", dest="source", choices=TOOL_NAMES, help="the tool you are in (default: detect)")
    parser.add_argument("--to", dest="target", choices=TOOL_NAMES, help="the tool to ask (default: the other one)")
    parser.add_argument("--session", help="a transcript path or session id instead of the current session")
    parser.add_argument("--last", action="store_true", help="use the newest session for this folder")
    parser.add_argument(
        "--current-turn",
        action="store_true",
        help="treat the latest message as the one to answer, not as the peer request",
    )
    parser.add_argument(
        "--trim",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="cut long content with sensible limits: messages {message}, tool input {tool_input}, "
        "tool output {tool_output}, summaries {summary}, whole transcript {budget} "
        "characters (default: off, everything is sent)".format(**TRIM_DEFAULTS),
    )
    parser.add_argument("--max-message", type=int, help="characters kept per message (0: no limit)")
    parser.add_argument("--max-tool-input", type=int, help="characters kept per tool call (0: no limit)")
    parser.add_argument("--max-tool-output", type=int, help="characters kept per tool result (0: no limit)")
    parser.add_argument(
        "--budget", type=int, help="most transcript characters; older middle turns are dropped past it (0: no limit)"
    )
    parser.add_argument("--no-tools", action="store_true", help="leave tool calls and results out")
    parser.add_argument(
        "--model", help="model for the peer (default: PEER_<TOOL>_MODEL, the config file, then the peer's own config)"
    )
    parser.add_argument(
        "--effort",
        help="reasoning effort for the peer (default: PEER_<TOOL>_EFFORT, the config file, then the peer's own config)",
    )
    parser.add_argument(
        "--assess",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="whether the asking tool adds its own assessment after the answer (default: off; "
        'PEER_ASSESS or "assess" in the config file change it)',
    )
    parser.add_argument(
        "--details",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="print the full header: commands, mode, the input answered, context size "
        '(default: off; PEER_DETAILS or "details" in the config file change it)',
    )
    parser.add_argument("--ephemeral", action="store_true", help="do not keep the peer session")
    parser.add_argument("--timeout", type=int, default=1800, help="seconds to wait (default: 1800)")
    parser.add_argument("--dry-run", action="store_true", help="print the header and prompt instead of sending it")
    # Intermixed, so options may follow the words: peer review --model gpt-x error handling
    return parser.parse_intermixed_args(argv)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = parse_args(argv)
    if os.environ.get("PEER_ACTIVE"):
        print("peer: refusing to run inside a peer session.", file=sys.stderr)
        return 2
    words = list(args.words)
    if words and words[0].lower() == "config":
        return config_command(words[1:])
    if words and words[0].lower() == "models":
        tools = [w for w in words[1:] if w in TOOL_NAMES] or ["codex", "claude"]
        try:
            print(list_models(tools))
        except PeerError as exc:
            print(f"peer: {exc}", file=sys.stderr)
            return 1
        return 0
    mode = "ask"
    if words and words[0].lower() == "review":
        mode, words = "review", words[1:]
    text = " ".join(words).strip()
    if mode == "ask" and text:
        mode = "question"
    try:
        outside = not (
            args.session
            or args.last
            or args.source
            or os.environ.get("CLAUDE_CODE_SESSION_ID")
            or os.environ.get("CODEX_THREAD_ID")
        )
        if outside and mode == "question":
            # A plain question from an ordinary terminal: no history to send.
            if not args.target:
                raise PeerError("Outside a session, pass --to claude or --to codex.")
            host = "claude" if args.target == "codex" else "codex"
            session, drop = Session(host, Path("."), os.getcwd(), []), False
        else:
            session, drop = resolve_session(args)
        peer = args.target or ("codex" if session.tool == "claude" else "claude")
        if args.current_turn:
            drop = False
        config = load_config()
        limits = resolve_limits(args, config)
        request = build_request(session, peer, mode, text, drop, limits)
        request.limits = limits
        cwd = session.cwd if session.cwd and os.path.isdir(session.cwd) else os.getcwd()
        assess = resolve_assess(args.assess, config)
        details = resolve_switch("details", args.details, config).value == "on"
        run = Run(
            peer,
            cwd,
            resolve_setting(peer, "model", args.model, config),
            resolve_setting(peer, "effort", args.effort, config),
            args,
        )
    except PeerError as exc:
        print(f"peer: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(header(argv, run, request, session, mode, None, assess))
        print(f"\n--- prompt ---\n{request.prompt}")
        run.close()
        return 0
    image_dir = tempfile.TemporaryDirectory(prefix="peer-images-") if IMAGE_REF_RE.search(request.prompt) else None
    try:
        prompt = save_images(request.prompt, session.images, Path(image_dir.name)) if image_dir else request.prompt
        answer = run(prompt)
    except PeerError as exc:
        print(
            header(argv, run, request, session, mode, None, assess)
            if details
            else short_header(run, request, None, assess),
            flush=True,
        )
        if not limits.budget and len(request.prompt) > TRIM_DEFAULTS["budget"]:
            exc = PeerError(
                f"{exc}\nThe full conversation was {len(request.prompt):,} characters. If it is too "
                f"long for the model, retry with --trim or --budget <chars>."
            )
        print(f"peer: {exc}", file=sys.stderr)
        return 1
    finally:
        if image_dir:
            image_dir.cleanup()

    print(
        header(argv, run, request, session, mode, answer, assess)
        if details
        else short_header(run, request, answer, assess)
    )
    print()
    print(answer.text)
    if answer.resume:
        print(f"\n---\nContinue with {TOOL_NAMES[peer]}: {answer.resume}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
