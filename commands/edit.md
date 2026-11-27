---
description: Edit part of an existing image, keeping the rest (needs OPENAI_API_KEY)
argument-hint: "<what to change> --image file.png [--mask mask.png]"
---

Edit an existing image: $ARGUMENTS

This is the one thing generation cannot do — change part of a picture and keep the rest.

1. **Say the cost first.** This path goes through the OpenAI API, so it needs `OPENAI_API_KEY`
   and bills per call, unlike `/imagebridge:draw` which runs on the ChatGPT plan. If the user
   only wants a variation rather than a surgical edit, `/imagebridge:draw -r original.png` is
   free and is usually the better first try — offer that before spending money.

2. Resolve `scripts/openai-edit` by the usual chain (`${CLAUDE_SKILL_DIR}/scripts/openai-edit`,
   then `${CLAUDE_PLUGIN_ROOT}/skills/imagebridge/scripts/openai-edit`, then Glob), and invoke
   it as `bash "<path>"`. Or use the `edit_image` MCP tool if this session has it.

3. **About the mask.** `--mask` confines the edit. Its **fully transparent areas are the parts
   that get edited** — this is the opposite of what most people assume, so say it plainly if the
   user supplies one. It must be a PNG with an alpha channel and exactly the same width and
   height as the image. The script checks all of that locally and refuses before spending
   anything, so a rejection costs nothing; relay the reason rather than guessing.

   Without a mask the model edits the whole image according to the prompt.

4. Useful flags: `--size`, `--quality low|medium|high|xhigh|max`, `--background transparent|opaque|auto`,
   `--fidelity high|low` (how hard to preserve the original's features; omit for gpt-image-2),
   `--model`, `--format png|webp|jpeg`, `-o <path>`, `--dry-run`.

5. It prints the saved path. The original is never modified. Read the result and show it with
   `SendUserFile` before calling the job done.
