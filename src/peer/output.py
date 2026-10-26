"""The header printed above the peer's answer."""

from __future__ import annotations

import shlex

from .common import TOOL_NAMES
from .request import Request, clip
from .runner import Answer, Run
from .settings import Setting
from .transcript import Session


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


def target_prefix(request: Request) -> str:
    return "Reviewing the reply to" if request.mode == "review" else "Answering"


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
    if run.write:
        stats.append("Access: write")
    if run.resume:
        stats.append(f"Continued session: {run.resume}")
    lines = [f"## {peer}'s answer", "", " | ".join(stats)]
    if request.target_label not in ("User", "Question"):
        # The peer answered something the user did not type; say so even in short form.
        target = " ".join(clip(request.target, 200).split())
        lines.append(f'{target_prefix(request)}: {request.target_label}: "{target}"')
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
    if request.continued:
        context += f" since {peer}'s last answer"
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
        f'{target_prefix(request)}: {request.target_label}: "{" ".join(clip(request.target, 300).split())}"',
        f"Context: {context}",
        f"Web access: {'on' if run.web else 'off'}",
        f"Assessment: {assess.value} ({assess.source if assess.source == 'default' else 'from ' + assess.source})",
        f"Access: {run.access.value} ({run.access.source})",
        f"{peer} session: {'continued ' + run.resume if run.resume else 'new'}",
    ]
    return "\n".join(lines)
