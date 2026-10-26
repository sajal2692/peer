"""The peer command line."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from . import __version__
from .common import TOOL_NAMES, PeerError
from .models import list_models
from .output import header, short_header
from .request import TRIM_DEFAULTS, build_request, last_peer_session
from .runner import Run
from .sessions import resolve_session
from .settings import (
    config_command,
    load_config,
    resolve_access,
    resolve_assess,
    resolve_limits,
    resolve_setting,
    resolve_switch,
)
from .transcript import IMAGE_REF_RE, Session, save_images


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
    parser.add_argument(
        "--web",
        action=argparse.BooleanOptionalAction,
        default=None,
        help='let the peer search and fetch web pages (default: on; --no-web, PEER_WEB=0 or "web" in the config '
        "file turn it off)",
    )
    parser.add_argument(
        "--write",
        dest="access",
        action="store_const",
        const="write",
        help="let the peer change files in the folder and run commands with side effects "
        "(default: match what this session may do)",
    )
    parser.add_argument(
        "--read-only",
        dest="access",
        action="store_const",
        const="read-only",
        help="keep the peer read-only, whatever this session may do",
    )
    parser.add_argument(
        "--continue",
        dest="cont",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="continue the peer session from the last peer answer in this conversation instead of starting a "
        'new one (default: on; --no-continue, PEER_CONTINUE=0 or "continue" in the config file turn it off)',
    )
    parser.add_argument("--resume", metavar="ID", help="continue this peer session id")
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
        resume = args.resume
        cont = resolve_switch("continue", args.cont, config)
        if not resume and cont.value == "on":
            resume = last_peer_session(session, peer)
            if not resume and args.cont:
                print(
                    f"peer: no earlier {TOOL_NAMES[peer]} answer in this conversation; starting a new session.",
                    file=sys.stderr,
                )
        if resume and args.ephemeral:
            raise PeerError("--ephemeral starts a session that is not kept, so it cannot continue one.")
        access = resolve_access(args.access, config, session.tool, session.permissions)
        write = access.value == "write"
        request = build_request(session, peer, mode, text, drop, limits, write, resume)
        request.limits = limits
        cwd = session.cwd if session.cwd and os.path.isdir(session.cwd) else os.getcwd()
        assess = resolve_assess(args.assess, config)
        details = resolve_switch("details", args.details, config).value == "on"
        web = resolve_switch("web", args.web, config).value == "on"
        run = Run(
            peer,
            cwd,
            resolve_setting(peer, "model", args.model, config),
            resolve_setting(peer, "effort", args.effort, config),
            args,
            web,
            write,
            resume,
            access,
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
