---
description: Check that the ImageBridge prerequisites are installed and signed in
allowed-tools: Bash(command:*), Bash(codex:*), Bash(openai:*), Bash(bash:*), Bash(ls:*)
---

# ImageBridge — environment check

Codex CLI: !`command -v codex >/dev/null 2>&1 && codex --version || echo "NOT INSTALLED"`

OpenAI CLI: !`command -v openai >/dev/null 2>&1 && openai --version || echo "not installed (only needed for /imagebridge:exact)"`

Codex auth: !`command -v codex >/dev/null 2>&1 && codex login status 2>&1 | head -1 || echo "n/a"`

Bundled scripts: !`ls -1 "${CLAUDE_PLUGIN_ROOT}/skills/imagebridge/scripts" 2>/dev/null || echo "COULD NOT RESOLVE \${CLAUDE_PLUGIN_ROOT}"`

OPENAI_API_KEY: !`[ -n "$OPENAI_API_KEY" ] && echo "set" || echo "not set (fine — only the exact-size path needs it)"`

python3 (for the MCP server): !`command -v python3 >/dev/null 2>&1 && python3 --version || echo "NOT FOUND — the MCP tools will not start; the scripts still work"`

MCP server self-test: !`printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python3 "${CLAUDE_PLUGIN_ROOT}/mcp/imagebridge-server.py" 2>/dev/null | head -c 200 || echo "could not run"`

Read the results above and tell the user, in their language, in a few lines:

- Whether they are ready to generate images. They are ready when `codex` is installed **and** auth says
  `Logged in using ChatGPT` (or an API-key login). Nothing else is required for `codex-image` / `codex-ask`.
- If `codex` is missing, give them exactly: `brew install --cask codex` (macOS/Linux) or
  `npm install -g @openai/codex`, then `codex login`.
- If auth is not signed in, tell them to run `codex login` themselves — never try to authenticate for them.
- If the bundled scripts could not be listed, the plugin files are not where expected: tell them to
  reinstall with `/plugin`.
- Mention the `openai` CLI and `OPENAI_API_KEY` only if they are missing **and** the user wants exact
  image dimensions; otherwise do not raise it.
- If the MCP self-test printed a JSON line listing tools, the MCP server works. If `python3` is
  missing, say the MCP tools are unavailable but the plugin still works through its commands.

Do not dump the raw output again — summarise it.
