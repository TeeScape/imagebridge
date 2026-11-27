#!/usr/bin/env python3
"""
ImageBridge MCP server.

Exposes the plugin's three scripts as real MCP tools, so a client calls
generate_image / ask_codex directly instead of going through a shell.

Zero dependencies: Python 3.8+ standard library only, no pip install.

stdio transport rules this file obeys (MCP spec, basic/transports):
  * stdout carries NOTHING but one JSON-RPC message per line
  * all logging goes to stderr
  * child processes get stdin=DEVNULL so they can never eat the protocol stream
"""

import base64
import json
import os
import signal
import tempfile
import shutil
import ssl
import struct
import urllib.error
import urllib.request
import subprocess
import sys

# Checked before anything else, in syntax every Python 3 can parse, so an old
# interpreter gets a sentence instead of a SyntaxError from somewhere further
# down. 3.8 is the floor because it is the oldest this is actually tested on;
# the code itself would likely run on 3.7.
if sys.version_info < (3, 8):
    sys.stderr.write(
        "imagebridge needs Python 3.8 or newer; this is %s.\n"
        "Point Claude Code's MCP config at a newer python3.\n"
        % ".".join(str(n) for n in sys.version_info[:3]))
    raise SystemExit(1)

SERVER_NAME = "imagebridge"
SERVER_VERSION = "0.15.0"

# Versions we can speak. The spec says: echo the client's version when we
# support it, otherwise answer with the latest we do support.
SUPPORTED_PROTOCOLS = ["2025-06-18"]
LATEST_PROTOCOL = SUPPORTED_PROTOCOLS[0]

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    import imagelib           # stdlib-only PNG decode / resize / encode
except Exception as _exc:     # a preview is a nicety; never let it break a generation
    imagelib = None
    _IMAGELIB_ERROR = str(_exc)
PLUGIN_ROOT = os.path.dirname(HERE)
SCRIPT_DIR = os.path.join(PLUGIN_ROOT, "skills", "imagebridge", "scripts")

INPAINT_TILE = 512
CONFIG_PATH = os.environ.get("IMAGEBRIDGE_CONFIG") or os.path.expanduser("~/.imagebridge/config.json")


def inpaint_url():
    """The LaMa endpoint, if the user has pointed us at one.

    Off unless configured, and never a built-in default: object removal needs a
    server running a model, and which one that is belongs to whoever installed
    this, not to the plugin.
    """
    env = os.environ.get("IMAGEBRIDGE_INPAINT_URL")
    if env:
        return env.strip()
    return (_config().get("inpaint_url") or "").strip() or None


def _config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            got = json.load(fh)
            return got if isinstance(got, dict) else {}
    except Exception:
        return {}


def ssl_context():
    """A context trusting a named CA bundle, when one is configured.

    Some Python builds ship without a usable CA store, and a self-hosted
    inpaint server may sit behind a private CA. Both are fixed by saying WHICH
    roots to trust — never by turning verification off, which would make every
    reply forgeable and is not an option this plugin offers at any setting.
    """
    bundle = (os.environ.get("IMAGEBRIDGE_CA_BUNDLE")
              or _config().get("ca_bundle") or "").strip()
    if not bundle:
        return None
    bundle = os.path.expanduser(bundle)
    if not os.path.isfile(bundle):
        raise RuntimeError("ca_bundle does not exist: %s" % bundle)
    return ssl.create_default_context(cafile=bundle)


MIME_BY_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".webp": "image/webp", ".gif": "image/gif"}

IMAGE_TIMEOUT_DEFAULT = 300
ASK_TIMEOUT_DEFAULT = 600


def log(message):
    print("[imagebridge] %s" % message, file=sys.stderr, flush=True)


def script_path(name):
    path = os.path.join(SCRIPT_DIR, name)
    if not os.path.isfile(path):
        raise RuntimeError(
            "bundled script not found: %s\n"
            "The plugin files are not where expected; reinstall imagebridge." % path
        )
    return path


def _kill_group(proc):
    """Terminate the child and anything it started."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
            return
        try:
            proc.wait(timeout=5)
            return
        except Exception:
            continue


def run_script(name, args, timeout):
    """Run a bundled script. Always `bash <path>` so a lost +x bit cannot break it."""
    cmd = ["bash", script_path(name)] + args
    log("run: %s" % " ".join(cmd[:3] + ["..."]))
    # start_new_session puts the child in its own process group, so a timeout can
    # kill codex *and* everything it spawned instead of orphaning them.
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,   # never let a child touch our protocol stdin
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=os.getcwd(),
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        try:
            proc.communicate(timeout=10)
        except Exception:
            pass
        raise RuntimeError(
            "%s timed out after %ss and was terminated. Image generation normally "
            "takes 45-60s; raise the limit with the timeout_seconds argument." % (name, timeout)
        )
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def png_size(path):
    """(width, height) from a PNG header, or None. No image library needed."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(24)
        if head[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        return struct.unpack(">II", head[16:24])
    except Exception:
        return None


# --------------------------------------------------------------------------- tools

TOOLS = [
    {
        "name": "generate_image",
        "title": "Generate an image with OpenAI Codex",
        "description": (
            "Generate an image from a text description using the OpenAI Codex CLI's built-in "
            "image generation (gpt-image-2 class), and save it to disk. Runs on the user's "
            "signed-in ChatGPT plan: no API key and no per-image API charge, but it does spend "
            "that plan's quota. Takes 45-60 seconds and about 10-12k Codex tokens per image, so "
            "do not call it speculatively or in a loop. The model chooses the output dimensions; "
            "there is no size or quality control on this path. Returns the absolute path of the "
            "saved file - read that file if you need to see the result."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "What the image should show. Be specific about style, colours and composition.",
                },
                "output_path": {
                    "type": "string",
                    "description": "Exact file path to write, e.g. 'assets/icon.png'. Parent directories are created.",
                },
                "output_dir": {
                    "type": "string",
                    "description": "Directory to write into, with a filename derived from the prompt. Ignored when output_path is given. Defaults to the current directory.",
                },
                "reference_images": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Paths of existing images that should guide the result.",
                },
                "keep_all": {
                    "type": "boolean",
                    "description": "Keep every image the run produced rather than only the newest. Default false.",
                },
                "transparent": {
                    "type": "boolean",
                    "description": "Ask for a transparent background. Real parameter of the image tool, but the model chooses it, so it is a strong request rather than a guarantee - check the result.",
                },
                "return_image": {
                    "type": "boolean",
                    "description": "Also return a small inline preview so you can SEE the result without a separate file read. Downscaled to 384px, so judge composition and colour from it, not fine detail. Default false.",
                },
                "max_dimension": {
                    "type": "integer",
                    "description": "Shrink the saved image so its longest side is at most this many pixels. The model still chooses the generated size; this resizes afterwards, and never enlarges.",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Give up after this long. Default 300.",
                },
            },
            "required": ["prompt"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ask_codex",
        "title": "Delegate a task to OpenAI Codex",
        "description": (
            "Send a self-contained task to the OpenAI Codex CLI and return its final answer. "
            "Use it for an independent second opinion on code or a diff, for reading a screenshot, "
            "or to run a well-specified coding task on the ChatGPT plan instead of spending this "
            "session's budget. Codex does not share this conversation and starts cold in the "
            "directory given, so write it a complete brief. Read-only by default."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "The complete, self-contained task for Codex."},
                "cwd": {"type": "string", "description": "Directory Codex should work in."},
                "allow_writes": {
                    "type": "boolean",
                    "description": "Let Codex edit files in that directory (sandbox workspace-write). Default false, which is read-only. Only set true when the user asked for file changes.",
                },
                "images": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Image paths to attach to the prompt, e.g. a screenshot.",
                },
                "web_search": {"type": "boolean", "description": "Allow live web search. Default false."},
                "model": {"type": "string", "description": "Codex model override."},
                "stdin_text": {"type": "string", "description": "Extra context to pipe in, such as a diff."},
                "timeout_seconds": {"type": "integer", "description": "Give up after this long. Default 600."},
            },
            "required": ["prompt"],
            "additionalProperties": False,
        },
    },
    {
        "name": "edit_image",
        "title": "Edit part of an existing image",
        "description": (
            "Change part of an image and keep the rest — the one thing plain generation cannot do. "
            "By default this runs on the user's ChatGPT plan through Codex and costs no API money: "
            "it is a real edit, measured as preserving a 1254x1254 icon's silhouette exactly while "
            "changing its colour. Use it freely for recolouring, adding or removing an element, "
            "restyling, or extending an image.\n\n"
            "provider='api' switches to the OpenAI API, which BILLS PER CALL and needs "
            "OPENAI_API_KEY. The only reasons to choose it are a pixel mask or an exact output "
            "size, which the free path cannot do. Never pick it without telling the user it costs "
            "money, and prefer the default."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "What should change about the image."},
                "images": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Source image path(s), png/webp/jpg, at most 16. The first is the one a mask applies to.",
                },
                "provider": {
                    "type": "string", "enum": ["codex", "api"],
                    "description": "'codex' (default) runs on the ChatGPT plan and is free. 'api' bills per call and is only for a mask or an exact size.",
                },
                "mask": {
                    "type": "string",
                    "description": "API path only: a PNG whose transparent areas mark what to edit, same dimensions as the first image.",
                },
                "from_last": {
                    "type": "integer",
                    "description": "Codex path only: edit the last N images of the Codex conversation (1-5) instead of files on disk.",
                },
                "output_path": {"type": "string", "description": "Exact file path to write."},
                "output_dir": {"type": "string", "description": "Directory to write into. Default: current directory."},
                "size": {"type": "string", "description": "e.g. '1024x1024', or 'auto'."},
                "quality": {"type": "string", "description": "low | medium | high | xhigh | max | auto"},
                "background": {"type": "string", "description": "transparent | opaque | auto"},
                "model": {"type": "string", "description": "Image model; defaults to the CLI's own default."},
                "return_image": {
                    "type": "boolean",
                    "description": "Also return a small inline preview so you can SEE the result without a separate file read. Downscaled to 384px, so judge composition and colour from it, not fine detail. Default false.",
                },
                "max_dimension": {
                    "type": "integer",
                    "description": "Shrink the saved image so its longest side is at most this many pixels. The model still chooses the generated size; this resizes afterwards, and never enlarges.",
                },
                "timeout_seconds": {"type": "integer", "description": "Give up after this long. Default 300."},
            },
            "required": ["prompt", "images"],
            "additionalProperties": False,
        },
    },
    {
        "name": "remove_object",
        "title": "Remove something from a photo and fill the gap",
        "description": (
            "Erase part of an image and let the surroundings close over it — a person out of a "
            "street, a sign off a wall, a blemish. This is NOT generative editing: it removes and "
            "fills plausibly, so it cannot add something that was never there. Use edit_image when "
            "the request is to put something IN.\n\n"
            "Runs on a LaMa server that the user configures; it costs no API money. If no endpoint "
            "is set the tool says so and does nothing.\n\n"
            "Give exactly one of `points` (a point inside each object - PREFER THIS, it follows "
            "the object's shape), `regions` (rectangles) or `mask` (a PNG whose transparent areas "
            "mark what to remove). A rectangle that clips the object leaves a stub of it behind "
            "and is the usual reason a removal looks bad; a point does not have that failure."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "image": {"type": "string", "description": "Path of the image to edit - PNG, or a JPEG this machine can convert. The original is never modified."},
                "regions": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4},
                    "description": "Rectangles to erase, each [x, y, width, height] in pixels from the top-left. Prefer this: it is far easier to get right than a painted mask.",
                },
                "mask": {"type": "string", "description": "Alternative to regions: a PNG the same size as the image whose FULLY TRANSPARENT areas are removed."},
                "points": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2},
                    "description": "Alternative to regions: [x, y] points, one well INSIDE each thing to remove. The area is grown out from each point by colour, so it follows the object's real shape instead of a rectangle - usually a smaller hole AND a cleaner removal. Seed the middle of the object, never its edge: an edge pixel is a blend of object and background and will either select nothing or leak into the picture.",
                },
                "tolerance": {"type": "integer", "description": "With points: how different a neighbouring colour may be and still be taken, 0-255. Default 40. Lower it if the selection leaks."},
                "grow": {"type": "integer", "description": "With points: pixels to expand the selection by, to swallow the object's antialiased edge. Default 2; raise it if a faint outline of the removed thing survives."},
                "output_path": {"type": "string", "description": "Where to write the result. Defaults to <image>-removed.png next to the original."},
                "return_image": {"type": "boolean", "description": "Also return a 384px preview so you can check the fill. Default false."},
                "timeout_seconds": {"type": "integer", "description": "Give up after this long. Default 120."},
            },
            "required": ["image"],
            "additionalProperties": False,
        },
    },
    {
        "name": "inspect_image",
        "title": "Measure an image, or what changed between two",
        "description": (
            "Read an image's real dimensions, transparency and colours, and — with `compare_to` — "
            "measure what an edit actually did: how many pixels moved, the bounding box they "
            "landed in, and whether the visible silhouette survived.\n\n"
            "Use it after edit_image or remove_object instead of taking the model's word. When the "
            "ask was 'change only the sky', the changed box is the evidence; when it was 'keep the "
            "shape', silhouette_iou near 1.0 is. Costs nothing, needs no network, and takes "
            "milliseconds - there is no reason to skip it, and reporting a measured result is "
            "worth more to the user than repeating what was requested."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "image": {"type": "string", "description": "Path of the PNG to measure."},
                "compare_to": {"type": "string", "description": "A second PNG of the SAME dimensions - usually the original, to see what the edit changed."},
                "tolerance": {"type": "integer", "description": "How far a channel may move before it counts as changed, 0-255. Default 8, which ignores re-encoding noise."},
            },
            "required": ["image"],
            "additionalProperties": False,
        },
    },
    {
        "name": "fit_image",
        "title": "Trim and reframe an image to an exact size",
        "description": (
            "Put an image on a canvas of exactly the size you need. Free, local and instant.\n\n"
            "Ask the free path for an icon and it returns a composition: the one measured while "
            "this was written came back 512x341 with two thirds of the frame empty white. This "
            "trims the flat border away and centres what is left on the canvas you asked for.\n\n"
            "Be straight with the user about what this is: framing, not generation. The model did "
            "not compose at this size. For an icon or a logo on a plain background that is exactly "
            "right; for a photograph whose composition matters, mode 'crop' throws away the edges, "
            "so say which you used."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "image": {"type": "string", "description": "Path of the image to reframe. The original is never modified."},
                "width": {"type": "integer", "description": "Canvas width in pixels."},
                "height": {"type": "integer", "description": "Canvas height in pixels. Defaults to width, i.e. a square."},
                "mode": {"type": "string", "enum": ["pad", "crop"], "description": "pad (default): fit it all in and fill the rest with the background. crop: fill the canvas and lose what hangs over the edge."},
                "trim": {"type": "boolean", "description": "Cut the flat border off first. Default true, which is what makes a loose composition into a tight icon."},
                "background": {"type": "string", "description": "'auto' (default: the colour already at the border), 'transparent', or a #rrggbb colour."},
                "margin": {"type": "integer", "description": "Blank pixels to leave around the content. Default 0."},
                "output_path": {"type": "string", "description": "Where to write it. Defaults to <image>-fitted.png."},
                "return_image": {"type": "boolean", "description": "Also return a 384px preview. Default false."},
            },
            "required": ["image", "width"],
            "additionalProperties": False,
        },
    },
    {
        "name": "check_setup",
        "title": "Check the ImageBridge prerequisites",
        "description": (
            "Report whether the Codex CLI is installed and signed in, whether the optional OpenAI "
            "CLI and API key are present, and where the bundled scripts live. Call this first when "
            "a tool fails, to tell the user what to fix."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


PREVIEW_SIDE = 384


# Converters that are already on the machine, best first. None is a dependency:
# if none is present the tools say so and name the command to run, rather than
# failing with four words. Arguments are passed as a list, never through a shell.
CONVERTERS = (
    ("sips",    lambda i, o: ["sips", "-s", "format", "png", "-s", "formatOptions", "best", i, "--out", o]),
    ("magick",  lambda i, o: ["magick", i, "png24:" + o]),
    ("convert", lambda i, o: ["convert", i, "png24:" + o]),
    ("ffmpeg",  lambda i, o: ["ffmpeg", "-v", "error", "-y", "-i", i, "-pix_fmt", "rgb24", o]),
)


def converter_available():
    for name, _ in CONVERTERS:
        if shutil.which(name):
            return name
    return None


def read_image(path):
    """Pixels from any image this machine can turn into a plain 8-bit PNG.

    imagelib handles 8-bit non-interlaced PNG and nothing else, on purpose. But
    the picture someone wants an object removed from is usually a photograph,
    which is a JPEG, and a 16-bit or interlaced PNG hits the same wall. Rather
    than widen the decoder, convert with whatever is already installed - sips
    ships with macOS - and read the result.
    """
    try:
        return imagelib.read_png(path)
    except imagelib.Unsupported as first:
        pass
    except Exception:
        raise

    tried = []
    tmp = tempfile.mkdtemp(prefix="imagebridge-conv-")
    try:
        for name, build in CONVERTERS:
            if not shutil.which(name):
                continue
            out = os.path.join(tmp, "converted.png")
            try:
                subprocess.run(build(path, out), check=True, timeout=120,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                return imagelib.read_png(out)
            except Exception as exc:
                tried.append("%s (%s)" % (name, str(exc).strip()[:80]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    detail = ("tried " + ", ".join(tried)) if tried else (
        "no image converter is installed: expected one of " +
        ", ".join(n for n, _ in CONVERTERS))
    raise RuntimeError(
        "cannot read %s - %s.\n%s\nImageBridge reads 8-bit PNG directly and converts anything "
        "else with a tool already on the machine. On macOS sips is built in; elsewhere install "
        "ImageMagick or ffmpeg. Tell the user that rather than asking them for a different file."
        % (os.path.basename(path), _png_reason(path), detail))


def _png_reason(path):
    try:
        imagelib.read_png(path)
        return "it reads fine"
    except Exception as exc:
        return str(exc)


def _describe(path):
    """'1254x1254, 1.2 MB' for a saved file, without assuming it is a PNG."""
    size = png_size(path)
    return "%s, %d bytes" % (("%dx%d" % size) if size else "size unknown",
                             os.path.getsize(path))


def _apply_max_dimension(paths, max_dimension):
    """Shrink saved images in place. Returns notes to show the caller."""
    notes = []
    if not max_dimension:
        return notes
    if imagelib is None:
        notes.append("max_dimension was ignored: imagelib is unavailable (%s)" % _IMAGELIB_ERROR)
        return notes
    for path in paths:
        try:
            ow, oh, nw, nh = imagelib.resize_file(path, int(max_dimension))
            if (ow, oh) != (nw, nh):
                notes.append("resized %s from %dx%d to %dx%d"
                             % (os.path.basename(path), ow, oh, nw, nh))
            else:
                notes.append("%s was already within %dpx"
                             % (os.path.basename(path), max_dimension))
        except Exception as exc:
            notes.append("could not resize %s: %s" % (os.path.basename(path), exc))
    return notes


def _image_blocks(paths, want_image):
    """An inline preview per image, when asked for and possible."""
    blocks, notes = [], []
    if not want_image:
        return blocks, notes
    if imagelib is None:
        return blocks, ["return_image was ignored: imagelib is unavailable (%s)" % _IMAGELIB_ERROR]
    for path in paths:
        try:
            data, w, h = imagelib.preview_base64(path, PREVIEW_SIDE)
            blocks.append({"type": "image", "data": data, "mimeType": "image/png"})
            notes.append("preview of %s shown at %dx%d" % (os.path.basename(path), w, h))
        except Exception as exc:
            notes.append("no preview for %s: %s" % (os.path.basename(path), exc))
    return blocks, notes


def tool_generate_image(args):
    prompt = args.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt is required and must be a non-empty string")

    # Flags first, then `--`, then the prompt. Putting `--` at the front turns
    # every later flag into a positional and the script rejects the call: that
    # is how generate_image was broken through MCP from 0.3.0 to 0.5.0, with no
    # test covering a SUCCESSFUL generate to catch it.
    cli = []
    if args.get("output_path"):
        cli += ["-o", str(args["output_path"])]
    elif args.get("output_dir"):
        cli += ["-d", str(args["output_dir"])]
    for ref in args.get("reference_images") or []:
        if not os.path.isfile(ref):
            raise ValueError("reference image not found: %s" % ref)
        cli += ["-r", str(ref)]
    if args.get("keep_all"):
        cli.append("-a")
    if args.get("transparent"):
        cli.append("-t")
    cli += ["--", prompt]

    timeout = int(args.get("timeout_seconds") or IMAGE_TIMEOUT_DEFAULT)
    proc = run_script("codex-image", cli, timeout)

    paths = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    paths = [p for p in paths if os.path.isfile(p)]
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            "codex-image failed (exit %s); not treating this as a success even though "
            "%d path(s) were printed.\n%s" % (proc.returncode, len(paths), detail[-1500:]))
    if not paths:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            "no image was produced (exit %s).\n%s" % (proc.returncode, detail[-1500:])
        )

    notes = _apply_max_dimension(paths, args.get("max_dimension"))
    previews, pnotes = _image_blocks(paths, args.get("return_image"))
    notes += pnotes

    lines, blocks = [], []
    for path in paths:
        lines.append("%s  (%s)" % (path, _describe(path)))
        blocks.append(
            {
                "type": "resource_link",
                "uri": "file://" + path,
                "name": os.path.basename(path),
                "description": "Generated image",
                "mimeType": MIME_BY_EXT.get(os.path.splitext(path)[1].lower(), "application/octet-stream"),
            }
        )

    header = "Generated %d image%s:" % (len(paths), "" if len(paths) == 1 else "s")
    note = ("\n\nThe model chose the generated dimensions; they cannot be requested on this "
            "path. Check the result before reporting it as done.")
    if notes:
        note += "\n" + "\n".join("- " + n for n in notes)
    return {
        "content": [{"type": "text", "text": header + "\n" + "\n".join(lines) + note}]
                   + previews + blocks,
        "structuredContent": {"paths": paths},
    }


def tool_ask_codex(args):
    prompt = args.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt is required and must be a non-empty string")

    cli = []
    if args.get("allow_writes"):
        cli.append("-w")
    if args.get("cwd"):
        cli += ["-C", str(args["cwd"])]
    if args.get("model"):
        cli += ["-m", str(args["model"])]
    if args.get("web_search"):
        cli.append("--search")
    stdin_text = args.get("stdin_text")
    if stdin_text:
        cli.append("--stdin")
    for img in args.get("images") or []:
        if not os.path.isfile(img):
            raise ValueError("image not found: %s" % img)
        cli += ["-i", str(img)]
    cli.append(prompt)

    timeout = int(args.get("timeout_seconds") or ASK_TIMEOUT_DEFAULT)

    if stdin_text:
        cmd = ["bash", script_path("codex-ask")] + cli
        try:
            proc = subprocess.run(
                cmd, input=stdin_text, capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("codex-ask timed out after %ss" % timeout)
    else:
        proc = run_script("codex-ask", cli, timeout)

    answer = (proc.stdout or "").strip()
    if proc.returncode != 0 and not answer:
        raise RuntimeError(
            "codex-ask failed (exit %s).\n%s" % (proc.returncode, (proc.stderr or "")[-1500:])
        )
    if not answer:
        answer = "(Codex returned an empty answer.)"
    return {"content": [{"type": "text", "text": answer}]}


def tool_edit_image(args):
    prompt = args.get("prompt")
    images = args.get("images") or []
    provider = (args.get("provider") or "codex").lower()
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt is required and must be a non-empty string")
    if provider not in ("codex", "api"):
        raise ValueError("provider must be 'codex' or 'api'")

    # ---- the free path, on the user's ChatGPT plan -------------------------
    if provider == "codex":
        if args.get("mask"):
            raise ValueError(
                "a pixel mask is only available on the API path, which bills per call. "
                "Either describe in words which part to change (the free path handles that "
                "well), or ask the user whether to spend an API call and pass provider='api'.")
        from_last = args.get("from_last")
        if not images and not from_last:
            raise ValueError("give images (file paths) or from_last (1-5) to say what to edit")
        cli = []
        if args.get("output_path"):
            cli += ["-o", str(args["output_path"])]
        elif args.get("output_dir"):
            cli += ["-d", str(args["output_dir"])]
        for img in images:
            if not os.path.isfile(img):
                raise ValueError("image not found: %s" % img)
            cli += ["-r", str(img)]
        if from_last:
            cli += ["--from-last", str(from_last)]
        cli += ["--", prompt]
        timeout = int(args.get("timeout_seconds") or IMAGE_TIMEOUT_DEFAULT)
        proc = run_script("codex-image", cli, timeout)
        if proc.returncode != 0:
            raise RuntimeError("codex-image failed (exit %s).\n%s"
                               % (proc.returncode, (proc.stderr or proc.stdout or "")[-1500:]))
        paths = [l.strip() for l in proc.stdout.splitlines()
                 if l.strip() and os.path.isfile(l.strip())]
        if not paths:
            raise RuntimeError("no edited image was produced.\n%s"
                               % (proc.stdout or proc.stderr or "")[-1500:])
        notes = _apply_max_dimension(paths, args.get("max_dimension"))
        previews, pnotes = _image_blocks(paths, args.get("return_image"))
        notes += pnotes
        lines, blocks = [], []
        for path in paths:
            lines.append("%s  (%s)" % (path, _describe(path)))
            blocks.append({"type": "resource_link", "uri": "file://" + path,
                           "name": os.path.basename(path), "description": "Edited image",
                           "mimeType": MIME_BY_EXT.get(os.path.splitext(path)[1].lower(),
                                                       "application/octet-stream")})
        tail = ("\n\nThe original is untouched. Check the result before reporting it done.")
        if notes:
            tail += "\n" + "\n".join("- " + n for n in notes)
        return {"content": [{"type": "text", "text":
                "Edited %d image%s on the ChatGPT plan (no API charge):\n%s%s"
                % (len(paths), "" if len(paths) == 1 else "s", "\n".join(lines), tail)}]
                + previews + blocks,
                "structuredContent": {"paths": paths, "provider": "codex"}}

    # ---- the paid path: only for a mask or an exact size -------------------
    if not isinstance(images, list) or not images:
        raise ValueError("images is required and must be a non-empty array of paths")

    cli = []
    for img in images:
        cli += ["-i", str(img)]
    if args.get("mask"):
        cli += ["-m", str(args["mask"])]
    if args.get("output_path"):
        cli += ["-o", str(args["output_path"])]
    elif args.get("output_dir"):
        cli += ["-d", str(args["output_dir"])]
    for flag, key in (("--size", "size"), ("--quality", "quality"),
                      ("--background", "background"), ("--model", "model")):
        if args.get(key):
            cli += [flag, str(args[key])]
    cli += ["--", prompt]

    timeout = int(args.get("timeout_seconds") or IMAGE_TIMEOUT_DEFAULT)
    proc = run_script("openai-edit", cli, timeout)

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError("openai-edit failed (exit %s).\n%s" % (proc.returncode, detail[-1500:]))

    paths = [l.strip() for l in proc.stdout.splitlines() if l.strip() and os.path.isfile(l.strip())]
    if not paths:
        raise RuntimeError("openai-edit produced no file.\n%s"
                           % (proc.stdout or proc.stderr or "")[-1500:])

    notes = _apply_max_dimension(paths, args.get("max_dimension"))
    previews, pnotes = _image_blocks(paths, args.get("return_image"))
    notes += pnotes
    lines, blocks = [], []
    for path in paths:
        lines.append("%s  (%s)" % (path, _describe(path)))
        blocks.append({"type": "resource_link", "uri": "file://" + path,
                       "name": os.path.basename(path), "description": "Edited image",
                       "mimeType": MIME_BY_EXT.get(os.path.splitext(path)[1].lower(),
                                                   "application/octet-stream")})
    tail = "\n\nThe original was kept."
    if notes:
        tail += "\n" + "\n".join("- " + n for n in notes)
    return {"content": [{"type": "text", "text":
            "Edited %d image%s — BILLED TO THE OPENAI API:\n%s%s"
            % (len(paths), "" if len(paths) == 1 else "s", "\n".join(lines), tail)}]
            + previews + blocks,
            "structuredContent": {"paths": paths, "provider": "api"}}


def _post_png(url, payload, timeout):
    # Identify ourselves. urllib's default is "Python-urllib/3.x", which any
    # sensible bot filter blocks on sight — including the one in front of the
    # server this was first pointed at.
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Content-Type": "image/png",
                 "User-Agent": "imagebridge/%s (object removal)" % SERVER_VERSION})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError("the inpaint server answered %s %s" % (exc.code, exc.reason))
    except Exception as exc:
        hint = ""
        if "CERTIFICATE_VERIFY_FAILED" in str(exc):
            hint = ("\nThis is a certificate store problem on this machine, not a problem with "
                    "the server: this Python cannot find a CA bundle. On macOS run the "
                    "'Install Certificates.command' that ships with Python, or point SSL_CERT_FILE "
                    "at a bundle such as /etc/ssl/cert.pem, or set ca_bundle in %s to that path. "
                    "Do not work around it by disabling verification." % CONFIG_PATH)
        raise RuntimeError("could not reach the inpaint server at %s: %s%s" % (url, exc, hint))


def tool_remove_object(args):
    if imagelib is None:
        raise RuntimeError("imagelib is unavailable (%s), so this cannot run" % _IMAGELIB_ERROR)
    url = inpaint_url()
    if not url:
        raise RuntimeError(
            "no inpaint endpoint is configured, so object removal is off.\n"
            "Point IMAGEBRIDGE_INPAINT_URL, or inpaint_url in %s, at a LaMa server that takes a "
            "512x512 RGBA PNG (alpha 0 marks the hole) and returns a 512x512 PNG. The plugin ships "
            "no default: whose server does the work is the user's choice, not ours.\n"
            "Tell the user this rather than silently falling back to the billed edit path."
            % CONFIG_PATH)

    image = args.get("image")
    if not image or not os.path.isfile(image):
        raise ValueError("image not found: %s" % image)
    regions, mask_path, points = args.get("regions"), args.get("mask"), args.get("points")
    given = [bool(regions), bool(mask_path), bool(points)]
    if sum(given) != 1:
        raise ValueError("give exactly one of points, regions or mask")

    width, height, channels, pixels = read_image(image)
    if points:
        mask = imagelib.select(pixels, width, height, channels, points,
                               tolerance=int(args.get("tolerance") or 40),
                               grow=2 if args.get("grow") is None else int(args.get("grow")))
    elif mask_path:
        mask = imagelib.mask_from_png(mask_path, width, height)
    else:
        mask = imagelib.rect_mask(width, height, regions)

    timeout = int(args.get("timeout_seconds") or 120)
    N = INPAINT_TILE
    # One request per hole, on a crop around it, rather than one request for the
    # whole picture. The model only ever sees a 512 square, so sending a 4000px
    # photo renders the fill at an eighth of the picture's resolution and returns
    # a soft patch in a sharp photograph. A hole big enough to need everything
    # gives back a single full-frame window, which is what this used to do.
    wins = imagelib.windows(mask, width, height, tile=N)
    out = bytearray(pixels)

    for win in wins:
        ww, wh = win[2], win[3]
        _, _, sub = imagelib.crop(pixels, width, height, channels, win)
        submask = imagelib.crop_mask(mask, width, win)
        small = imagelib.resize(sub, ww, wh, N, N, channels)
        # Carry the hole through the same resampler, then take anything that
        # picked up a trace of it: a hole that shrinks under downsampling leaves
        # a rim of the thing we were asked to remove.
        hole_src = bytes(0 if m else 255 for m in submask)
        hole_small = imagelib.resize(hole_src, ww, wh, N, N, 1)
        mask_small = bytes(0 if h > 2 else 1 for h in hole_small)
        payload = imagelib.write_png(None, N, N, 4,
                                     imagelib.to_rgba_with_hole(small, N, N, channels, mask_small))
        filled_png = _post_png(url, payload, timeout)
        try:
            fw, fh, fch, fpx = imagelib.read_png_bytes(filled_png)
        except Exception as exc:
            raise RuntimeError("the inpaint server did not return a usable PNG: %s" % exc)
        if fch != 3:   # compose() reads the fill as RGB
            fpx = b"".join(fpx[i * fch:i * fch + 3] for i in range(fw * fh))
        imagelib.paste(out, width, channels, win,
                       imagelib.compose(fpx, fw, fh, sub, ww, wh, channels, submask))

    out_path = args.get("output_path") or (os.path.splitext(image)[0] + "-removed.png")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    imagelib.write_png(out_path, width, height, channels, bytes(out))

    removed = sum(1 for m in mask if not m)
    previews, notes = _image_blocks([out_path], args.get("return_image"))
    biggest = max(w[2] for w in wins)
    text = ("Removed %d pixel%s (%.1f%% of the image) and filled the gap.\n%s  (%s)\n"
            "Filled in %d window%s, the largest %dpx wide, so the fill is rendered at %d%% of "
            "the picture's own resolution.\n\n"
            "The original is untouched. This fills from the surroundings — check that the result "
            "is plausible rather than assuming it is. inspect_image with compare_to will tell you "
            "exactly where the change landed."
            % (removed, "" if removed == 1 else "s", 100.0 * removed / (width * height),
               out_path, _describe(out_path), len(wins), "" if len(wins) == 1 else "s",
               biggest, round(100.0 * N / biggest)))
    if notes:
        text += "\n" + "\n".join("- " + n for n in notes)
    return {"content": [{"type": "text", "text": text}] + previews
            + [{"type": "resource_link", "uri": "file://" + out_path,
                "name": os.path.basename(out_path), "description": "Object removed",
                "mimeType": "image/png"}],
            "structuredContent": {"path": out_path, "removed_pixels": removed,
                                  "windows": wins, "tile": N}}


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(rgb)


def tool_inspect_image(args):
    if imagelib is None:
        raise RuntimeError("imagelib is unavailable (%s), so this cannot run" % _IMAGELIB_ERROR)
    image = args.get("image")
    if not image or not os.path.isfile(image):
        raise ValueError("image not found: %s" % image)
    a = read_image(image)
    info = imagelib.stats(*a)

    lines = ["%s: %dx%d, %s" % (os.path.basename(image), info["width"], info["height"],
                                "with alpha" if info["has_alpha"] else "no alpha")]
    if info["has_alpha"]:
        lines.append("  %.1f%% transparent" % (info["transparent_fraction"] * 100))
    if info["mean_colour"]:
        lines.append("  mean %s, mostly %s" % (
            _hex(info["mean_colour"]),
            ", ".join("%s (%.0f%%)" % (_hex(c["rgb"]), c["fraction"] * 100)
                      for c in info["dominant_colours"])))

    other = args.get("compare_to")
    if other:
        if not os.path.isfile(other):
            raise ValueError("compare_to not found: %s" % other)
        tol = args.get("tolerance")
        tol = 8 if tol is None else max(0, min(255, int(tol)))
        b = read_image(other)
        rescaled = None
        if (b[0], b[1]) != (a[0], a[1]):
            # The free edit path hands back whatever size the model chose, so
            # "measure every edit" would be advice that usually cannot be
            # followed. Compare on the smaller grid: downscaling discards
            # detail, upscaling would invent it, and the question being asked
            # - what changed, and where - survives the first but not the
            # second. Resampling is not free, though, so say so: small
            # differences here are the resampler, not the edit.
            if b[0] * b[1] <= a[0] * a[1]:
                small, big, who = b, a, "this image"
            else:
                small, big, who = a, b, os.path.basename(other)
            scaled = imagelib.resize(big[3], big[0], big[1], small[0], small[1], big[2])
            if big is a:
                a_cmp, b_cmp = (small[0], small[1], big[2], scaled), small
            else:
                a_cmp, b_cmp = small, (small[0], small[1], big[2], scaled)
            rescaled = "%s was %dx%d and is compared at %dx%d" % (
                who, big[0], big[1], small[0], small[1])
            d = imagelib.diff(b_cmp, a_cmp, tolerance=tol)
        else:
            d = imagelib.diff(b, a, tolerance=tol)
        if rescaled:
            d["rescaled"] = rescaled
        info["comparison"] = d
        info["compared_to"] = other
        if rescaled:
            lines.append("The two are different sizes: %s. Resampling puts a floor under these "
                         "numbers - a few per cent of changed pixels may be the resampler rather "
                         "than the edit." % rescaled)
        if not d["changed_pixels"]:
            lines.append("Nothing changed against %s - not one pixel beyond tolerance %d. If an "
                         "edit was meant to happen, it did not." % (os.path.basename(other), tol))
        else:
            x, y, w, h = d["changed_box"]
            lines.append("Against %s: %.1f%% of pixels changed, all inside [x=%d y=%d w=%d h=%d]."
                         % (os.path.basename(other), d["changed_fraction"] * 100, x, y, w, h))
            if d["silhouette_iou"] is not None:
                lines.append("  silhouette IoU %.3f%s" % (
                    d["silhouette_iou"],
                    " - the visible shape is unchanged" if d["silhouette_iou"] > 0.999 else ""))
    return {"content": [{"type": "text", "text": "\n".join(lines)}],
            "structuredContent": info}


def tool_fit_image(args):
    if imagelib is None:
        raise RuntimeError("imagelib is unavailable (%s), so this cannot run" % _IMAGELIB_ERROR)
    image = args.get("image")
    if not image or not os.path.isfile(image):
        raise ValueError("image not found: %s" % image)
    out_w = int(args.get("width") or 0)
    out_h = int(args.get("height") or out_w)
    if out_w < 1 or out_h < 1:
        raise ValueError("width and height must be at least 1 pixel")
    mode = args.get("mode") or "pad"
    if mode not in ("pad", "crop"):
        raise ValueError("mode must be 'pad' or 'crop'")

    width, height, channels, pixels = read_image(image)
    trim = True if args.get("trim") is None else bool(args.get("trim"))
    box = imagelib.content_box(pixels, width, height, channels) if trim else [0, 0, width, height]

    bg = (args.get("background") or "auto").strip().lower()
    if bg == "auto":
        background = None
    elif bg == "transparent":
        background = "transparent"
    elif bg.startswith("#") and len(bg) == 7:
        background = tuple(int(bg[i:i + 2], 16) for i in (1, 3, 5))
    else:
        raise ValueError("background must be 'auto', 'transparent' or #rrggbb, not %r" % bg)

    ow, oh, oc, px, scale = imagelib.fit_canvas(
        pixels, width, height, channels, box, out_w, out_h, mode,
        background=background, margin=max(0, int(args.get("margin") or 0)))

    out_path = args.get("output_path") or (os.path.splitext(image)[0] + "-fitted.png")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    imagelib.write_png(out_path, ow, oh, oc, px)

    previews, notes = _image_blocks([out_path], args.get("return_image"))
    emptied = 1 - (box[2] * box[3]) / float(width * height)
    text = ("%dx%d -> %dx%d.\n%s  (%s)\n"
            "%sContent scaled %.3gx and centred, mode '%s'.\n\n"
            "This is framing, not generation: the model did not compose at this size. Say so."
            % (width, height, ow, oh, out_path, _describe(out_path),
               ("Trimmed a flat border that was %.0f%% of the frame, down to %dx%d. "
                % (emptied * 100, box[2], box[3])) if trim else "",
               scale, mode))
    if notes:
        text += "\n" + "\n".join("- " + n for n in notes)
    return {"content": [{"type": "text", "text": text}] + previews
            + [{"type": "resource_link", "uri": "file://" + out_path,
                "name": os.path.basename(out_path), "description": "Reframed",
                "mimeType": "image/png"}],
            "structuredContent": {"path": out_path, "width": ow, "height": oh,
                                  "content_box": box, "scale": scale, "mode": mode}}


def tool_check_setup(_args):
    def probe(cmd):
        try:
            out = subprocess.run(
                cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20
            )
            return (out.stdout or out.stderr or "").strip().splitlines()[0]
        except Exception:
            return None

    codex = probe(["codex", "--version"])
    auth = probe(["codex", "login", "status"]) if codex else None
    report = [
        "Codex CLI    : %s" % (codex or "NOT INSTALLED - brew install --cask codex, or npm install -g @openai/codex"),
        "Codex auth   : %s" % ((auth or "unknown - run `codex login` yourself") if codex else "n/a"),
        "OpenAI CLI   : %s" % (probe(["openai", "--version"]) or "not installed (only needed for exact-size images)"),
        "OPENAI_API_KEY: %s" % ("set" if os.environ.get("OPENAI_API_KEY") else "not set (fine - the Codex path does not use it)"),
        "Image reader : 8-bit PNG directly; %s" % (
            ("JPEG and the rest via " + converter_available()) if converter_available()
            else "NOTHING ELSE - no sips, magick or ffmpeg on PATH, so a JPEG cannot be read"),
        "Object removal: %s" % (inpaint_url() or "off - no endpoint configured in " + CONFIG_PATH),
        "Scripts      : %s" % (SCRIPT_DIR if os.path.isdir(SCRIPT_DIR) else "MISSING at " + SCRIPT_DIR),
        "Server       : %s %s on python %s (needs 3.8+, tested to 3.14)"
        % (SERVER_NAME, SERVER_VERSION, sys.version.split()[0]),
    ]
    signed_in = bool(codex) and bool(auth) and "logged in" in auth.lower()
    ready = bool(codex) and signed_in and os.path.isdir(SCRIPT_DIR)
    report.append("")
    report.append("Ready to generate images: %s" % ("yes" if ready else "no - fix the lines above"))
    return {
        "content": [{"type": "text", "text": "\n".join(report)}],
        "structuredContent": {"ready": ready, "codex_installed": bool(codex), "signed_in": signed_in,
                              "converter": converter_available(),
                              "inpaint_configured": bool(inpaint_url())},
    }


HANDLERS = {
    "generate_image": tool_generate_image,
    "edit_image": tool_edit_image,
    "remove_object": tool_remove_object,
    "ask_codex": tool_ask_codex,
    "inspect_image": tool_inspect_image,
    "fit_image": tool_fit_image,
    "check_setup": tool_check_setup,
}

# --------------------------------------------------------------------------- protocol


def handle(message):
    """Return a response dict, or None for notifications (which get no reply)."""
    method = message.get("method")
    msg_id = message.get("id")
    params = message.get("params") or {}

    if msg_id is None:  # a notification; JSON-RPC forbids responding
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def fail(code, text):
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": text}}

    if method == "initialize":
        asked = params.get("protocolVersion")
        agreed = asked if asked in SUPPORTED_PROTOCOLS else LATEST_PROTOCOL
        return ok(
            {
                "protocolVersion": agreed,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": (
                    "Image generation runs on the user's ChatGPT plan and takes 45-60 seconds "
                    "per image. Call check_setup first if a tool fails."
                ),
            }
        )

    if method == "ping":
        return ok({})

    if method == "tools/list":
        return ok({"tools": TOOLS})

    if method == "tools/call":
        name = params.get("name")
        handler = HANDLERS.get(name)
        if handler is None:
            # Unknown tool is a protocol error, not a tool error.
            return fail(-32602, "Unknown tool: %s" % name)
        try:
            return ok(handler(params.get("arguments") or {}))
        except Exception as exc:  # anything the model could fix -> isError
            log("tool %s failed: %r" % (name, exc))
            return ok({"content": [{"type": "text", "text": str(exc)}], "isError": True})

    return fail(-32601, "Method not found: %s" % method)


def main():
    log("started; scripts at %s" % SCRIPT_DIR)
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except Exception:
            out.write(json.dumps({"jsonrpc": "2.0", "id": None,
                                  "error": {"code": -32700, "message": "Parse error"}}) + "\n")
            out.flush()
            continue
        if not isinstance(message, dict):
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32600, "message": "Invalid Request: expected an object"}}) + "\n")
            sys.stdout.flush()
            continue
        try:
            response = handle(message)
        except Exception as exc:
            log("handler crashed: %r" % exc)
            response = {"jsonrpc": "2.0", "id": message.get("id") if isinstance(message, dict) else None,
                        "error": {"code": -32603, "message": "Internal error: %s" % exc}}
        if response is not None:
            out.write(json.dumps(response) + "\n")
            out.flush()
    log("stdin closed; exiting")


if __name__ == "__main__":
    main()
