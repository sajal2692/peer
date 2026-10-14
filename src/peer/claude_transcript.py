"""Reads a Claude Code session transcript into a Session."""

from __future__ import annotations

import re
from pathlib import Path

from .transcript import (
    ANSWER_LABEL,
    SUMMARY_LABEL,
    Item,
    Session,
    blocks_text,
    brief_input,
    image_ref,
    is_peer_command,
    is_peer_request,
    merge_assistant,
    read_jsonl,
    tool_item,
)

REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
COMMAND_RE = re.compile(r"<command-name>(.*?)</command-name>", re.S)
COMMAND_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.S)


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


TOOL_USE_ID_RE = re.compile(r"<tool-use-id>\s*([\w-]+)\s*</tool-use-id>")


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
    peer_call_ids: set = set()  # Bash calls that ran peer
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
                    text = REMINDER_RE.sub("", text).strip()
                    label = injected_label(row, text)
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
            # Reminders can come first; the label is read from what follows them.
            text = REMINDER_RE.sub("", blocks_text(content, images)).strip()
            label = injected_label(row, text)
            if label:
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
                        call = tool_item(str(block.get("name")), block.get("input"))
                        if block.get("name") == "Bash" and is_peer_command(block.get("input")):
                            peer_call_ids.add(block.get("id"))
                            call.peer_request = is_peer_request(block.get("input"))
                        items.append(call)
    for item in items:
        match = TOOL_USE_ID_RE.search(item.text) if item.role == "input" else None
        if match and match.group(1) in peer_call_ids:
            item.peer_run = True
    return Session("claude", path, cwd, merge_assistant(items), images, instruction_files)
