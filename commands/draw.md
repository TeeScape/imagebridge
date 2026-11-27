---
description: Generate an image with OpenAI Codex and save it into this project
argument-hint: "<what the image should show> [-o path/to/file.png] [-r reference.png]"
---

Generate an image with the Codex CLI: $ARGUMENTS

## How to run it

1. Resolve the script. It is `scripts/codex-image` inside this skill's folder. Use the first of these
   that exists, checking in order — the placeholders are only filled in in some contexts:
   - `${CLAUDE_SKILL_DIR}/scripts/codex-image`
   - `${CLAUDE_PLUGIN_ROOT}/skills/imagebridge/scripts/codex-image`
   - otherwise Glob for `**/skills/imagebridge/scripts/codex-image` under `~/.claude` and take the
     newest match.
   Always invoke it as `bash "<resolved path>"` — the executable bit does not survive every install
   method. Quote the path.

2. Work out the arguments from the request:
   - The description is everything the user asked for, as **one** quoted argument.
   - `-o <path>` when they named a file or you can infer a sensible one from the project
     (`assets/`, `public/images/`, `static/`); otherwise `-d .` for the current directory.
   - `-r <file>` for each reference image they pointed at.
   Run `bash "<script>" -h` if you need the full flag list.

3. Run it. It takes **45–60 seconds** and spends roughly 10–12k tokens of the user's ChatGPT plan.
   Say that you are starting before you run it, so the wait is expected.

4. It prints the saved file's absolute path on stdout, one per line. Afterwards:
   - Read the file to confirm it is really an image and really what was asked for.
   - Check its actual pixel size. The size cannot be set on this path at all — the image tool has
     no size argument — so if the user needs a specific one, say so plainly and point at
     `/imagebridge:exact`. Wording like "square icon" steers it but guarantees nothing.
   - For a transparent background use `-t`; that one IS supported here and needs no API key.
   - Show it to the user with `SendUserFile`.

If it fails, re-run once with `-v` to get the Codex transcript, then report what actually went wrong.
Do not retry more than once — each attempt costs the user real quota.
