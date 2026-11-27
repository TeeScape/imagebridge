---
description: Measure an image, or measure what an edit actually changed
argument-hint: "<image> [--against original.png]"
---

Measure an image: $ARGUMENTS

This costs nothing, needs no network and takes milliseconds. There is no reason to skip it.

1. Call the `inspect_image` MCP tool with `image`, and `compare_to` when you have the original.

2. **Use it after every edit.** `edit_image` and `remove_object` both return a path and a
   cheerful sentence; neither proves anything. The measurement does:

   | the ask was | the evidence is |
   |---|---|
   | "change only the sky" | `changed_box` lands in the sky and nowhere else |
   | "keep the shape, recolour it" | `silhouette_iou` ≈ 1.000 |
   | "remove the sign" | `changed_fraction` is small and the box covers the sign |
   | anything at all | `changed_pixels` is not 0 |

3. **Report the number, not the request.** "3.1% of pixels changed, all inside
   [x=265 y=50 w=123 h=100]" tells the user something. "I removed the leaf as requested" does
   not, and is what you would say whether or not it worked.

4. `tolerance` defaults to 8 so PNG re-encoding does not read as an edit. Drop it to 0 to see
   every last bit.

5. If `changed_pixels` is 0, say the edit did not happen. Do not describe an edit you cannot
   measure.
