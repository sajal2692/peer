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

__version__ = "0.2.0"
