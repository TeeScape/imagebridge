---
description: Generate an image with exact dimensions via the OpenAI API (needs OPENAI_API_KEY)
argument-hint: "<description> [--size 1024x1024] [--quality high] [-n 2]"
---

Generate an image through the OpenAI **API**: $ARGUMENTS

Use this path only when the user needs control `/codex-bridge:image` cannot give — an exact size,
a quality level, several images at once, or a transparent background. It bills their OpenAI API
account per image, unlike the Codex path which runs on the ChatGPT plan.

1. Resolve `scripts/openai-image` by the same chain (`${CLAUDE_SKILL_DIR}/scripts/openai-image`, then
   `${CLAUDE_PLUGIN_ROOT}/skills/codex-bridge/scripts/openai-image`, then Glob). Invoke with `bash "<path>"`.

2. If it reports that `OPENAI_API_KEY` is unset, do not try to find a key anywhere on the machine.
   Tell the user to export it or put it in the plugin's `.env`, and offer `/codex-bridge:image` as the
   no-key alternative.

3. Useful flags: `--size`, `--quality auto|low|medium|high|xhigh|max`, `-n 1..10`, `--model`,
   `--background transparent|opaque|auto`, `--output-dir <existing dir>`, `--name <filename>`.
   Without `--output-dir` it saves into `~/Downloads/gpt-images/`.

4. Confirm the cost implication with the user before generating more than one image.

Show the result with `SendUserFile` once it is written.
