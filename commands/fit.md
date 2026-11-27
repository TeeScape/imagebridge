---
description: Trim and reframe an image to an exact size, free and offline
argument-hint: "<image> <width>[x<height>]"
---

Reframe an image: $ARGUMENTS

Free, local, instant. Call the `fit_image` MCP tool.

1. **This is why you rarely need the paid path for size.** Ask the free path for an icon and you
   get a composition — one measured example came back 512×341 with two thirds of the frame empty
   white. `fit_image` trims that border and centres what is left on the canvas you name.

2. **Say what it is.** Framing, not generation: the model did not compose at this size. For an
   icon or logo on a plain background that distinction does not matter. For a photograph it does,
   and `mode: "crop"` throws away the edges — tell the user which mode you used.

3. Options worth knowing: `background: "transparent"` (adds an alpha channel), `#rrggbb` for a
   flat colour, `margin` for breathing room, `trim: false` to keep the original framing and only
   change the canvas.

4. The tool reports how much border it cut and how far it scaled the content. Quote that rather
   than saying "resized".

5. `/imagebridge:exact` is still the answer when the *model itself* must compose at a given size
   or aspect — that one costs API money, so only reach for it when framing genuinely will not do.
