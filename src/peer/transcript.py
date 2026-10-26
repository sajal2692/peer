"""The conversation model (Item, Session) and helpers shared by both transcript readers."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path


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
    # The completion notification of a peer run started from this session.
    peer_run: bool = False
    # A call that ran peer on the user's behalf (not with --current-turn), so its turn was a
    # request for peer even when the user asked in plain words.
    peer_request: bool = False

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
    # The host's latest permission setting as its transcript records it: Claude Code's
    # permission mode (such as "auto" or "plan") or Codex's sandbox (such as "read-only").
    permissions: str | None = None


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


# A shell command that runs peer for this session; the skills always pass --from.
PEER_COMMAND_RE = re.compile(r"(?:^|[\s/;&|(])peer(?:\.py)?\s[^\n]*--from\b")


def is_peer_command(value) -> bool:
    command = value.get("command") if isinstance(value, dict) else value
    return isinstance(command, str) and bool(PEER_COMMAND_RE.search(command))


def is_peer_request(value) -> bool:
    """A peer run made because the user asked for one. With --current-turn the agent consulted
    peer about the user's message on its own, so that turn is still the user's work."""
    command = value.get("command") if isinstance(value, dict) else value
    return is_peer_command(command) and "--current-turn" not in command
