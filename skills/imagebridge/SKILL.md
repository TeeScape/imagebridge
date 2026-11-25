---
name: imagebridge
description: Delegate work to the OpenAI Codex CLI from Claude Code. Use this to GENERATE IMAGES (icons, illustrations, mockups, textures, og-images, placeholder art, logos, hero images) since Claude cannot create raster images itself; to get a second opinion from a GPT model on code, a diff or a design; to run a self-contained coding task on the ChatGPT plan instead of spending Claude tokens; or to describe and compare existing images. Triggers on "generate an image", "make an icon", "create a picture", "draw me", "ask codex", "second opinion", "what would GPT say", "สร้างภาพ", "ทำไอคอน", "วาดรูป", "ถาม codex", "ให้ gpt ดู", "ให้ codex ทำ".
---

# ImageBridge

Hands work to the OpenAI Codex CLI and brings the result back. Three bundled scripts, all
non-interactive: they print their result on **stdout** and exit non-zero on failure.

## Finding the scripts

They live in `scripts/` inside this skill's own folder. Resolve the path with the first of these
that exists — the placeholders are only expanded in some contexts, so check in order:

1. `${CLAUDE_SKILL_DIR}/scripts/<name>`
2. `${CLAUDE_PLUGIN_ROOT}/skills/imagebridge/scripts/<name>`
3. Glob for `**/skills/imagebridge/scripts/<name>` under `~/.claude` and take the newest match.

**Always invoke as `bash "<resolved path>"`** — the executable bit does not survive every install
method — and quote the path. Below, `SCRIPTS` means the resolved directory.

If none of the three resolves, the plugin files are not where expected: tell the user to reinstall
`imagebridge` from `/plugin` and stop. Do not reimplement the scripts yourself.

## Prerequisites

`codex` must be installed and signed in. `/imagebridge:doctor` checks this. If it is missing:
`brew install --cask codex` or `npm install -g @openai/codex`, then **the user** runs `codex login`.
Never try to authenticate on their behalf.

## Generate an image

```bash
bash "$SCRIPTS/codex-image" "<description>" -o path/to/file.png   # exact path
bash "$SCRIPTS/codex-image" "<description>" -d assets/            # dir, name from description
bash "$SCRIPTS/codex-image" "<description>" -r reference.png -d out
bash "$SCRIPTS/codex-image" "<description>" -o a.png -a           # keep every image produced
```

stdout is the absolute path of the saved file, one per line:

```bash
IMG=$(bash "$SCRIPTS/codex-image" "flat vector icon of a blue folder" -o assets/folder.png)
```

- Runs Codex's built-in `$imagegen` skill (gpt-image-2 class) on the **signed-in ChatGPT plan**.
  No `OPENAI_API_KEY`, no per-image API charge.
- **~45–60 s and ~10–12k Codex tokens per image.** Real quota. Say you are starting before you run
  it, and never call it speculatively or in a loop without the user agreeing first.
- **The model picks the dimensions, not you** — 1254×1254 and 1536×1024 have both come out of the
  same script. There is no size, quality or count flag on this path. When exact dimensions matter,
  use `openai-image`, or resize afterwards. Check the real size with `file` before reporting.
- The script places the file itself, reading Codex's own `~/.codex/generated_images/<session>/`,
  so the path it prints is reliable — the model is never trusted with placement.
- Afterwards: read the file to confirm it is what was asked for, then show it with `SendUserFile`.

## Ask Codex to do something

```bash
bash "$SCRIPTS/codex-ask" "<prompt>"                                 # read-only, answer on stdout
bash "$SCRIPTS/codex-ask" -C ~/code/myapp "explain src/auth.ts"
bash "$SCRIPTS/codex-ask" -w -C ~/code/myapp "add --verbose to cli.py and run the tests"
bash "$SCRIPTS/codex-ask" --schema s.json -C . "extract the deps"    # structured JSON answer
bash "$SCRIPTS/codex-ask" -i screenshot.png "what is wrong in this UI?"
git diff | bash "$SCRIPTS/codex-ask" --stdin "review this diff for bugs"
bash "$SCRIPTS/codex-ask" --resume "now fix what you found"
```

| flag | meaning |
|---|---|
| *(default)* | sandbox `read-only` — Codex reads and runs commands, cannot write |
| `-w` | sandbox `workspace-write` — Codex may edit files in the working root |
| `--yolo` | no sandbox at all. Only with the user's explicit go-ahead |
| `-C DIR` | working root |
| `--add-dir DIR` | extra writable directory |
| `-m MODEL` | model override |
| `-i FILE` | attach an image to the prompt |
| `--schema FILE` | JSON Schema for a structured final answer |
| `--json` | raw JSONL event stream instead of the final message |
| `--stdin` | forward piped input as context (**required** — stdin is detached otherwise) |
| `--resume` | continue the most recent Codex session |
| `--search` | allow live web search |

Codex does not share this conversation. Write it a self-contained brief; it starts cold in the
directory you give it. Relay its answer rather than pasting it whole, and say it came from Codex.

## Exact image dimensions

`openai-image` wraps `openai images generate` (the official Go CLI). It needs `OPENAI_API_KEY` and
bills the OpenAI API per image, so reach for it only when `codex-image` cannot do the job:

```bash
bash "$SCRIPTS/openai-image" "flat icon of a blue folder" --name folder --output-dir ./assets \
  --size 1024x1024 --quality high --background transparent
```

Flags: `--size`, `--quality auto|low|medium|high|xhigh|max`, `-n 1..10`, `--model`, `--background`,
`--moderation`, `--format json`. Models include `gpt-image-2.6-sunburst` (default),
`gpt-image-2.5-sunburst/flare`, `gpt-image-2`, `gpt-image-1-mini`. Saves to `~/Downloads/gpt-images/`
unless `--output-dir` names an existing directory. Requires `brew install openai/tools/openai`.

If the key is unset, do **not** go looking for one on the machine. Say it is unset and offer the
Codex path instead.

## When to use this at all

- **Images** — Claude cannot produce raster images. This is the main reason.
- **A second opinion** — an independent GPT read on a diff, a design, a bug.
- **Moving cost off the Claude budget** — a self-contained, well-specified task.

Do not route ordinary work here. Claude is faster, already has the context, and every `codex` call is
a cold start that re-reads the repository.

## Gotchas, all verified

- Never pipe into `codex exec` without `--stdin` or `</dev/null`: Codex reads stdin whenever it is
  not a TTY and waits for EOF, and an agent's pipe never closes, so it hangs forever.
- `codex exec -i/--image` is variadic and will eat the prompt as a filename. The scripts put image
  flags last and insert `--` before the prompt. Keep that if you call `codex` directly.
- The scripts pass `--ignore-user-config` by default, because a user's `~/.codex/config.toml` can
  point `openai_base_url` at a local provider such as Ollama and silently route the call away from
  OpenAI. `--user-config` opts back in.
- `--full-auto` no longer exists in Codex; use `-w` (`--sandbox workspace-write`).
- Codex refuses to run outside a git repository without `--skip-git-repo-check`; the scripts always
  pass it.
- Quote the whole prompt as **one** argument. The scripts refuse several positionals rather than
  silently dropping them.
