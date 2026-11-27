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


def _unfilter(raw, width, height, channels):
    """Undo the per-scanline filters. See PNG spec §9.2."""
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
        blob = fh.read()
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
            if depth != 8:
                raise Unsupported("bit depth %d (only 8 is handled)" % depth)
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
    return width, height, channels, _unfilter(zlib.decompress(bytes(idat)), width, height, channels)


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
