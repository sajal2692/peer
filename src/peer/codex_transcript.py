"""Reads a Codex rollout file into a Session."""

from __future__ import annotations

import json
from pathlib import Path

from .transcript import Item, Session, blocks_text, brief_input, is_peer_request, merge_assistant, read_jsonl, tool_item

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
        call = Item("tool", f"shell: {command}", peer_request=is_peer_request(str(command)))
        return [call, Item("result", output)]
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
            call = tool_item(str(p.get("name")), p.get("arguments") or p.get("input"))
            call.peer_request = is_peer_request(call.detail)
            items.append(call)
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
    sandbox = None
    for r in rows:
        if r.get("type") in ("session_meta", "turn_context"):
            cwd = (r.get("payload") or {}).get("cwd") or cwd
        if r.get("type") == "turn_context":
            policy = (r.get("payload") or {}).get("sandbox_policy")
            sandbox = (policy.get("type") if isinstance(policy, dict) else policy) or sandbox
    images: list = []
    # A thread started on an older Codex and continued on a newer one has both formats: the
    # old one up to the first item_completed event, the new one after it.
    first = next((i for i, r in enumerate(rows) if is_completed_item(r)), len(rows))
    items = codex_legacy(rows[:first])
    for row in rows[first:]:
        if is_completed_item(row):
            items += codex_item(row["payload"]["item"], images)
    session = Session("codex", path, cwd, merge_assistant(items), images)
    session.permissions = sandbox if isinstance(sandbox, str) else None
    return session
