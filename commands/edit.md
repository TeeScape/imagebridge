---
description: Edit an existing image on the ChatGPT plan, keeping the rest unchanged
argument-hint: "<what to change> --ref original.png"
---

Edit an existing image: $ARGUMENTS

**This is free.** Codex edits images on the user's ChatGPT plan — no API key, no per-image
charge. A measured edit of a 1254×1254 icon came back 1254×1254 with the silhouette identical
and only the colour changed. Use it without hesitation for recolouring, adding or removing an
element, restyling, or extending a picture.

1. Resolve `scripts/codex-image` by the usual chain and run it with `-r`:

   ```bash
   bash "<script>" "change the folder to green, nothing else" -r assets/folder.png -o assets/folder.png
   ```

   `-r` is repeatable (up to 16 images). `--from-last N` edits the last N images of the Codex
   conversation instead, for images that have no file on disk.

2. Say what to change and what to keep. The model is editing, not redrawing, so "change only the
   sky, keep the building exactly as it is" works and is worth writing.

3. It takes **45–60 s** and spends the user's ChatGPT quota, like any generation. Say you are
   starting before you run it.

4. Read the result and show it with `SendUserFile`. If the edit went too far, say so rather than
   pretending; re-running costs another minute of their quota, so ask first.

## When the paid path is the only option

`/imagebridge:exact` and `edit_image` with `provider: "api"` bill the OpenAI API per call and
need `OPENAI_API_KEY`. There are exactly two reasons to go there:

- **a pixel mask** — confining the edit to an exact region
- **an exact output size** — the free path has no size argument at all

Anything else, including transparency, is free. Never send the user to the paid path without
saying it costs money and why the free one cannot do it.
