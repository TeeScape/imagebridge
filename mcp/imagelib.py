"""
PNG decode, resize and encode using nothing but the Python standard library.

ImageBridge ships no dependencies, which until now meant it could not look at
the images it produced: it could read a PNG header for the dimensions, but not
a pixel. That blocked two things people kept wanting — handing Claude a preview
of what was just generated, and saving an image at a size the caller asked for.

The resampler is a port of the bilinear `resize()` in the analogue photo
editor, which is plain arithmetic with no canvas and no imaging library behind
it. Everything else here is zlib plus the PNG spec.

Scope on purpose: 8-bit, non-interlaced, colour types 0, 2, 4 and 6. That is
what the image models emit. Anything else raises Unsupported, and the caller is
expected to carry on without a preview rather than fail the whole job.
"""

import base64
import struct
import zlib

MAGIC = bytes([137, 80, 78, 71, 13, 10, 26, 10])
CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4}


class Unsupported(Exception):
    """The file is a PNG this module deliberately does not handle."""


def _unfilter(raw, width, height, channels, sample=1):
    """Undo the per-scanline filters. See PNG spec §9.2.

    `sample` is bytes per sample: 2 for a 16-bit image. Filtering works on the
    byte before the corresponding byte of the previous pixel, so the only thing
    that changes is how far back that is.
    """
    channels = channels * sample
    stride = width * channels
    out = bytearray(stride * height)
    prev = bytearray(stride)
    pos = 0
    for y in range(height):
        ftype = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        if ftype == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 255
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif ftype == 3:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
        elif ftype == 4:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 255
        elif ftype != 0:
            raise Unsupported("unknown scanline filter %d" % ftype)
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return bytes(out)


def read_png(path):
    """-> (width, height, channels, pixel bytes). Raises Unsupported."""
    with open(path, "rb") as fh:
        return read_png_bytes(fh.read())


def read_png_bytes(blob):
    """Same, for a PNG already in memory (an HTTP response, say)."""
    if blob[:8] != MAGIC:
        raise Unsupported("not a PNG")
    pos, idat, width, height, channels = 8, bytearray(), None, None, None
    while pos + 8 <= len(blob):
        length = struct.unpack(">I", blob[pos:pos + 4])[0]
        ctype = blob[pos + 4:pos + 8]
        data = blob[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            width, height = struct.unpack(">II", data[:8])
            depth, colour, _, _, interlace = data[8], data[9], data[10], data[11], data[12]
            if depth not in (8, 16):
                raise Unsupported("bit depth %d (only 8 and 16 are handled)" % depth)
            if interlace:
                raise Unsupported("interlaced PNG")
            if colour not in CHANNELS:
                raise Unsupported("colour type %d" % colour)
            channels = CHANNELS[colour]
        elif ctype == b"IDAT":
            idat += data
        elif ctype == b"IEND":
            break
        pos += 12 + length
    if width is None or not idat:
        raise Unsupported("no image data")
    sample = depth // 8
    px = _unfilter(zlib.decompress(bytes(idat)), width, height, channels, sample)
    if sample == 2:
        # Keep the high byte of each sample. Everything downstream - the
        # resampler, the mask, the comparison - is 8-bit, and a 16-bit source is
        # worth reading rather than refusing over precision nobody asked for.
        px = px[::2]
    return width, height, channels, px


def resize(src, sw, sh, dw, dh, channels):
    """Bilinear resample, ported from analogue's inpaint worker.

    Samples at pixel centres ((x + 0.5) * scale - 0.5), which is why a 2x
    reduction averages the right pairs instead of drifting half a pixel.
    """
    out = bytearray(dw * dh * channels)
    kx, ky = sw / dw, sh / dh
    for y in range(dh):
        fy = (y + .5) * ky - .5
        fy = 0.0 if fy < 0 else (sh - 1 if fy > sh - 1 else fy)
        y0 = int(fy)
        y1 = y0 + 1 if y0 + 1 < sh else y0
        ty = fy - y0
        row0, row1 = y0 * sw, y1 * sw
        for x in range(dw):
            fx = (x + .5) * kx - .5
            fx = 0.0 if fx < 0 else (sw - 1 if fx > sw - 1 else fx)
            x0 = int(fx)
            x1 = x0 + 1 if x0 + 1 < sw else x0
            tx = fx - x0
            i00 = (row0 + x0) * channels
            i01 = (row0 + x1) * channels
            i10 = (row1 + x0) * channels
            i11 = (row1 + x1) * channels
            o = (y * dw + x) * channels
            for c in range(channels):
                a = src[i00 + c]
                b = src[i01 + c]
                d = src[i10 + c]
                e = src[i11 + c]
                top = a + (b - a) * tx
                bot = d + (e - d) * tx
                out[o + c] = int(top + (bot - top) * ty + 0.5)
    return bytes(out)


def write_png(path_or_none, width, height, channels, pixels):
    """Encode and either write it or return the bytes."""
    colour = {1: 0, 2: 4, 3: 2, 4: 6}[channels]
    stride = width * channels
    raw = bytearray()
    for y in range(height):
        raw.append(0)                      # filter 0: no prediction, smallest code
        raw += pixels[y * stride:(y + 1) * stride]

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))

    blob = (MAGIC
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, colour, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + chunk(b"IEND", b""))
    if path_or_none is None:
        return blob
    with open(path_or_none, "wb") as fh:
        fh.write(blob)
    return blob


def fit(width, height, max_side):
    """New size with the aspect ratio kept, never enlarging."""
    longest = max(width, height)
    if longest <= max_side:
        return width, height
    scale = max_side / float(longest)
    return max(1, int(width * scale + 0.5)), max(1, int(height * scale + 0.5))


def resize_file(path, max_side):
    """Shrink a PNG in place. -> (old_w, old_h, new_w, new_h); no-op if it fits."""
    w, h, ch, px = read_png(path)
    nw, nh = fit(w, h, max_side)
    if (nw, nh) == (w, h):
        return w, h, w, h
    write_png(path, nw, nh, ch, resize(px, w, h, nw, nh, ch))
    return w, h, nw, nh


def preview_base64(path, max_side=512):
    """A small PNG of the image, base64 for an MCP image block.

    Kept deliberately small: the point is for a model to see whether the result
    is roughly right, not to ship the asset through the conversation.
    """
    w, h, ch, px = read_png(path)
    nw, nh = fit(w, h, max_side)
    if (nw, nh) != (w, h):
        px = resize(px, w, h, nw, nh, ch)
    return base64.b64encode(write_png(None, nw, nh, ch, px)).decode("ascii"), nw, nh


# --------------------------------------------------------------------- masks

def rect_mask(width, height, regions):
    """A keep/remove map: 1 = keep, 0 = remove. regions are [x, y, w, h]."""
    mask = bytearray([1]) * (width * height)
    for r in regions:
        x, y, w, h = (int(v) for v in r)
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(width, x + w), min(height, y + h)
        if x1 <= x0 or y1 <= y0:
            raise Unsupported("region %r falls outside the %dx%d image" % (r, width, height))
        for yy in range(y0, y1):
            row = yy * width
            for xx in range(x0, x1):
                mask[row + xx] = 0
    if 0 not in mask:
        raise Unsupported("the regions cover nothing")
    return bytes(mask)


def mask_from_png(path, width, height):
    """1 = keep, 0 = remove, taken from a PNG's transparent areas.

    Same convention as the OpenAI edit endpoint: FULLY TRANSPARENT means edit.
    """
    mw, mh, mch, mpx = read_png(path)
    if (mw, mh) != (width, height):
        raise Unsupported("the mask is %dx%d but the image is %dx%d" % (mw, mh, width, height))
    if mch not in (2, 4):
        raise Unsupported("the mask has no alpha channel (it marks the area to edit)")
    alpha_at = mch - 1
    mask = bytearray(width * height)
    for i in range(width * height):
        mask[i] = 0 if mpx[i * mch + alpha_at] < 128 else 1
    if 0 not in mask:
        raise Unsupported("the mask is fully opaque, so it marks nothing to edit")
    return bytes(mask)


def to_rgba_with_hole(pixels, width, height, channels, mask):
    """RGBA where the holes are transparent black — the shape the LaMa API wants."""
    out = bytearray(width * height * 4)
    for i in range(width * height):
        keep = mask[i]
        src = i * channels
        if channels >= 3:
            r, g, b = pixels[src], pixels[src + 1], pixels[src + 2]
        else:
            r = g = b = pixels[src]
        o = i * 4
        out[o] = r if keep else 0
        out[o + 1] = g if keep else 0
        out[o + 2] = b if keep else 0
        out[o + 3] = 255 if keep else 0
    return bytes(out)


def compose(filled, fw, fh, original, width, height, channels, mask):
    """Keep the original everywhere the mask kept, take the fill in the holes.

    A port of analogue's lamaCompose: the model returns a whole square, but only
    what was inside the hole may be used, or the untouched part of the picture
    would come back softened by a round trip through 512x512.
    """
    up = resize(filled, fw, fh, width, height, 3) if (fw, fh) != (width, height) else filled
    out = bytearray(width * height * channels)
    for i in range(width * height):
        o, s = i * channels, i * 3
        if mask[i]:
            src = i * channels
            for c in range(channels):
                out[o + c] = original[src + c]
        else:
            if channels >= 3:
                out[o], out[o + 1], out[o + 2] = up[s], up[s + 1], up[s + 2]
                if channels == 4:
                    out[o + 3] = 255
            else:
                out[o] = up[s]
                if channels == 2:
                    out[o + 1] = 255
    return bytes(out)


def _rgb(pixels, i, channels):
    """The RGB triple of pixel i, whatever the colour type was."""
    if channels <= 2:                       # grey, grey+alpha
        g = pixels[i * channels]
        return g, g, g
    o = i * channels
    return pixels[o], pixels[o + 1], pixels[o + 2]


def _alpha(pixels, i, channels):
    return 255 if channels in (1, 3) else pixels[i * channels + channels - 1]


def stats(width, height, channels, pixels, top=4):
    """What is actually in this image: mean and dominant colour over the
    pixels you can see, plus how much of it is transparent.

    The mean ignores transparent pixels on purpose. Averaging in the empty
    space around an icon drags every colour towards whatever the file happens
    to store under alpha 0, and reports a tint that nobody can see.
    """
    n = width * height
    tot = [0, 0, 0]
    opaque = 0
    buckets = {}
    for i in range(n):
        if _alpha(pixels, i, channels) == 0:
            continue
        opaque += 1
        r, g, b = _rgb(pixels, i, channels)
        tot[0] += r
        tot[1] += g
        tot[2] += b
        key = (r >> 4, g >> 4, b >> 4)      # 4096 buckets: enough to name a colour
        buckets[key] = buckets.get(key, 0) + 1
    mean = tuple(t // opaque for t in tot) if opaque else None
    ranked = sorted(buckets.items(), key=lambda kv: -kv[1])[:top]
    return {
        "width": width,
        "height": height,
        "channels": channels,
        "has_alpha": channels in (2, 4),
        "transparent_fraction": round((n - opaque) / n, 4) if n else 0.0,
        "mean_colour": mean,
        "dominant_colours": [
            {"rgb": [(k[0] << 4) + 8, (k[1] << 4) + 8, (k[2] << 4) + 8],
             "fraction": round(c / opaque, 4)}
            for k, c in ranked
        ] if opaque else [],
    }


def diff(a, b, tolerance=8):
    """Measure what changed between two images of the same size.

    Each is (width, height, channels, pixels), as read_png returns. A channel
    difference above `tolerance` counts as changed, so PNG re-encoding and the
    model's own dithering do not read as an edit.

    The bounding box is the point of this: it says WHERE the change landed, so
    "change only the sky" can be checked instead of believed.
    """
    aw, ah, ac, ap = a
    bw, bh, bc, bp = b
    if (aw, ah) != (bw, bh):
        raise Unsupported("the images are %dx%d and %dx%d, so they cannot be compared"
                          % (aw, ah, bw, bh))
    n = aw * ah
    changed = 0
    x0 = y0 = None
    x1 = y1 = -1
    inter = union = 0
    for i in range(n):
        aa, ba = _alpha(ap, i, ac), _alpha(bp, i, bc)
        if aa or ba:
            union += 1
            if aa and ba:
                inter += 1
        ar, ag, ab = _rgb(ap, i, ac)
        br, bg, bb = _rgb(bp, i, bc)
        if (abs(ar - br) > tolerance or abs(ag - bg) > tolerance
                or abs(ab - bb) > tolerance or abs(aa - ba) > tolerance):
            changed += 1
            x, y = i % aw, i // aw
            if x0 is None or x < x0:
                x0 = x
            if y0 is None or y < y0:
                y0 = y
            if x > x1:
                x1 = x
            if y > y1:
                y1 = y
    return {
        "width": aw,
        "height": ah,
        "changed_pixels": changed,
        "changed_fraction": round(changed / n, 4) if n else 0.0,
        "changed_box": [x0, y0, x1 - x0 + 1, y1 - y0 + 1] if changed else None,
        # 1.0 means the visible shape is identical; only meaningful with alpha.
        "silhouette_iou": round(inter / union, 4) if union else None,
        "tolerance": tolerance,
    }


def crop(pixels, width, height, channels, box):
    """The (w, h, pixels) of a rectangle. box is [x, y, w, h], already in range."""
    x, y, w, h = box
    out = bytearray(w * h * channels)
    for yy in range(h):
        src = ((y + yy) * width + x) * channels
        dst = yy * w * channels
        out[dst:dst + w * channels] = pixels[src:src + w * channels]
    return w, h, bytes(out)


def crop_mask(mask, width, box):
    x, y, w, h = box
    out = bytearray(w * h)
    for yy in range(h):
        src = (y + yy) * width + x
        out[yy * w:(yy + 1) * w] = mask[src:src + w]
    return bytes(out)


def paste(dest, width, channels, box, patch):
    """Write a patch back into dest at box. dest is a bytearray, modified in place."""
    x, y, w, h = box
    for yy in range(h):
        dst = ((y + yy) * width + x) * channels
        src = yy * w * channels
        dest[dst:dst + w * channels] = patch[src:src + w * channels]


def windows(mask, width, height, tile=512, margin=0.6):
    """Rectangles to inpaint one at a time, instead of the whole picture.

    The model works on a fixed `tile` square, so feeding it a 4000px photo
    means the fill is rendered at an eighth of the picture's resolution and
    comes back visibly soft against its sharp surroundings. Cropping a window
    around each hole and filling that instead keeps the fill at full detail -
    and a window smaller than the tile loses nothing at all.

    `margin` is how much context to add around a hole, as a fraction of its
    size: LaMa needs to see what it is continuing. Windows that overlap are
    merged, so holes close together cost one request, and a hole large enough
    to need the whole picture degrades to exactly the old behaviour.
    """
    # Connected holes, found with a flood fill over 4-neighbours.
    seen = bytearray(width * height)
    boxes = []
    for start in range(width * height):
        if mask[start] or seen[start]:
            continue
        stack, x0, y0, x1, y1 = [start], start % width, start // width, start % width, start // width
        seen[start] = 1
        while stack:
            i = stack.pop()
            x, y = i % width, i // width
            x0, y0, x1, y1 = min(x0, x), min(y0, y), max(x1, x), max(y1, y)
            for j, ok in ((i - 1, x > 0), (i + 1, x < width - 1),
                          (i - width, y > 0), (i + width, y < height - 1)):
                if ok and not mask[j] and not seen[j]:
                    seen[j] = 1
                    stack.append(j)
        boxes.append([x0, y0, x1, y1])

    def grown(b):
        x0, y0, x1, y1 = b
        w, h = x1 - x0 + 1, y1 - y0 + 1
        pad = int(max(w, h) * margin) + 1
        side = max(w, h) + 2 * pad
        # A square window keeps the aspect ratio through the square tile, so
        # nothing is stretched on the way in and squashed on the way out.
        side = min(side, width, height)
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        wx = max(0, min(width - side, cx - side // 2))
        wy = max(0, min(height - side, cy - side // 2))
        return [wx, wy, wx + side - 1, wy + side - 1]

    wins = [grown(b) for b in boxes]
    merged = True
    while merged:
        merged = False
        for i in range(len(wins)):
            for j in range(i + 1, len(wins)):
                a, b = wins[i], wins[j]
                if a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]:
                    side = max(max(a[2], b[2]) - min(a[0], b[0]) + 1,
                               max(a[3], b[3]) - min(a[1], b[1]) + 1)
                    side = min(side, width, height)
                    cx = (min(a[0], b[0]) + max(a[2], b[2])) // 2
                    cy = (min(a[1], b[1]) + max(a[3], b[3])) // 2
                    wx = max(0, min(width - side, cx - side // 2))
                    wy = max(0, min(height - side, cy - side // 2))
                    wins[i] = [wx, wy, wx + side - 1, wy + side - 1]
                    wins.pop(j)
                    merged = True
                    break
            if merged:
                break
    return [[x0, y0, x1 - x0 + 1, y1 - y0 + 1] for x0, y0, x1, y1 in wins]


SELECT_LIMIT = 0.4


def select(pixels, width, height, channels, seeds, tolerance=40, grow=2):
    """A mask (1 keep, 0 remove) of the areas a seed point sits in.

    Drawing a rectangle round an object is the main reason object removal
    disappoints: clip one edge and a stub of the thing comes back. Growing the
    region out from a point inside it instead follows the object's actual
    shape, so the hole is both tighter and complete.

    `grow` dilates the result along its boundary. Without it an antialiased
    edge survives as a faint halo of the removed object - a selection that
    caught 98% of a leaf still left a visible green fringe.

    Seed INSIDE the object, not on its edge: an edge pixel is a blend of the
    object and its background, so it either selects almost nothing or bridges
    into the background and swallows the picture. A selection past
    SELECT_LIMIT of the image is treated as that mistake and refused.
    """
    keep = bytearray([1]) * (width * height)
    frontier = set()
    for seed in seeds:
        sx, sy = (int(v) for v in seed)
        if not (0 <= sx < width and 0 <= sy < height):
            raise Unsupported("the point (%d, %d) is outside the %dx%d image"
                              % (sx, sy, width, height))
        start = sy * width + sx
        sr, sg, sb = _rgb(pixels, start, channels)
        limit = tolerance * 3
        stack = [start]
        keep[start] = 0
        while stack:
            i = stack.pop()
            x, y = i % width, i // width
            for j, ok in ((i - 1, x > 0), (i + 1, x < width - 1),
                          (i - width, y > 0), (i + width, y < height - 1)):
                if not ok or not keep[j]:
                    continue
                r, g, b = _rgb(pixels, j, channels)
                if abs(r - sr) + abs(g - sg) + abs(b - sb) <= limit:
                    keep[j] = 0
                    stack.append(j)
                else:
                    frontier.add(j)

    # Dilate along the boundary only; walking the whole image per step would
    # cost as much as the fill itself.
    for _ in range(max(0, int(grow))):
        nxt = set()
        for i in frontier:
            if not keep[i]:
                continue
            keep[i] = 0
            x, y = i % width, i // width
            for j, ok in ((i - 1, x > 0), (i + 1, x < width - 1),
                          (i - width, y > 0), (i + width, y < height - 1)):
                if ok and keep[j]:
                    nxt.add(j)
        frontier = nxt

    chosen = len(keep) - sum(keep)
    if not chosen:
        raise Unsupported("the point selected nothing")
    if chosen > SELECT_LIMIT * width * height:
        raise Unsupported(
            "the selection spread over %.0f%% of the picture, which means it leaked out of the "
            "object into the background. Seed a point well inside the thing to remove, not on "
            "its edge, or lower the tolerance." % (100.0 * chosen / (width * height)))
    return bytes(keep)


def content_box(pixels, width, height, channels, tolerance=12):
    """The rectangle holding everything that is not the flat border.

    An image model asked for an icon returns a composition, not an icon: the
    one measured while this was written was 512x341 with two thirds of the
    frame empty white. The border colour is taken from the corners rather
    than assumed to be white, and a fully transparent pixel counts as border
    whatever is stored under it.
    """
    if channels in (2, 4):
        edges = [i for i in range(width * height) if _alpha(pixels, i, channels) > 0]
        if edges:
            xs = [i % width for i in edges]
            ys = [i // width for i in edges]
            if len(edges) < width * height:
                return [min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1]

    corners = [0, width - 1, (height - 1) * width, height * width - 1]
    ref = [sum(_rgb(pixels, i, channels)[k] for i in corners) // 4 for k in range(3)]
    limit = tolerance * 3
    x0, y0, x1, y1 = width, height, -1, -1
    for i in range(width * height):
        r, g, b = _rgb(pixels, i, channels)
        if abs(r - ref[0]) + abs(g - ref[1]) + abs(b - ref[2]) > limit:
            x, y = i % width, i // width
            if x < x0:
                x0 = x
            if y < y0:
                y0 = y
            if x > x1:
                x1 = x
            if y > y1:
                y1 = y
    if x1 < 0:
        raise Unsupported("the whole image is one flat colour, so there is nothing to trim to")
    return [x0, y0, x1 - x0 + 1, y1 - y0 + 1]


def fit_canvas(pixels, width, height, channels, box, out_w, out_h, mode="pad",
               background=None, margin=0):
    """Put `box` of the image onto an out_w x out_h canvas.

    mode "pad" fits the whole thing inside and fills the rest with
    `background`; mode "crop" fills the canvas and loses whatever falls
    outside it. Neither invents detail: this is framing, and the caller is
    expected to say so rather than imply the model produced this size.
    """
    bx, by, bw, bh = box
    _, _, sub = crop(pixels, width, height, channels, box)
    inner_w, inner_h = max(1, out_w - 2 * margin), max(1, out_h - 2 * margin)
    sx, sy = inner_w / bw, inner_h / bh
    scale = min(sx, sy) if mode == "pad" else max(sx, sy)
    nw, nh = max(1, round(bw * scale)), max(1, round(bh * scale))
    scaled = resize(sub, bw, bh, nw, nh, channels)

    if background is None:
        background = tuple(_rgb(pixels, 0, channels))
    if background == "transparent":
        if channels not in (2, 4):
            channels_out = 4
            scaled = b"".join(bytes(_rgb(scaled, i, channels)) + b"\xff" for i in range(nw * nh))
        else:
            channels_out = channels
        fill = bytes(channels_out)
    else:
        channels_out = channels
        px = list(background)[:3]
        fill = bytes(px[:1] if channels_out <= 2 else px) + (b"\xff" if channels_out in (2, 4) else b"")

    canvas = bytearray(fill * (out_w * out_h))
    ox, oy = (out_w - nw) // 2, (out_h - nh) // 2
    for y in range(nh):
        ty = oy + y
        if not (0 <= ty < out_h):
            continue
        for x in range(nw):
            tx = ox + x
            if not (0 <= tx < out_w):
                continue
            s = (y * nw + x) * channels_out
            d = (ty * out_w + tx) * channels_out
            canvas[d:d + channels_out] = scaled[s:s + channels_out]
    return out_w, out_h, channels_out, bytes(canvas), round(scale, 4)
