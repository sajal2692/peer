"""peer's settings: flags, PEER_* variables and the config file, and the peer config command."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import common
from .common import TOOL_NAMES, PeerError
from .request import Limits


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
    """An on/off option: the flag, then PEER_<KEY>, then the config file, then the default."""
    if flag is not None:
        return Setting("on" if flag else "off", f"--{key}" if flag else f"--no-{key}")
    env_name = f"PEER_{key.upper()}"
    env = os.environ.get(env_name, "").strip().lower()
    if env:
        return Setting("on" if env in ("1", "true", "yes", "on") else "off", env_name)
    if isinstance(config.get(key), bool):
        return Setting("on" if config[key] else "off", "peer config")
    return Setting("on" if SWITCH_DEFAULTS.get(key) else "off", "default")


# Switches that are on unless turned off. The rest are off unless turned on.
SWITCH_DEFAULTS = {"web": True}


def resolve_assess(flag: bool | None, config: dict) -> Setting:
    """Whether the asking tool should add its own assessment after the answer."""
    return resolve_switch("assess", flag, config)


def own_default(tool: str, key: str) -> str | None:
    """The model or effort the tool itself is configured with, for display only."""
    try:
        if tool == "codex":
            name = "model" if key == "model" else "model_reasoning_effort"
            text = (common.CODEX_HOME / "config.toml").read_text(encoding="utf-8")
            # Top-level keys only: stop at the first [table].
            top = text.split("\n[", 1)[0]
            match = re.search(rf'^\s*{name}\s*=\s*"([^"]+)"', top, re.M)
            return match.group(1) if match else None
        data = json.loads((common.CLAUDE_HOME / "settings.json").read_text(encoding="utf-8"))
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
  peer config web on|off              let the peer search and fetch web pages
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
    for key, label in (("assess", "Assessment"), ("details", "Full header"), ("web", "Web access")):
        setting = resolve_switch(key, None, config)
        source = "default" if setting.source == "default" else f"from {setting.source}"
        lines.append(f"{label}: {setting.value} ({source})")
    trim = resolve_switch("trim", None, config)
    lines.append(f"Trim: {trim.value} ({'default' if trim.source == 'default' else 'from ' + trim.source})")
    budget = config_budget(config)
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
        elif len(words) == 2 and words[0] in ("assess", "details", "trim", "web") and words[1].lower() in ("on", "off"):
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


def config_budget(config: dict) -> int:
    value = config.get("budget")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def resolve_limits(args, config: dict) -> Limits:
    """Everything by default; --trim (or the trim setting) starts from sensible limits; each
    --max-* flag, --budget and --no-tools then overrides one part."""
    limits = Limits.trimmed() if resolve_switch("trim", args.trim, config).value == "on" else Limits()
    if config_budget(config) and args.budget is None:
        limits.budget = config_budget(config)
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
