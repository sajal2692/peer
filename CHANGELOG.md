# Changelog

## 0.1.0

First release.

- `/peer` in Claude Code and `$peer` in Codex ask the other tool to answer from the same conversation, and print its answer back in the tool you asked from.
- Three modes: answer your last message independently, `review` the last reply, or answer a new question with the conversation as context. A fresh session sends just the question.
- The whole thread is sent by default, untrimmed: messages, tool calls and results, messages sent while the agent was busy, attached files, pasted images (saved to temporary files the other tool can open), and compaction summaries. Inputs the system injected are labelled as what they are.
- `--trim` and the `--max-*`, `--budget` and `--no-tools` flags cut what is sent, all at once or one part at a time.
- `peer models` lists the models and effort levels each tool accepts. `peer config` saves defaults for the model, effort, trimming, the asking tool's assessment and the full header.
- The other tool runs headless and read-only, in the same folder, with session variables scrubbed and a guard against peer calling itself.
