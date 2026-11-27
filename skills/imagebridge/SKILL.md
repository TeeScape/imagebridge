---
name: imagebridge
description: Delegate work to the OpenAI Codex CLI from Claude Code. Use this to GENERATE IMAGES (icons, illustrations, mockups, textures, og-images, placeholder art, logos, hero images) since Claude cannot create raster images itself; to get a second opinion from a GPT model on code, a diff or a design; to run a self-contained coding task on the ChatGPT plan instead of spending Claude tokens; or to describe and compare existing images. Triggers on "generate an image", "make an icon", "create a picture", "draw me", "ask codex", "second opinion", "what would GPT say", "สร้างภาพ", "ทำไอคอน", "วาดรูป", "ถาม codex", "ให้ gpt ดู", "ให้ codex ทำ".
---

# ImageBridge

Hands work to the OpenAI Codex CLI and brings the result back.

## Use the MCP tools when they are there

This plugin ships an MCP server. If this session has the tools `generate_image`, `ask_codex`
and `check_setup` (their names will carry an `imagebridge` prefix), **use them instead of the
shell**: same capability, typed arguments, no quoting traps. Everything below describes the
fallback path, for when the MCP server is not running — the server needs `python3` on PATH,
and the plugin still works fully without it.

| MCP tool | script it wraps |
|---|---|
| `generate_image` | `codex-image` |
| `ask_codex` | `codex-ask` |
| `check_setup` | — |

A long call is normal: `generate_image` takes 45-60 s. If the client gives up before the tool
returns, the user can raise `MCP_TOOL_TIMEOUT` (milliseconds) in their environment.

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
bash "$SCRIPTS/codex-image" "<description>" -o icon.png -t        # transparent background
bash "$SCRIPTS/codex-image" "<description>" --dry-run             # show the command, spend nothing
```

stdout is the absolute path of the saved file, one per line:

```bash
IMG=$(bash "$SCRIPTS/codex-image" "flat vector icon of a blue folder" -o assets/folder.png)
```

- Runs Codex's built-in `$imagegen` skill (gpt-image-2 class) on the **signed-in ChatGPT plan**.
  No `OPENAI_API_KEY`, no per-image API charge.
- **~45–60 s and ~10–12k Codex tokens per image.** Real quota. Say you are starting before you run
  it, and never call it speculatively or in a loop without the user agreeing first.
- **The size cannot be set here.** Codex's image tool takes five arguments and none is a size, so
  this is a property of the tool, not a missing flag. Wording steers it — "square icon" reliably
  comes back 1:1 — but never promise a size on this path. When the user needs one, use
  `openai-image`. Always check the real dimensions before reporting the result.
- **Transparency is free here**: pass `-t/--transparent`. `transparent_background` is one of the
  five real arguments, so it does not need the API key path. `--gif` likewise asks for animation.
- The script places the file itself, reading Codex's own `~/.codex/generated_images/<session>/`,
  so the path it prints is reliable — the model is never trusted with placement.
- Pass `return_image: true` to get a 384 px preview back as an image, so you can judge the result
  yourself in the same call instead of generating, then reading the file separately. Judge
  composition and colour from it, not fine detail.
- `max_dimension: 512` shrinks the saved file after generation. The generated size is still the
  model's choice; this is how you honour "make it 512px" without the paid path.
- Afterwards: show it with `SendUserFile` so the user sees it too.

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

## Editing an existing image — free

```bash
bash "$SCRIPTS/codex-image" "change the folder to green, nothing else" -r folder.png -o folder.png
bash "$SCRIPTS/codex-image" "make the sky purple" -r photo.png -o out/purple.png
bash "$SCRIPTS/codex-image" "remove the logo" --from-last 1 -d out
```

This runs on the ChatGPT plan with **no API key and no per-image charge**, and it is a real edit:
editing a 1254×1254 icon returned 1254×1254 with a silhouette IoU of 1.000 and only the colour
changed. Say what to change *and what to keep* — the model is editing, not redrawing.

`-r` takes up to 16 images; `--from-last N` (1–5) edits recent conversation images that have no
file on disk. The two cannot be combined.

**Only two things need the paid API path**: an exact output size (`openai-image`) and a pixel
mask (`openai-edit`). Transparency, references and GIFs are all free. Never send the user to the
paid path without saying it costs money and why the free one cannot do it.

## Photographs, and other formats

`inspect_image` and `remove_object` read 8-bit and 16-bit PNG directly, and take JPEGs and
interlaced PNGs by converting with `sips`, `magick`, `convert` or `ffmpeg` — whichever is already
installed. Do not ask the
user to convert a file by hand. If none is installed the error names them; relay that.

Results are written as PNG even when the input was a JPEG, so the picture is not compressed a
second time. Say so if the user expected a `.jpg` back.

## Checking what an edit actually did

`inspect_image` is free, offline and instant. Call it after `edit_image` and `remove_object`
rather than reporting what you asked for as though it were what happened.

With `compare_to` it returns `changed_pixels`, `changed_fraction`, `changed_box` and
`silhouette_iou`. The box is the evidence: it says where the change landed, so "change only the
sky" can be verified. `silhouette_iou` near 1.000 means the visible shape survived a recolour.
`changed_pixels: 0` means the edit did not happen — say so; it reads like success otherwise.

Quote the measurement to the user. A number they can check beats a sentence they cannot.

## Removing an object — self-hosted, off by default

The `remove_object` MCP tool erases something and fills the gap. It is the only tool here that
does not use the ChatGPT plan: it POSTs a 512×512 RGBA PNG (alpha 0 = hole) to an inpainting
endpoint the **user** hosts and gets the filled image back. There is deliberately no default URL.
If `IMAGEBRIDGE_INPAINT_URL` or `~/.imagebridge/config.json` is unset, the tool says so — point
the user at the README's "Object removal (optional)" section; never invent an endpoint.

Prefer `points`: one `[x, y]` well inside each object. The area grows out from it by colour, so
it follows the object's real shape — measured on one picture, a point left no trace of a leaf
where a hand-drawn box left 501 pixels of it, using a hole half the size. Seed the middle, never
the edge; an edge pixel is half background and either selects nothing or swallows the picture.
`tolerance` (default 40) and `grow` (default 2, which eats the antialiased rim) tune it.

Otherwise pass `regions` (`[x, y, w, h]` boxes) or a `mask` PNG the size of the image. Cover the
**whole** object, shadow and thin parts included: a box that clips an edge leaves a stub behind,
and that — not the model — is what usually makes a result look bad.

It fills a cropped window around each hole, so a small removal from a large photo stays sharp;
the result says how many windows were used and what resolution the fill was rendered at. Holes
near each other share a window, so cost is one request per cluster, not per rectangle.

It **removes**; it does not invent. "Delete the car" yes; "add a car" no, that is an edit.
Pixels outside the mask are bit-for-bit unchanged, because the fill is composed through the mask.

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
