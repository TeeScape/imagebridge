#!/usr/bin/env bash
# Standalone install, for people who do NOT want to use the Claude Code plugin:
# links the scripts into ~/bin, the skill into ~/.claude/skills, and registers
# the MCP server. If you installed imagebridge via /plugin, you do not need this.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

mkdir -p "$HOME/bin" "$HOME/.claude/skills"

for f in "$HERE"/skills/imagebridge/scripts/*; do
  n=$(basename "$f")
  ln -sfn "$f" "$HOME/bin/$n"
  # shellcheck disable=SC2088  # the tilde is being shown to a person, not used as a path
  echo "~/bin/$n -> $f"
done

ln -sfn "$HERE/skills/imagebridge" "$HOME/.claude/skills/imagebridge"
# shellcheck disable=SC2088  # likewise: this line is prose
echo "~/.claude/skills/imagebridge -> $HERE/skills/imagebridge"

# The MCP server is where inspect_image, remove_object and fit_image live.
# Linking only the scripts, as this did until 0.14.0, quietly installed about
# half of imagebridge and gave no sign which half was missing.
echo
if command -v claude >/dev/null 2>&1; then
  if claude mcp list 2>/dev/null | grep -q '^imagebridge\b'; then
    echo "ok: MCP server 'imagebridge' is already registered"
  elif claude mcp add imagebridge -- python3 "$HERE/mcp/imagebridge-server.py" >/dev/null 2>&1; then
    echo "registered MCP server 'imagebridge' -> $HERE/mcp/imagebridge-server.py"
  else
    echo "NOTE: could not register the MCP server automatically. Run:"
    echo "  claude mcp add imagebridge -- python3 \"$HERE/mcp/imagebridge-server.py\""
  fi
else
  echo "NOTE: the claude CLI is not on PATH, so the MCP server was not registered."
  echo "      Without it there is no inspect_image, remove_object or fit_image. Run later:"
  echo "  claude mcp add imagebridge -- python3 \"$HERE/mcp/imagebridge-server.py\""
fi

echo
case ":$PATH:" in
  *":$HOME/bin:"*) ;;
  *) echo "NOTE: \$HOME/bin is not on PATH. Add to ~/.zshrc:"
     echo '  export PATH="$HOME/bin:$PATH"' ;;
esac

for c in codex openai; do
  if command -v "$c" >/dev/null 2>&1; then echo "ok: $c $("$c" --version 2>&1 | head -1)"
  else echo "MISSING: $c"; fi
done
command -v codex >/dev/null 2>&1 && codex login status 2>&1 | head -1
