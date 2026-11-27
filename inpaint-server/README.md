# ImageBridge inpaint server

A reference endpoint for the plugin's `remove_object`. One file, two
dependencies, and the thing the main README means by "any LaMa server with
that shape".

The plugin ships no default URL on purpose — whose machine runs the model is
your choice. That principle is worth nothing if there is nothing to choose, so
here is something to choose.

## Run it

```bash
pip install -r requirements.txt
python3 serve.py --model /path/to/lama.onnx --port 8421
```

Then point the plugin at it:

```bash
export IMAGEBRIDGE_INPAINT_URL=http://127.0.0.1:8421/
```

or put `"inpaint_url": "http://127.0.0.1:8421/"` in `~/.imagebridge/config.json`.

It binds to `127.0.0.1`. A public address takes an explicit `--host` and
wants TLS and a reverse proxy in front of it: anyone who can reach this can
spend your CPU, and it has no authentication of its own.

## The model

LaMa (Samsung AI / advimman, Apache-2.0) has been exported to ONNX many times
under many filenames, with more than one signature. Rather than name a file
and hope, **this server checks what you gave it** and tells you if the shape
is wrong:

```
this ONNX model is not a 512x512 LaMa export of the expected shape.
  expected inputs : {'image': [1, 3, 512, 512], 'mask': [1, 1, 512, 512]}, and an output named 'output'
  this model has  : {...}, outputs [...]
```

What it needs: `image` float32 `[1, 3, 512, 512]` with RGB scaled to 0..1,
`mask` float32 `[1, 1, 512, 512]` where **1 marks the hole**, and an output
named `output` of `[1, 3, 512, 512]`.

Published exports that are worth trying, licence and shape to be checked by
you before use — I have not verified every one against this server:

- <https://huggingface.co/Carve/LaMa-ONNX> (Apache-2.0)
- <https://huggingface.co/sjdkVSDH/LaMa-ONNX>
- <https://huggingface.co/sapienkit/LaMa-ONNX> — documents exactly this 512×512 contract
- <https://huggingface.co/othytenk/lama-int4-int8> — quantised, far smaller

MI-GAN (Picsart, MIT) is the other usual choice; it has a different signature,
so it needs a few lines changed here.

## The contract

```
POST /   body: 512x512 RGBA PNG, alpha 0 marks the hole
         -> 200, body: 512x512 RGB PNG, hole filled
GET  /   -> a line of text, for checking it is up
```

PNG decoding and encoding come from the plugin's own `mcp/imagelib.py`, which
is pure standard library — hence only `onnxruntime` and `numpy` here.

## Does it work?

Running this against the same picture as a separately hosted LaMa endpoint
produced a **bit-identical** result: `inspect_image` reported 0 pixels
changed between the two. That is the contract doing its job.
