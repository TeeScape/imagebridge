#!/usr/bin/env bash
# ImageBridge test suite.
#
# Runs the whole image path against a STUB codex, so it costs nothing, needs no
# network and no ChatGPT quota, and can run in CI on Linux and macOS.
#
#   ./tests/run.sh
#
# Every test here exists because a real bug got through without it.

set -uo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SCRIPTS="$ROOT/skills/imagebridge/scripts"
PASS=0; FAIL=0

ok()   { PASS=$((PASS+1)); printf '  \033[32mok\033[0m   %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); printf '  \033[31mFAIL\033[0m %s\n' "$1"; [ -n "${2:-}" ] && printf '         %s\n' "$2"; }
is()   { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "expected [$3] got [$2]"; fi; }
# paths: $TMPDIR often ends in / so the expected value can carry a double slash
samepath() { a=$(printf '%s' "$2" | tr -s /); b=$(printf '%s' "$3" | tr -s /); is "$1" "$a" "$b"; }
# "contains", with both sides path-normalised
haspath() { a=$(printf '%s' "$2" | tr -s /); b=$(printf '%s' "$3" | tr -s /); has "$1" "$a" "$b"; }
# JSON: assert on the parsed value, never on spacing
jhas() { # name, json, python-expression over `d`
  if printf '%s' "$2" | python3 -c "import json,sys
try: d=json.load(sys.stdin)
except Exception: sys.exit(1)
sys.exit(0 if ($3) else 1)" 2>/dev/null; then ok "$1"; else bad "$1" "[$3] false for ${2:0:110}"; fi; }
has()  { case "$2" in *"$3"*) ok "$1";; *) bad "$1" "expected to contain [$3] in [${2:0:120}]";; esac; }
hasnt(){ case "$2" in *"$3"*) bad "$1" "should NOT contain [$3]";; *) ok "$1";; esac; }

# ---------------------------------------------------------------- stub codex
# STUB_MODE: ok | fail | silent   (silent = exit 0 but generate nothing)
make_stub() {
  STUB_DIR=$(mktemp -d "${TMPDIR:-/tmp}/ib-stub.XXXXXXXX")
  export CODEX_HOME="$STUB_DIR/codexhome"
  mkdir -p "$CODEX_HOME/generated_images"
  cat > "$STUB_DIR/codex" <<'STUB'
#!/usr/bin/env bash
case "${STUB_MODE:-ok}" in
  fail)   echo "stub: pretending codex failed" >&2; exit 3 ;;
  silent) echo "stub: produced nothing"; exit 0 ;;
esac
# fail_with_image: an image lands on disk AND codex reports failure. This is the
# case where a success-looking path used to be returned for a failed run.
sess="$CODEX_HOME/generated_images/stub-$$"
mkdir -p "$sess"
python3 - "$sess/exec-stub.${STUB_EXT:-png}" 64 64 0 <<'PY'
import sys, struct, zlib
path = sys.argv[1]
w = int(sys.argv[2]); h = int(sys.argv[3]); alpha = sys.argv[4] == '1'
px = bytes([0, 0, 0, 255]) if alpha else bytes([0, 0, 0])
raw = b''.join(bytes([0]) + px * w for _ in range(h))
def ch(t, d):
    return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
magic = bytes([137, 80, 78, 71, 13, 10, 26, 10])
ihdr = struct.pack('>IIBBBBB', w, h, 8, 6 if alpha else 2, 0, 0, 0)
open(path, 'wb').write(magic + ch(b'IHDR', ihdr) + ch(b'IDAT', zlib.compress(raw)) + ch(b'IEND', b''))
PY
echo "$sess/exec-stub.${STUB_EXT:-png}"
if [ "${STUB_MODE:-ok}" = "fail_with_image" ]; then
  echo "stub: produced an image, then failed" >&2
  exit 3
fi
exit 0
STUB
  chmod +x "$STUB_DIR/codex"
  export PATH="$STUB_DIR:$PATH"
}
make_openai_stub() {
  cat > "$STUB_DIR/openai" <<'OSTUB'
#!/usr/bin/env bash
# Minimal stand-in for `openai images edit`: writes a file where it was told to
# and prints the path, so the whole edit path can be tested without a paid call.
out=""; name="edited.png"
while [ $# -gt 0 ]; do
  case "$1" in
    --output-dir) out="$2"; shift 2 ;;
    --name) name="$2"; shift 2 ;;
    *) shift ;;
  esac
done
[ -z "$out" ] && out="."
mkdir -p "$out"
case "$name" in *.*) ;; *) name="$name.png" ;; esac
python3 - "$out/$name" 8 8 0 <<'PYP'
import sys, struct, zlib
path = sys.argv[1]
w = int(sys.argv[2]); h = int(sys.argv[3]); alpha = sys.argv[4] == '1'
px = bytes([0, 0, 0, 255]) if alpha else bytes([0, 0, 0])
raw = b''.join(bytes([0]) + px * w for _ in range(h))
def ch(t, d):
    return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
magic = bytes([137, 80, 78, 71, 13, 10, 26, 10])
ihdr = struct.pack('>IIBBBBB', w, h, 8, 6 if alpha else 2, 0, 0, 0)
open(path, 'wb').write(magic + ch(b'IHDR', ihdr) + ch(b'IDAT', zlib.compress(raw)) + ch(b'IEND', b''))
PYP
echo "Saved image to: $out/$name"
exit 0
OSTUB
  chmod +x "$STUB_DIR/openai"
}

make_png() { # path width height alpha(0|1)
  python3 - "$1" "$2" "$3" "$4" <<'PYP'
import sys, struct, zlib
path = sys.argv[1]
w = int(sys.argv[2]); h = int(sys.argv[3]); alpha = sys.argv[4] == '1'
px = bytes([0, 0, 0, 255]) if alpha else bytes([0, 0, 0])
raw = b''.join(bytes([0]) + px * w for _ in range(h))
def ch(t, d):
    return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
magic = bytes([137, 80, 78, 71, 13, 10, 26, 10])
ihdr = struct.pack('>IIBBBBB', w, h, 8, 6 if alpha else 2, 0, 0, 0)
open(path, 'wb').write(magic + ch(b'IHDR', ihdr) + ch(b'IDAT', zlib.compress(raw)) + ch(b'IEND', b''))
PYP
}

cleanup() { [ -n "${STUB_DIR:-}" ] && rm -rf "$STUB_DIR"; [ -n "${WORK:-}" ] && rm -rf "$WORK"; }
trap cleanup EXIT

echo "ImageBridge tests  ($(uname -s))"
echo
echo "syntax"
for f in "$SCRIPTS"/*; do
  if bash -n "$f" 2>/dev/null; then ok "bash -n $(basename "$f")"; else bad "bash -n $(basename "$f")"; fi
done
if python3 -c "import ast;ast.parse(open('$ROOT/mcp/imagebridge-server.py').read())" 2>/dev/null; then
  ok "python parses the MCP server"; else bad "python parses the MCP server"; fi
# Same invocation CI uses, so a contributor never learns about it from a red build.
if command -v shellcheck >/dev/null 2>&1; then
  if shellcheck -S warning "$SCRIPTS"/* "${BASH_SOURCE[0]}" >/dev/null 2>&1; then
    ok "shellcheck -S warning"
  else
    bad "shellcheck -S warning" "$(shellcheck -S warning "$SCRIPTS"/* "${BASH_SOURCE[0]}" 2>&1 | head -4)"
  fi
else
  printf '  \033[33mskip\033[0m shellcheck (not installed; CI runs it)\n'
fi

make_stub
WORK=$(mktemp -d "${TMPDIR:-/tmp}/ib-work.XXXXXXXX")

echo
echo "codex-image"
out=$(bash "$SCRIPTS/codex-image" "a blue folder icon" -o "$WORK/a.png" --dry-run 2>&1); rc=$?
is   "--dry-run exits 0"                "$rc" "0"
has  "--dry-run shows the codex argv"   "$out" "command: codex exec"
[ -e "$WORK/a.png" ] && bad "--dry-run writes nothing" || ok "--dry-run writes nothing"

out=$(STUB_MODE=ok bash "$SCRIPTS/codex-image" "a blue folder icon" -o "$WORK/a.png" 2>/dev/null); rc=$?
is   "success exits 0"                  "$rc" "0"
samepath "success prints the path it wrote" "$out" "$WORK/a.png"
[ -f "$WORK/a.png" ] && ok "the file exists" || bad "the file exists"

# regression: a failed generation used to exit 0 and print a path
out=$(STUB_MODE=fail bash "$SCRIPTS/codex-image" "x" -o "$WORK/b.png" 2>/dev/null); rc=$?
[ "$rc" -ne 0 ] && ok "codex failure is fatal (exit $rc)" || bad "codex failure is fatal" "exit 0 with [$out]"
[ -f "$WORK/b.png" ] && bad "no file is written on failure" || ok "no file is written on failure"

# regression: codex failing *after* writing an image used to be read as success
out=$(STUB_MODE=fail_with_image bash "$SCRIPTS/codex-image" "x" -o "$WORK/d.png" 2>/dev/null); rc=$?
[ "$rc" -ne 0 ] && ok "a failure that still produced a file is fatal" || bad "a failure that still produced a file is fatal" "exit 0 with [$out]"
[ -f "$WORK/d.png" ] && bad "that file is not delivered" || ok "that file is not delivered"

# the empty-xargs hazard is Linux-only, so assert the guard exists by construction
if grep -q 'print0 .*| *xargs' "$SCRIPTS/codex-image"; then
  bad "find never pipes straight into xargs (Linux CWD hazard)"
else ok "find never pipes straight into xargs (Linux CWD hazard)"; fi
if grep -q 'if \[ -s "\$RAW" \]' "$SCRIPTS/codex-image"; then
  ok "the match list is tested before xargs runs"
else bad "the match list is tested before xargs runs"; fi

# regression: on Linux an empty xargs used to list the CWD and copy a project file
cd "$WORK" || exit 1; : > innocent-project-file.txt
out=$(STUB_MODE=silent bash "$SCRIPTS/codex-image" "x" -o "$WORK/c.png" 2>/dev/null); rc=$?
[ "$rc" -ne 0 ] && ok "no output is fatal, not a fake success" || bad "no output is fatal" "exit 0 with [$out]"
hasnt "never copies an unrelated project file" "$out" "innocent-project-file"
cd "$ROOT" || exit 1

# regression: -o said "exact path" but forked into -2, -3 ...
STUB_MODE=ok bash "$SCRIPTS/codex-image" "x" -o "$WORK/same.png" >/dev/null 2>&1
out=$(STUB_MODE=ok bash "$SCRIPTS/codex-image" "x" -o "$WORK/same.png" 2>/dev/null)
samepath "-o overwrites instead of making -2" "$out" "$WORK/same.png"
[ -e "$WORK/same-2.png" ] && bad "no stray -2 file" || ok "no stray -2 file"
out=$(STUB_MODE=ok bash "$SCRIPTS/codex-image" "x" -o "$WORK/same.png" --no-clobber 2>/dev/null)
has  "--no-clobber still protects"        "$out" "same-2.png"

# regression: `-o name` with no extension produced name.name
out=$(STUB_MODE=ok bash "$SCRIPTS/codex-image" "x" -o "$WORK/noext" 2>/dev/null)
hasnt "-o without an extension is not doubled" "$out" "noext.noext"
has   "-o without an extension gets the real one" "$out" "noext.png"

# regression: a Thai description always slugged to the generic name
out=$(STUB_MODE=ok bash "$SCRIPTS/codex-image" "ไอคอนโฟลเดอร์สีฟ้า แบน" -d "$WORK" 2>/dev/null)
hasnt "a Thai description keeps its words" "$out" "codex-image.png"

# editing on the free path: the reference must reach referenced_image_paths via
# the prompt, not `codex exec -i`, which is a different mechanism
make_png "$WORK/src.png" 64 64 0
out=$(bash "$SCRIPTS/codex-image" "make it green" -r "$WORK/src.png" --dry-run 2>&1)
has   "an edit names the file for referenced_image_paths" "$out" "referenced_image_paths"
# Compare by file identity, not by spelling: on macOS /var/folders is a symlink
# to /private/var/folders and different shells report different forms, so a
# string comparison here is a platform coin-flip. `-ef` asks the only question
# that matters — does the path Codex is given point at the file we meant?
ref_line=$(printf '%s\n' "$out" | sed -n 's/^referenced: //p' | head -1)
if [ -n "$ref_line" ] && [ "$ref_line" -ef "$WORK/src.png" ]; then
  ok "the referenced path points at the right file"
else
  bad "the referenced path points at the right file" "dry-run said: $(printf '%s' "$out" | head -3 | tr '\n' '|')"
fi
case "$ref_line" in /*) ok "the referenced path is absolute";; *) bad "the referenced path is absolute" "[$ref_line]";; esac
hasnt "an edit does not attach with -i"                   "$out" " -i "
out=$(bash "$SCRIPTS/codex-image" "x" --from-last 3 --dry-run 2>&1)
has   "--from-last is reported by the dry run"            "$out" "from-last: 3"
has   "--from-last drives num_last_images_to_include"     "$out" "num_last_images_to_include"
out=$(bash "$SCRIPTS/codex-image" "x" -r "$WORK/src.png" --from-last 2 --dry-run 2>&1); rc=$?
[ "$rc" -ne 0 ] && ok "--ref and --from-last together are refused" || bad "--ref and --from-last together are refused"
out=$(bash "$SCRIPTS/codex-image" "x" --from-last 9 --dry-run 2>&1); rc=$?
[ "$rc" -ne 0 ] && ok "--from-last outside 1-5 is refused" || bad "--from-last outside 1-5 is refused"

echo
echo "imagelib (stdlib PNG decode / resize / encode)"
make_png "$WORK/big.png"   64 64 0
make_png "$WORK/biga.png"  64 64 1
printf 'not a png' > "$WORK/bad.png"
il() { python3 -c "
import sys; sys.path.insert(0, '$ROOT/mcp')
import imagelib as L
$1" 2>&1; }

r=$(il "w,h,c,px = L.read_png('$WORK/big.png'); print(w,h,c,len(px))")
is "reads an RGB png"            "$r" "64 64 3 12288"
r=$(il "w,h,c,px = L.read_png('$WORK/biga.png'); print(w,h,c,len(px))")
is "reads an RGBA png"           "$r" "64 64 4 16384"
r=$(il "print(L.fit(1254,1254,384), L.fit(1536,1024,384), L.fit(100,80,384))")
is "fit keeps aspect, never enlarges" "$r" "(384, 384) (384, 256) (100, 80)"
r=$(il "b,w,h = L.preview_base64('$WORK/big.png', 32); print(w,h,b[:8])")
has "preview downsamples"        "$r" "32 32 iVBORw0K"
r=$(il "
import base64, io
b,w,h = L.preview_base64('$WORK/biga.png', 32)
raw = base64.b64decode(b)
w2,h2,c2,_ = L.read_png_bytes(raw) if hasattr(L,'read_png_bytes') else (0,0,0,0)
print('roundtrip-ok' if raw[:8] == L.MAGIC else 'bad-magic')")
has "preview is a real png"      "$r" "roundtrip-ok"
r=$(il "
try:
    L.read_png('$WORK/bad.png'); print('no-error')
except L.Unsupported as e: print('Unsupported:', e)")
has "a non-png raises Unsupported" "$r" "Unsupported: not a PNG"
r=$(il "
L.resize_file('$WORK/big.png', 16)
w,h,_,_ = L.read_png('$WORK/big.png'); print(w,h)")
is "resize_file shrinks in place" "$r" "16 16"
r=$(il "
o = L.resize_file('$WORK/biga.png', 999); print(o[0]==o[2] and o[1]==o[3])")
is "resize_file never enlarges"   "$r" "True"

echo
echo "openai-edit (stubbed; no paid call, no key)"
make_openai_stub
make_png "$WORK/src.png"      64 64 0
make_png "$WORK/mask-ok.png"  64 64 1
make_png "$WORK/mask-big.png" 96 96 1
make_png "$WORK/mask-noa.png" 64 64 0
printf 'not a png' > "$WORK/notpng.png"

rej() { # name, expected-substring, args...
  local n="$1" want="$2"; shift 2
  local o; o=$(bash "$SCRIPTS/openai-edit" "$@" 2>&1); local rc=$?
  if [ "$rc" -eq 0 ]; then bad "$n" "accepted, should have been rejected"
  else case "$o" in *"$want"*) ok "$n";; *) bad "$n" "wrong reason: $(printf '%s' "$o" | head -1)";; esac; fi
}
rej "a missing image is rejected"        "image not found"       "x" -i "$WORK/nope.png" --dry-run
rej "a mask that is not a PNG"           "must be a PNG"         "x" -i "$WORK/src.png" -m "$WORK/notpng.png" --dry-run
rej "a mask with no alpha channel"       "no alpha channel"      "x" -i "$WORK/src.png" -m "$WORK/mask-noa.png" --dry-run
rej "a mask of the wrong dimensions"     "same dimensions"       "x" -i "$WORK/src.png" -m "$WORK/mask-big.png" --dry-run
many=(); for _ in $(seq 17); do many+=(-i "$WORK/src.png"); done
rej "more than 16 images"                "at most 16"            "x" "${many[@]}" --dry-run

out=$(bash "$SCRIPTS/openai-edit" "make it blue" -i "$WORK/src.png" -m "$WORK/mask-ok.png" --dry-run 2>&1); rc=$?
is  "a valid mask is accepted" "$rc" "0"
has "the dry run passes --mask through" "$out" "--mask"
has "the dry run passes the prompt"     "$out" "make"

out=$(OPENAI_API_KEY=test-key bash "$SCRIPTS/openai-edit" "make it blue" \
        -i "$WORK/src.png" -m "$WORK/mask-ok.png" -o "$WORK/edited.png" 2>/dev/null); rc=$?
is "a stubbed edit exits 0" "$rc" "0"
samepath "a stubbed edit prints the path it wrote" "$out" "$WORK/edited.png"
[ -f "$WORK/edited.png" ] && ok "the edited file exists" || bad "the edited file exists"
[ -f "$WORK/src.png" ] && ok "the original is kept" || bad "the original is kept"

echo
echo "MCP server"
rpc() {
  printf '%s\n' "$@" | python3 "$ROOT/mcp/imagebridge-server.py" 2>/dev/null
}
INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}'
r=$(rpc "$INIT" | head -1)
jhas "initialize answers with the asked version" "$r" "d['result']['protocolVersion']=='2025-06-18'" 
r=$(rpc "$INIT" '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | tail -1)
jhas "tools/list has generate_image" "$r" "'generate_image' in [t['name'] for t in d['result']['tools']]" 
r=$(rpc "$INIT" '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"nope","arguments":{}}}' | tail -1)
jhas "unknown tool is a protocol error" "$r" "d['error']['code']==-32602" 
r=$(rpc "$INIT" '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"generate_image","arguments":{}}}' | tail -1)
jhas "a bad argument is isError, not a crash" "$r" "d['result'].get('isError') is True" 
r=$(rpc "$INIT" 'not json' | tail -1)
jhas "malformed JSON is a parse error" "$r" "d['error']['code']==-32700" 
# regression: a JSON array used to kill the process and lose the rest of the session
r=$(rpc "$INIT" '[1,2,3]' '{"jsonrpc":"2.0","id":9,"method":"ping"}' | tail -1)
jhas "an array does not kill the server" "$r" "d['id']==9" 
r=$(rpc "$INIT" '{"jsonrpc":"2.0","id":7,"method":"tools/list"}' | tail -1)
jhas "tools/list now offers edit_image" "$r" "'edit_image' in [t['name'] for t in d['result']['tools']]"
r=$(rpc "$INIT" '{"jsonrpc":"2.0","id":10,"method":"tools/call","params":{"name":"edit_image","arguments":{"prompt":"x"}}}' | tail -1)
jhas "edit_image without images is isError" "$r" "d['result'].get('isError') is True"
# the default provider is the free Codex path
r=$(rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":11,\"method\":\"tools/call\",\"params\":{\"name\":\"edit_image\",\"arguments\":{\"prompt\":\"make it green\",\"images\":[\"$WORK/src.png\"],\"output_path\":\"$WORK/mcp-edit-free.png\"}}}" | tail -1)
jhas "edit_image defaults to the free Codex path" "$r" "d['result']['structuredContent']['provider']=='codex'"
jhas "the free edit returns a path"              "$r" "len(d['result']['structuredContent']['paths'])==1"
jhas "the free edit says there is no API charge" "$r" "'no API charge' in d['result']['content'][0]['text']"

# a mask is a paid-path feature, and asking for it free must explain, not fail silently
r=$(rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":12,\"method\":\"tools/call\",\"params\":{\"name\":\"edit_image\",\"arguments\":{\"prompt\":\"x\",\"images\":[\"$WORK/src.png\"],\"mask\":\"$WORK/mask-ok.png\"}}}" | tail -1)
jhas "a mask on the free path is refused clearly" "$r" "d['result'].get('isError') is True and 'bills per call' in d['result']['content'][0]['text']"

# the paid path still works when asked for explicitly
r=$(OPENAI_API_KEY=test-key rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":13,\"method\":\"tools/call\",\"params\":{\"name\":\"edit_image\",\"arguments\":{\"provider\":\"api\",\"prompt\":\"make it blue\",\"images\":[\"$WORK/src.png\"],\"mask\":\"$WORK/mask-ok.png\",\"output_path\":\"$WORK/mcp-edited.png\"}}}" | tail -1)
jhas "provider=api still reaches the paid path" "$r" "d['result']['structuredContent']['provider']=='api'"
jhas "the paid path says it is billed"          "$r" "'BILLED' in d['result']['content'][0]['text']"

r=$(rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":14,\"method\":\"tools/call\",\"params\":{\"name\":\"generate_image\",\"arguments\":{\"prompt\":\"x\",\"output_path\":\"$WORK/prev.png\",\"return_image\":true}}}" | tail -1)
jhas "return_image adds an image block" "$r" "any(c['type']=='image' for c in d['result']['content'])"
jhas "the image block is a png"          "$r" "[c for c in d['result']['content'] if c['type']=='image'][0]['mimeType']=='image/png'"
r=$(rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":15,\"method\":\"tools/call\",\"params\":{\"name\":\"generate_image\",\"arguments\":{\"prompt\":\"x\",\"output_path\":\"$WORK/small.png\",\"max_dimension\":16}}}" | tail -1)
jhas "max_dimension reports the resize"  "$r" "'resized' in d['result']['content'][0]['text']"
sz=$(python3 -c "
import sys; sys.path.insert(0,'$ROOT/mcp'); import imagelib as L
w,h,_,_ = L.read_png('$WORK/small.png'); print(w,h)" 2>/dev/null)
is "max_dimension really shrank the file" "$sz" "16 16"
r=$(rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":16,\"method\":\"tools/call\",\"params\":{\"name\":\"generate_image\",\"arguments\":{\"prompt\":\"x\",\"output_path\":\"$WORK/plain.png\"}}}" | tail -1)
jhas "no preview unless asked"           "$r" "not any(c['type']=='image' for c in d['result']['content'])"

r=$(rpc "$INIT" '{"jsonrpc":"2.0","method":"notifications/initialized"}' '{"jsonrpc":"2.0","id":8,"method":"ping"}')
is "a notification gets no reply" "$(printf '%s' "$r" | grep -c '"id"')" "2"

echo
echo
echo "reference inpaint server (no onnxruntime needed for these)"
python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$ROOT/inpaint-server/serve.py" \
  && ok "serve.py parses" || bad "serve.py parses"
r=$(cd "$ROOT/inpaint-server" && python3 serve.py 2>&1; echo "rc=$?")
has "it refuses to start with no model"  "$r" "give --model"
has "and exits non-zero"                 "$r" "rc=1"
r=$(cd "$ROOT/inpaint-server" && python3 serve.py --model /nope/missing.onnx 2>&1)
has "a missing model file is named"      "$r" "model not found"
# A machine without the dependencies installed should get a sentence, not a
# traceback - deterministic here via a stub that raises on import.
STUB=$(mktemp -d "${TMPDIR:-/tmp}/ib-stub.XXXXXX")
printf 'raise ImportError("No module named %s")\n' "'onnxruntime'" > "$STUB/onnxruntime.py"
r=$(cd "$ROOT/inpaint-server" && PYTHONPATH="$STUB" python3 serve.py --model "$STUB/onnxruntime.py" 2>&1)
has "a missing dependency says what to run" "$r" "pip install -r requirements.txt"
rm -rf "$STUB"

echo
echo "inspect_image (measurement only; no model, no network)"
box() { # path, box-colour-or-none
python3 - "$1" "$2" "$ROOT" <<'MK'
import sys; sys.path.insert(0, sys.argv[3] + "/mcp"); import imagelib as L
path, col = sys.argv[1], sys.argv[2]
w = h = 64
px = bytearray([200, 200, 200] * (w * h))
if col != "none":
    r, g, b = (int(col[i:i+2], 16) for i in (0, 2, 4))
    for y in range(10, 30):
        for x in range(20, 50):
            o = (y * w + x) * 3
            px[o], px[o+1], px[o+2] = r, g, b
L.write_png(path, w, h, 3, bytes(px))
MK
}
box "$WORK/flat.png" none
box "$WORK/boxed.png" 1040c0
box "$WORK/boxed2.png" 1040c8   # within tolerance of boxed.png

r=$(rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":30,\"method\":\"tools/call\",\"params\":{\"name\":\"inspect_image\",\"arguments\":{\"image\":\"$WORK/boxed.png\"}}}" | tail -1)
jhas "inspect reports the size"          "$r" "d['result']['structuredContent']['width']==64"
jhas "it names the dominant colour"      "$r" "d['result']['structuredContent']['dominant_colours'][0]['rgb']==[200,200,200]"
jhas "no alpha is reported as such"      "$r" "d['result']['structuredContent']['has_alpha'] is False"

r=$(rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":31,\"method\":\"tools/call\",\"params\":{\"name\":\"inspect_image\",\"arguments\":{\"image\":\"$WORK/boxed.png\",\"compare_to\":\"$WORK/flat.png\"}}}" | tail -1)
jhas "it counts the changed pixels"      "$r" "d['result']['structuredContent']['comparison']['changed_pixels']==600"
jhas "the changed box is exact"          "$r" "d['result']['structuredContent']['comparison']['changed_box']==[20,10,30,20]"

r=$(rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":32,\"method\":\"tools/call\",\"params\":{\"name\":\"inspect_image\",\"arguments\":{\"image\":\"$WORK/boxed.png\",\"compare_to\":\"$WORK/boxed2.png\"}}}" | tail -1)
jhas "encoding noise is not an edit"     "$r" "d['result']['structuredContent']['comparison']['changed_pixels']==0"
jhas "and it says so plainly"            "$r" "'it did not' in d['result']['content'][0]['text']"

r=$(rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":33,\"method\":\"tools/call\",\"params\":{\"name\":\"inspect_image\",\"arguments\":{\"image\":\"$WORK/boxed.png\",\"compare_to\":\"$WORK/boxed2.png\",\"tolerance\":0}}}" | tail -1)
jhas "tolerance 0 sees it"               "$r" "d['result']['structuredContent']['comparison']['changed_pixels']==600"

make_png "$WORK/wrongsize.png" 96 96 0
r=$(rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":34,\"method\":\"tools/call\",\"params\":{\"name\":\"inspect_image\",\"arguments\":{\"image\":\"$WORK/boxed.png\",\"compare_to\":\"$WORK/wrongsize.png\"}}}" | tail -1)
jhas "mismatched sizes are refused"      "$r" "d['result'].get('isError') is True and 'cannot be compared' in d['result']['content'][0]['text']"

make_png "$WORK/alpha.png" 64 64 1
r=$(rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":35,\"method\":\"tools/call\",\"params\":{\"name\":\"inspect_image\",\"arguments\":{\"image\":\"$WORK/alpha.png\"}}}" | tail -1)
jhas "alpha is detected"                 "$r" "d['result']['structuredContent']['has_alpha'] is True"

echo
echo "install.sh (standalone install, into a throwaway HOME)"
FAKE=$(mktemp -d "${TMPDIR:-/tmp}/ib-home.XXXXXX")
# No claude on PATH: the script must still link everything and SAY that the
# MCP server - where three of the six tools live - was not registered.
r=$(cd "$ROOT" && env HOME="$FAKE" PATH="$(dirname "$(command -v python3)"):/usr/bin:/bin" bash install.sh 2>&1)
[ -L "$FAKE/bin/codex-image" ] && ok "it links the scripts into ~/bin" || bad "it links the scripts into ~/bin"
[ -L "$FAKE/.claude/skills/imagebridge" ] && ok "and the skill into ~/.claude/skills" || bad "and the skill into ~/.claude/skills"
has "it names the MCP server when it cannot register it" "$r" "claude mcp add imagebridge"
has "and says what is missing without it"                "$r" "inspect_image"
(cd "$ROOT" && env HOME="$FAKE" PATH="$(dirname "$(command -v python3)"):/usr/bin:/bin" bash install.sh >/dev/null 2>&1)
[ -L "$FAKE/bin/codex-image" ] && ok "running it twice is safe" || bad "running it twice is safe"
rm -rf "$FAKE"

echo
echo "fit_image (framing, offline, no model)"
r=$(rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":50,\"method\":\"tools/call\",\"params\":{\"name\":\"fit_image\",\"arguments\":{\"image\":\"$WORK/boxed.png\",\"width\":100,\"output_path\":\"$WORK/fit.png\"}}}" | tail -1)
jhas "it finds the content and drops the border" "$r" "d['result']['structuredContent']['content_box']==[20,10,30,20]"
jhas "the canvas is exactly what was asked"      "$r" "d['result']['structuredContent']['width']==100 and d['result']['structuredContent']['height']==100"
r=$(rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":51,\"method\":\"tools/call\",\"params\":{\"name\":\"fit_image\",\"arguments\":{\"image\":\"$WORK/boxed.png\",\"width\":40,\"height\":80,\"trim\":false,\"output_path\":\"$WORK/fit-notrim.png\"}}}" | tail -1)
jhas "trim:false keeps the whole frame"          "$r" "d['result']['structuredContent']['content_box']==[0,0,64,64]"
jhas "and a non-square canvas is honoured"       "$r" "[d['result']['structuredContent']['width'],d['result']['structuredContent']['height']]==[40,80]"
r=$(rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":52,\"method\":\"tools/call\",\"params\":{\"name\":\"fit_image\",\"arguments\":{\"image\":\"$WORK/boxed.png\",\"width\":50,\"background\":\"transparent\",\"output_path\":\"$WORK/fit-tr.png\"}}}" | tail -1)
jhas "transparent padding is accepted"           "$r" "d['result'].get('isError') is not True"
r=$(python3 -c "
import sys; sys.path.insert(0,'$ROOT/mcp'); import imagelib as L
w,h,c,px = L.read_png('$WORK/fit-tr.png')
print('rgba' if c==4 else 'opaque', 'clear' if L._alpha(px,0,c)==0 else 'filled')")
has "it gains an alpha channel"                  "$r" "rgba"
has "and the padding really is clear"            "$r" "clear"
r=$(rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":53,\"method\":\"tools/call\",\"params\":{\"name\":\"fit_image\",\"arguments\":{\"image\":\"$WORK/boxed.png\",\"width\":64,\"background\":\"blue\"}}}" | tail -1)
jhas "a colour name is refused, hex is not"      "$r" "d['result'].get('isError') is True and 'rrggbb' in d['result']['content'][0]['text']"
r=$(rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":54,\"method\":\"tools/call\",\"params\":{\"name\":\"fit_image\",\"arguments\":{\"image\":\"$WORK/boxed.png\",\"width\":0}}}" | tail -1)
jhas "a zero canvas is refused"                  "$r" "d['result'].get('isError') is True"
r=$(rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":55,\"method\":\"tools/call\",\"params\":{\"name\":\"fit_image\",\"arguments\":{\"image\":\"$WORK/flat.png\",\"width\":64}}}" | tail -1)
jhas "a blank image says there is nothing to trim" "$r" "d['result'].get('isError') is True and 'nothing to trim' in d['result']['content'][0]['text']"
[ -f "$WORK/boxed.png" ] && ok "the original is left alone" || bad "the original is left alone"

echo
echo "remove_object (stub inpaint server; no network, no model)"
# A stand-in for the LaMa endpoint: answers any POST with a solid 512 PNG, so
# the whole path - mask, downscale, HTTP, compose - runs without a model.
cat > "$WORK/stub_inpaint.py" <<'SRV'
import struct, zlib, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
def png(n=512):
    raw = b''.join(bytes([0]) + bytes([9, 99, 199]) * n for _ in range(n))
    def ch(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    return (bytes([137, 80, 78, 71, 13, 10, 26, 10])
            + ch(b'IHDR', struct.pack('>IIBBBBB', n, n, 8, 2, 0, 0, 0))
            + ch(b'IDAT', zlib.compress(raw)) + ch(b'IEND', b''))
BODY = png()
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length', 0)))
        self.send_response(200); self.send_header('Content-Type', 'image/png')
        self.send_header('Content-Length', str(len(BODY))); self.end_headers()
        self.wfile.write(BODY)
    def log_message(self, *a): pass
HTTPServer(('127.0.0.1', int(sys.argv[1])), H).serve_forever()
SRV
PORT=8731
python3 "$WORK/stub_inpaint.py" "$PORT" & STUB_PID=$!
for _ in 1 2 3 4 5 6 7 8 9 10; do
  python3 -c "import socket,sys; s=socket.socket(); sys.exit(0 if s.connect_ex(('127.0.0.1',$PORT))==0 else 1)" 2>/dev/null && break
  python3 -c "import time; time.sleep(.25)"
done
make_png "$WORK/scene.png" 64 64 0
export IMAGEBRIDGE_INPAINT_URL="http://127.0.0.1:$PORT/"

r=$(rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":20,\"method\":\"tools/call\",\"params\":{\"name\":\"remove_object\",\"arguments\":{\"image\":\"$WORK/scene.png\",\"regions\":[[10,10,20,20]],\"output_path\":\"$WORK/scene-out.png\"}}}" | tail -1)
jhas "remove_object writes a file"      "$r" "d['result']['structuredContent']['path'].endswith('scene-out.png')"
jhas "it reports the pixels removed"    "$r" "d['result']['structuredContent']['removed_pixels']==400"
[ -f "$WORK/scene-out.png" ] && ok "the output exists" || bad "the output exists"
[ -f "$WORK/scene.png" ] && ok "the original is untouched" || bad "the original is untouched"
r=$(python3 -c "
import sys; sys.path.insert(0,'$ROOT/mcp'); import imagelib as L
w,h,c,px = L.read_png('$WORK/scene-out.png')
inside  = px[(20*w+20)*c:(20*w+20)*c+3]
outside = px[(60*w+60)*c:(60*w+60)*c+3]
print('fill' if tuple(inside)==(9,99,199) else 'not-filled', 'kept' if tuple(outside)!=(9,99,199) else 'overwritten')" 2>&1)
has "the hole takes the server's fill"  "$r" "fill"
has "outside the hole is left alone"    "$r" "kept"

r=$(rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":21,\"method\":\"tools/call\",\"params\":{\"name\":\"remove_object\",\"arguments\":{\"image\":\"$WORK/scene.png\"}}}" | tail -1)
jhas "needs regions or a mask"          "$r" "d['result'].get('isError') is True and 'exactly one' in d['result']['content'][0]['text']"
r=$(rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":22,\"method\":\"tools/call\",\"params\":{\"name\":\"remove_object\",\"arguments\":{\"image\":\"$WORK/scene.png\",\"regions\":[[900,900,10,10]]}}}" | tail -1)
jhas "a region outside the image fails" "$r" "d['result'].get('isError') is True"

r=$(python3 - "$ROOT" <<'MK'
import sys; sys.path.insert(0, sys.argv[1] + "/mcp"); import imagelib as L
W = 1200
one  = L.windows(L.rect_mask(W, W, [[500, 500, 100, 100]]), W, W)
two  = L.windows(L.rect_mask(W, W, [[80, 80, 50, 50], [1000, 1000, 50, 50]]), W, W)
near = L.windows(L.rect_mask(W, W, [[80, 80, 50, 50], [150, 80, 50, 50]]), W, W)
all_ = L.windows(L.rect_mask(W, W, [[5, 5, 1190, 1190]]), W, W)
print("one", len(one), one[0][2] < W // 3)
print("two", len(two))
print("near", len(near))
print("all", len(all_), all_[0] == [0, 0, W, W])
MK
)
has "a small hole gets a small window"   "$r" "one 1 True"
has "far-apart holes get one window each" "$r" "two 2"
has "holes close together merge"          "$r" "near 1"
has "a hole covering it all is one frame" "$r" "all 1 True"

make_png "$WORK/wide.png" 300 300 0
r=$(rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":24,\"method\":\"tools/call\",\"params\":{\"name\":\"remove_object\",\"arguments\":{\"image\":\"$WORK/wide.png\",\"regions\":[[20,20,20,20],[250,250,20,20]],\"output_path\":\"$WORK/wide-out.png\"}}}" | tail -1)
jhas "two holes are filled in two passes" "$r" "len(d['result']['structuredContent']['windows'])==2"
jhas "and the window stays off full frame" "$r" "max(w[2] for w in d['result']['structuredContent']['windows'])<300"
r=$(python3 -c "
import sys; sys.path.insert(0,'$ROOT/mcp'); import imagelib as L
w,h,c,px = L.read_png('$WORK/wide-out.png')
a = tuple(px[(25*w+25)*c:(25*w+25)*c+3]); b = tuple(px[(255*w+255)*c:(255*w+255)*c+3])
m = tuple(px[(150*w+150)*c:(150*w+150)*c+3])
print('both' if a==(9,99,199) and b==(9,99,199) else 'missed', 'between-kept' if m!=(9,99,199) else 'clobbered')")
has "both holes get filled"                "$r" "both"
has "the gap between them is untouched"    "$r" "between-kept"

r=$(IMAGEBRIDGE_CA_BUNDLE=/nonexistent/ca.pem rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":25,\"method\":\"tools/call\",\"params\":{\"name\":\"remove_object\",\"arguments\":{\"image\":\"$WORK/scene.png\",\"regions\":[[10,10,20,20]]}}}" | tail -1)
jhas "a missing ca_bundle is named"       "$r" "d['result'].get('isError') is True and 'ca_bundle does not exist' in d['result']['content'][0]['text']"

cat > "$WORK/cfg.json" <<'CFG'
["not", "an", "object"]
CFG
r=$(IMAGEBRIDGE_CONFIG="$WORK/cfg.json" rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":26,\"method\":\"tools/call\",\"params\":{\"name\":\"remove_object\",\"arguments\":{\"image\":\"$WORK/scene.png\",\"regions\":[[10,10,20,20]]}}}" | tail -1)
jhas "a config that is not an object is ignored" "$r" "d['result'].get('isError') is not True"

# Growing a region from a point, instead of drawing a box round it.
box "$WORK/sel.png" 1040c0          # a 30x20 blue box at (20,10) on grey
r=$(rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":40,\"method\":\"tools/call\",\"params\":{\"name\":\"remove_object\",\"arguments\":{\"image\":\"$WORK/sel.png\",\"points\":[[35,20]],\"grow\":0,\"output_path\":\"$WORK/sel-out.png\"}}}" | tail -1)
jhas "a point selects the shape exactly" "$r" "d['result']['structuredContent']['removed_pixels']==600"
r=$(rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":41,\"method\":\"tools/call\",\"params\":{\"name\":\"remove_object\",\"arguments\":{\"image\":\"$WORK/sel.png\",\"points\":[[35,20]],\"grow\":2,\"output_path\":\"$WORK/sel-g.png\"}}}" | tail -1)
jhas "grow swallows the antialiased edge" "$r" "d['result']['structuredContent']['removed_pixels']>600"
r=$(rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":42,\"method\":\"tools/call\",\"params\":{\"name\":\"remove_object\",\"arguments\":{\"image\":\"$WORK/sel.png\",\"points\":[[2,2]]}}}" | tail -1)
jhas "a seed in the background is refused" "$r" "d['result'].get('isError') is True and 'leaked out of the object' in d['result']['content'][0]['text']"
r=$(rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":43,\"method\":\"tools/call\",\"params\":{\"name\":\"remove_object\",\"arguments\":{\"image\":\"$WORK/sel.png\",\"points\":[[900,900]]}}}" | tail -1)
jhas "a point outside the image is refused" "$r" "d['result'].get('isError') is True and 'outside' in d['result']['content'][0]['text']"
r=$(rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":44,\"method\":\"tools/call\",\"params\":{\"name\":\"remove_object\",\"arguments\":{\"image\":\"$WORK/sel.png\",\"points\":[[35,20]],\"regions\":[[1,1,4,4]]}}}" | tail -1)
jhas "points and regions together refused" "$r" "d['result'].get('isError') is True and 'exactly one' in d['result']['content'][0]['text']"
r=$(python3 -c "
import sys; sys.path.insert(0,'$ROOT/mcp'); import imagelib as L
w,h,c,px = L.read_png('$WORK/sel-out.png')
inside = tuple(px[(20*w+35)*c:(20*w+35)*c+3]); outside = tuple(px[(50*w+10)*c:(50*w+10)*c+3])
print('filled' if inside==(9,99,199) else 'not-filled', 'kept' if outside!=(9,99,199) else 'clobbered')")
has "the selected shape is filled"        "$r" "filled"
has "the rest of the picture is kept"     "$r" "kept"

# A JPEG, the format a photograph actually arrives in. Skipped where the
# machine has no converter, because then there is nothing to test.
if command -v sips >/dev/null 2>&1 || command -v ffmpeg >/dev/null 2>&1 || command -v magick >/dev/null 2>&1 || command -v convert >/dev/null 2>&1; then
  if command -v sips >/dev/null 2>&1; then
    sips -s format jpeg "$WORK/scene.png" --out "$WORK/scene.jpg" >/dev/null 2>&1
  else
    ffmpeg -v error -y -i "$WORK/scene.png" "$WORK/scene.jpg" >/dev/null 2>&1
  fi
  r=$(rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":27,\"method\":\"tools/call\",\"params\":{\"name\":\"inspect_image\",\"arguments\":{\"image\":\"$WORK/scene.jpg\"}}}" | tail -1)
  jhas "a JPEG can be measured"           "$r" "d['result']['structuredContent']['width']==64"
  r=$(rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":28,\"method\":\"tools/call\",\"params\":{\"name\":\"remove_object\",\"arguments\":{\"image\":\"$WORK/scene.jpg\",\"regions\":[[10,10,20,20]],\"output_path\":\"$WORK/jpg-out.png\"}}}" | tail -1)
  jhas "and an object removed from it"    "$r" "d['result']['structuredContent']['path'].endswith('jpg-out.png')"
  PYDIR=$(dirname "$(command -v python3)")
  r=$(printf '%s\n%s\n' "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":30,\"method\":\"tools/call\",\"params\":{\"name\":\"inspect_image\",\"arguments\":{\"image\":\"$WORK/scene.jpg\"}}}" | env PATH="$PYDIR" python3 "$ROOT/mcp/imagebridge-server.py" 2>/dev/null | tail -1)
  jhas "with no converter it says what to install" "$r" "d['result'].get('isError') is True and 'no image converter is installed' in d['result']['content'][0]['text']"
else
  ok "a JPEG can be measured (skipped: no converter here)"
  ok "and an object removed from it (skipped: no converter here)"
  ok "with no converter it says what to install (skipped: no converter here)"
fi

# A 16-bit PNG hits the same decoder wall as a JPEG and takes the same route out.
python3 - "$WORK" <<'MK'
import sys, zlib, struct
w = h = 8
raw = b"".join(bytes([0]) + bytes([0, 1] * 3 * w) for _ in range(h))
def ch(t, d):
    return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
open(sys.argv[1] + "/deep.png", "wb").write(
    bytes([137,80,78,71,13,10,26,10])
    + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 16, 2, 0, 0, 0))
    + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b""))
MK
if true; then
  r=$(rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":29,\"method\":\"tools/call\",\"params\":{\"name\":\"inspect_image\",\"arguments\":{\"image\":\"$WORK/deep.png\"}}}" | tail -1)
  jhas "a 16-bit PNG is read directly"    "$r" "d['result']['structuredContent']['width']==8"
fi

unset IMAGEBRIDGE_INPAINT_URL
r=$(IMAGEBRIDGE_CONFIG=/nonexistent rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":23,\"method\":\"tools/call\",\"params\":{\"name\":\"remove_object\",\"arguments\":{\"image\":\"$WORK/scene.png\",\"regions\":[[1,1,4,4]]}}}" | tail -1)
jhas "unconfigured says so, clearly"    "$r" "d['result'].get('isError') is True and 'no inpaint endpoint is configured' in d['result']['content'][0]['text']"
kill "$STUB_PID" 2>/dev/null; wait "$STUB_PID" 2>/dev/null || true

printf 'passed %d, failed %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
