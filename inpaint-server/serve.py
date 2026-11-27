#!/usr/bin/env python3
"""A reference inpainting endpoint for ImageBridge's remove_object.

The plugin ships no default URL on purpose: whose machine runs the model is
the user's choice. That principle is worth nothing if there is no server to
choose, so this is one - small enough to read in a sitting, and the thing the
README means by "any LaMa server with that shape".

The contract, both directions, is one PNG:

    POST /  body: 512x512 RGBA PNG, alpha 0 marks the hole
            -> 200, body: 512x512 RGB PNG with the hole filled

Run it:

    pip install -r requirements.txt
    python3 serve.py --model /path/to/lama-512-h.onnx --port 8421

then point the plugin at it:

    export IMAGEBRIDGE_INPAINT_URL=http://127.0.0.1:8421/

It binds to localhost by default. Putting it on a public address is a
decision with consequences - anyone who can reach it can spend your CPU - so
that takes an explicit --host, and a reverse proxy with TLS in front of it.

PNG encoding and decoding come from the plugin's own imagelib, which is pure
standard library, so the only dependencies here are onnxruntime and numpy.
"""

import argparse
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mcp"))
import imagelib  # noqa: E402

if sys.version_info < (3, 8):
    sys.stderr.write("this server needs Python 3.8 or newer; this is %s.\n"
                     % ".".join(str(n) for n in sys.version_info[:3]))
    raise SystemExit(1)

N = 512
MAX_BODY = 8 * 1024 * 1024


def build(model_path):
    # The file first, the imports second. Checking in the other order answers
    # a mistyped path with an ImportError, which is true but useless, and the
    # missing package deserves a sentence of its own rather than a traceback.
    if not os.path.isfile(model_path):
        sys.exit("model not found: %s\nSee README.md for which LaMa ONNX exports fit." % model_path)
    try:
        import numpy as np
        import onnxruntime as ort
    except ImportError as exc:
        sys.exit("%s\nInstall this server's dependencies first: "
                 "pip install -r requirements.txt" % exc)
    session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])

    # Check the model is the shape this server speaks, and say so plainly if it
    # is not. LaMa has been exported under many filenames and a few different
    # signatures, so "download the right file" is poor advice compared with a
    # server that reports what it was actually given.
    ins = {i.name: list(i.shape) for i in session.get_inputs()}
    outs = [o.name for o in session.get_outputs()]
    want = {"image": [1, 3, N, N], "mask": [1, 1, N, N]}
    if ins != want or "output" not in outs:
        sys.exit(
            "this ONNX model is not a %dx%d LaMa export of the expected shape.\n"
            "  expected inputs : %s, and an output named 'output'\n"
            "  this model has  : %s, outputs %s\n"
            "Use a LaMa export taking image [1,3,%d,%d] float32 0..1 and mask [1,1,%d,%d] where 1 "
            "marks the hole. See README.md." % (N, N, want, ins, outs, N, N, N, N))

    def infer(png):
        w, h, channels, px = imagelib.read_png_bytes(png)
        if (w, h) != (N, N):
            raise ValueError("expected a %dx%d image, got %dx%d" % (N, N, w, h))
        if channels != 4:
            raise ValueError("expected RGBA: alpha 0 is how the hole is marked")
        a = np.frombuffer(px, dtype=np.uint8).reshape(h, w, 4)
        image = a[:, :, :3].astype(np.float32).transpose(2, 0, 1)[None] / 255.0
        # 1 marks the hole, which is where alpha is 0.
        mask = (a[:, :, 3] == 0).astype(np.float32)[None, None]
        out = session.run(["output"], {"image": image, "mask": mask})[0]
        rgb = np.clip(out[0].transpose(1, 2, 0), 0, 255)
        if rgb.max() <= 1.001:          # some exports return 0..1, others 0..255
            rgb = rgb * 255.0
        rgb = rgb.round().astype(np.uint8)
        return imagelib.write_png(None, N, N, 3, rgb.tobytes())

    return infer


class Handler(BaseHTTPRequestHandler):
    server_version = "imagebridge-inpaint/1.0"
    infer = None

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return self.fail(413, "body must be a PNG of at most %d bytes" % MAX_BODY)
        try:
            filled = self.infer(self.rfile.read(length))
        except Exception as exc:
            return self.fail(400, str(exc))
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(filled)))
        self.end_headers()
        self.wfile.write(filled)

    def do_GET(self):
        # Something to curl while working out whether it is running at all.
        body = b"imagebridge inpaint server: POST a 512x512 RGBA PNG, alpha 0 = hole\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def fail(self, code, message):
        body = message.encode("utf-8", "replace")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *a):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % a))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", default=os.environ.get("LAMA_ONNX", ""),
                    help="path to lama-512-h.onnx (or $LAMA_ONNX)")
    ap.add_argument("--port", type=int, default=8421)
    ap.add_argument("--host", default="127.0.0.1",
                    help="default 127.0.0.1; a public address needs TLS in front of it")
    args = ap.parse_args()
    if not args.model:
        sys.exit("give --model /path/to/lama-512-h.onnx, or set LAMA_ONNX")

    Handler.infer = staticmethod(build(args.model))
    sys.stderr.write("listening on http://%s:%d/\n" % (args.host, args.port))
    HTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
