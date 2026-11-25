---
description: Delegate a task to the OpenAI Codex CLI and bring its answer back
argument-hint: "<the task, e.g. review this diff for bugs / explain src/auth.ts>"
---

Delegate this to Codex: $ARGUMENTS

## How to run it

1. Resolve `scripts/codex-ask` the same way as the image command: try
   `${CLAUDE_SKILL_DIR}/scripts/codex-ask`, then
   `${CLAUDE_PLUGIN_ROOT}/skills/imagebridge/scripts/codex-ask`, then Glob for
   `**/skills/imagebridge/scripts/codex-ask` under `~/.claude`. Invoke it as `bash "<path>"`.

2. Choose the flags deliberately:
   - **Default to read-only.** Codex may read and run commands but not edit files.
   - `-w` only when the user actually asked Codex to change files. Say so before you do it.
   - `--yolo` removes the sandbox entirely — only with an explicit go-ahead in this conversation.
   - `-C <dir>` to give it the right repository as context.
   - `--schema <file>` when you want a structured answer you will parse.
   - `-i <image>` to attach a screenshot.
   - `--stdin` when you pipe something in. **Without it stdin is detached on purpose** — Codex blocks
     forever on an agent's pipe otherwise.

3. The whole prompt must be **one** quoted argument. Write Codex a self-contained brief: it does not
   share this conversation's context and starts cold in the directory you give it.

4. stdout is Codex's final message and nothing else. Relay what matters rather than pasting it whole,
   and say plainly that it came from Codex, not from you.

Remember this costs the user's ChatGPT quota and takes a cold start every time. If you already have the
context and could answer yourself, say so instead of spending the call.
