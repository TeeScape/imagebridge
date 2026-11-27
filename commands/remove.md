---
description: Remove an object from a photo and fill the gap, using a self-hosted inpainting endpoint
argument-hint: "<image> <what to remove>"
---

Remove an object from an image: $ARGUMENTS

**This needs setup, and it is off until the user does it.** Unlike the other commands, object
removal does not run on the ChatGPT plan — it posts to an inpainting endpoint the user hosts
themselves, and ImageBridge ships **no default URL**: whose machine does the work is the user's
choice, not ours. If it is not configured, say so and point at the README section
"Object removal (optional)" rather than guessing a URL.

1. Decide what covers the object. The tool takes either
   - `regions`: a list of `[x, y, width, height]` boxes, or
   - `mask`: a PNG the size of the image, where **alpha 0 marks what to erase**.

2. Call the `remove_object` MCP tool. Cover the **whole** object, including its shadow and any
   thin parts — a box that clips an edge leaves a visible stub of it behind, and that is the most
   common way this disappoints.

3. This model **removes**; it does not invent. "Delete the person on the left" works well.
   "Put a hat on him" is not something it can do — use `/imagebridge:edit` for that.

4. The image is resampled to the model's 512×512 tile and composed back, so only the erased
   region changes; every pixel outside the mask is bit-for-bit the original.

5. Show the result with `SendUserFile` and be honest about residue if you see any.
