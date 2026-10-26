"""Turns a session into the prompt the peer receives."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from .common import MARKER, TOOL_NAMES, PeerError
from .transcript import SUMMARY_LABEL, Item, Session

# Limits that --trim applies, in characters. Without --trim nothing is cut.
TRIM_DEFAULTS = {"message": 20_000, "tool_input": 300, "tool_output": 500, "summary": 4_000, "budget": 400_000}


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
    # Only the turns since the peer's earlier answer were sent, into that same peer session.
    continued: bool = False


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


def is_peer_turn(turn: list[Item]) -> bool:
    """A turn that is about an earlier peer call rather than the user's work: one the user
    started with /peer or $peer, one where the user asked for peer in plain words and the
    agent ran it, or the notification that a peer run had finished."""
    first = turn[0]
    if first.peer_run:
        return True
    if first.role != "user":
        return False
    return bool(PEER_INVOCATION_RE.match(first.text)) or any(item.peer_request for item in turn)


def drop_earlier_peer_turns(turns: list[list[Item]]) -> list[list[Item]]:
    """Drops peer turns at the end, so /peer then /peer review both look at the reply the
    user asked about, not at the host relaying an earlier peer answer."""
    while turns and is_peer_turn(turns[-1]):
        turns = turns[:-1]
    return turns


# The "Continue with" line peer prints under every answer that kept its session.
RESUME_LINE_RE = re.compile(r"Continue with (Codex|Claude Code): (?:codex resume|claude --resume) ([\w-]+)")


def peer_session_ids(item: Item, peer: str) -> list[str]:
    if item.role not in ("result", "assistant"):
        return []
    return [sid for name, sid in RESUME_LINE_RE.findall(item.text) if name == TOOL_NAMES[peer]]


def last_peer_session(session: Session, peer: str) -> str | None:
    """The session id of the newest peer answer from this tool shown in the host session."""
    for item in reversed(session.items):
        ids = peer_session_ids(item, peer)
        if ids:
            return ids[-1]
    return None


def turns_since(turns: list[list[Item]], peer: str, session_id: str) -> list[list[Item]] | None:
    """The turns after the one that showed this peer session's answer, without turns that only
    ran or relayed peer. None when the session's answer is not in the transcript."""
    for index in range(len(turns) - 1, -1, -1):
        if any(session_id in peer_session_ids(item, peer) for item in turns[index]):
            return [t for t in turns[index + 1 :] if not is_peer_turn(t)]
    return None


def access_rule(write: bool) -> str:
    if write:
        return (
            "you may change files and run commands, including ones with side effects such as "
            "writing to a service, when the request calls for it; say exactly what you changed. "
            "Do not ask the user questions"
        )
    return (
        "read files to check facts, but do not modify anything, do not run commands with side "
        "effects, and do not ask the user questions"
    )


def build_request(
    session: Session,
    peer: str,
    mode: str,
    text: str,
    drop_current: bool,
    limits: Limits,
    write: bool = False,
    resume: str | None = None,
) -> Request:
    """The prompt for the peer. With resume, the peer continues that earlier session of its own,
    so when its last answer is in the transcript only the turns since then are sent."""
    host_name, peer_name = TOOL_NAMES[session.tool], TOOL_NAMES[peer]
    cwd = session.cwd or os.getcwd()
    turns = split_turns(session.items)
    if drop_current:
        turns = drop_from_last_typed(turns)
    since = turns_since(turns, peer, resume) if resume else None
    if since is not None:
        turns = since
    elif drop_current and mode in ("ask", "review"):
        turns = drop_earlier_peer_turns(turns)

    if since is not None:
        intro = (
            f"{MARKER}\n"
            f"This continues your earlier answer in this session. Since then the user and {host_name} "
            f"went on as in the transcript below (it holds only the new turns, and leaves out turns "
            f"that ran peer). You still share the working directory, {cwd}: {access_rule(write)}. "
        )
    else:
        intro = (
            f"{MARKER}\n"
            f"You are {peer_name}, asked for an independent answer. The transcript below is a session "
            f"between a user and {host_name}, another AI assistant, working in {cwd}. You share the "
            f"working directory: {access_rule(write)}. "
        )
    intro += (
        f"If something is ambiguous, "
        f"state your assumption and continue. Do not comment on this setup or the transcript format. "
        f'Headings other than "User" and "{host_name}" mark inputs the system injected, such as '
        f"task notifications or messages from other sessions; the user did not type them. Images "
        f"appear as [image: path]; open the file when it matters."
    )
    if session.instruction_files and since is None:
        intro += (
            f"\n{host_name} was also following these instruction files; read them when they bear on "
            f"the answer: " + ", ".join(session.instruction_files)
        )

    if mode == "ask":
        if since is not None and not since:
            raise PeerError(
                f'Nothing new since {peer_name}\'s last answer. Ask a question instead: peer "your question"'
            )
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
        return Request("ask", prompt, last.text, last.heading, len(turns), omitted, continued=since is not None)

    if mode == "review":
        if since is not None and not since:
            raise PeerError(f"No {host_name} reply since {peer_name}'s last answer to review.")
        if not turns:
            raise PeerError("No earlier reply to review.")
        last = turns[-1]
        if not any(i.role in ("assistant", "tool") for i in last):
            raise PeerError(f"The last turn has no {host_name} reply to review.")
        transcript, omitted = render_transcript(turns, host_name, limits)
        focus = f"\n\nThe user wants you to focus on: {text}" if text else ""
        prompt = (
            f"{intro}\n\n<transcript>\n{transcript}\n</transcript>\n\n"
            f"Review {host_name}'s latest reply, the last one in the transcript. Lead with your verdict. "
            f"Then say what is correct, what is wrong or missing, the risks, and what you would do "
            f"differently. Check its claims against the files where you can.{focus}"
        )
        return Request(
            "review", prompt, last[0].text, last[0].heading, len(turns), omitted, continued=since is not None
        )

    # A direct question, with whatever history exists as context.
    if not text:
        raise PeerError("Nothing to ask.")
    if not turns and since is not None:
        prompt = f"{intro}\n\nThe user now asks you directly:\n\n<question>\n{text}\n</question>"
        return Request("question", prompt, text, "Question", 0, 0, continued=True)
    if not turns:
        rule = access_rule(write)
        prompt = (
            f"{MARKER}\nYou are {peer_name}. Working directory: {cwd}. {rule[0].upper()}{rule[1:]}; "
            f"state assumptions instead.\n\n{text}"
        )
        return Request("question", prompt, text, "Question", 0, 0)
    transcript, omitted = render_transcript(turns, host_name, limits)
    prompt = (
        f"{intro}\n\n<transcript>\n{transcript}\n</transcript>\n\n"
        f"The user now asks you directly, with the session above as context:\n\n"
        f"<question>\n{text}\n</question>"
    )
    return Request("question", prompt, text, "Question", len(turns), omitted, continued=since is not None)
