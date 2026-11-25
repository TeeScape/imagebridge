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
import struct
import subprocess
import sys

SERVER_NAME = "imagebridge"
SERVER_VERSION = "0.2.0"

# Versions we can speak. The spec says: echo the client's version when we
# support it, otherwise answer with the latest we do support.
SUPPORTED_PROTOCOLS = ["2025-06-18", "2025-03-26", "2024-11-05"]
LATEST_PROTOCOL = SUPPORTED_PROTOCOLS[0]

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(HERE)
SCRIPT_DIR = os.path.join(PLUGIN_ROOT, "skills", "imagebridge", "scripts")

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


def run_script(name, args, timeout):
    """Run a bundled script. Always `bash <path>` so a lost +x bit cannot break it."""
    cmd = ["bash", script_path(name)] + args
    log("run: %s" % " ".join(cmd[:3] + ["..."]))
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,   # never let a child touch our protocol stdin
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=os.getcwd(),
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            "%s timed out after %ss. Image generation normally takes 45-60s; "
            "raise the limit with the timeout_seconds argument." % (name, timeout)
        )
    return proc


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


def tool_generate_image(args):
    prompt = args.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt is required and must be a non-empty string")

    cli = [prompt]
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

    timeout = int(args.get("timeout_seconds") or IMAGE_TIMEOUT_DEFAULT)
    proc = run_script("codex-image", cli, timeout)

    paths = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    paths = [p for p in paths if os.path.isfile(p)]
    if not paths:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            "no image was produced (exit %s).\n%s" % (proc.returncode, detail[-1500:])
        )

    lines, blocks = [], []
    for path in paths:
        size = png_size(path)
        dims = "%dx%d" % size if size else "unknown size"
        lines.append("%s  (%s, %d bytes)" % (path, dims, os.path.getsize(path)))
        blocks.append(
            {
                "type": "resource_link",
                "uri": "file://" + path,
                "name": os.path.basename(path),
                "description": "Generated image",
                "mimeType": "image/png",
            }
        )

    header = "Generated %d image%s:" % (len(paths), "" if len(paths) == 1 else "s")
    note = (
        "\n\nThe model chose these dimensions; they cannot be requested on this path. "
        "Read the file to check the result before reporting it as done."
    )
    return {
        "content": [{"type": "text", "text": header + "\n" + "\n".join(lines) + note}] + blocks,
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
    report = [
        "Codex CLI    : %s" % (codex or "NOT INSTALLED - brew install --cask codex, or npm install -g @openai/codex"),
        "Codex auth   : %s" % (probe(["codex", "login", "status"]) or "unknown - run `codex login` yourself" if codex else "n/a"),
        "OpenAI CLI   : %s" % (probe(["openai", "--version"]) or "not installed (only needed for exact-size images)"),
        "OPENAI_API_KEY: %s" % ("set" if os.environ.get("OPENAI_API_KEY") else "not set (fine - the Codex path does not use it)"),
        "Scripts      : %s" % (SCRIPT_DIR if os.path.isdir(SCRIPT_DIR) else "MISSING at " + SCRIPT_DIR),
        "Server       : %s %s on python %s" % (SERVER_NAME, SERVER_VERSION, sys.version.split()[0]),
    ]
    ready = bool(codex) and os.path.isdir(SCRIPT_DIR)
    report.append("")
    report.append("Ready to generate images: %s" % ("yes" if ready else "no - fix the lines above"))
    return {
        "content": [{"type": "text", "text": "\n".join(report)}],
        "structuredContent": {"ready": ready, "codex_installed": bool(codex)},
    }


HANDLERS = {
    "generate_image": tool_generate_image,
    "ask_codex": tool_ask_codex,
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
        try:
            response = handle(message)
        except Exception as exc:
            log("handler crashed: %r" % exc)
            response = {"jsonrpc": "2.0", "id": message.get("id"),
                        "error": {"code": -32603, "message": "Internal error: %s" % exc}}
        if response is not None:
            out.write(json.dumps(response) + "\n")
            out.flush()
    log("stdin closed; exiting")


if __name__ == "__main__":
    main()
