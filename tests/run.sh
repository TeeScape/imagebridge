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
python3 - "$sess/exec-stub.${STUB_EXT:-png}" 4 4 0 <<'PY'
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
has  "--dry-run shows the codex argv"   "$out" "codex exec"
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
r=$(OPENAI_API_KEY=test-key rpc "$INIT" "{\"jsonrpc\":\"2.0\",\"id\":11,\"method\":\"tools/call\",\"params\":{\"name\":\"edit_image\",\"arguments\":{\"prompt\":\"make it blue\",\"images\":[\"$WORK/src.png\"],\"mask\":\"$WORK/mask-ok.png\",\"output_path\":\"$WORK/mcp-edited.png\"}}}" | tail -1)
jhas "edit_image returns a path through MCP" "$r" "d['result']['structuredContent']['paths']"

r=$(rpc "$INIT" '{"jsonrpc":"2.0","method":"notifications/initialized"}' '{"jsonrpc":"2.0","id":8,"method":"ping"}')
is "a notification gets no reply" "$(printf '%s' "$r" | grep -c '"id"')" "2"

echo
printf 'passed %d, failed %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
