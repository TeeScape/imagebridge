#!/usr/bin/env bash
# Standalone install, for people who do NOT want to use the Claude Code plugin:
# links the scripts into ~/bin and the skill into ~/.claude/skills.
# If you installed imagebridge via /plugin, you do not need this.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

mkdir -p "$HOME/bin" "$HOME/.claude/skills"

for f in "$HERE"/skills/imagebridge/scripts/*; do
  n=$(basename "$f")
  ln -sfn "$f" "$HOME/bin/$n"
  echo "~/bin/$n -> $f"
done

ln -sfn "$HERE/skills/imagebridge" "$HOME/.claude/skills/imagebridge"
echo "~/.claude/skills/imagebridge -> $HERE/skills/imagebridge"

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
