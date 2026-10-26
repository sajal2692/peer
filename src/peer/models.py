"""The peer models command: which models each tool can answer with."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .common import PeerError
from .runner import child_env, find_binary
from .settings import load_config, own_default, resolve_setting

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
    return claude_efforts_from_help(out) or ["low", "medium", "high", "xhigh", "max"]


def claude_efforts_from_help(help_text: str) -> list[str]:
    match = re.search(r"--effort.*?\(([a-z, ]+)\)", help_text, re.S)
    return [e.strip() for e in match.group(1).split(",")] if match else []


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
