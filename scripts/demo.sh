#!/bin/sh
# End-to-end Mnemos demo on Sui testnet and Walrus testnet.
#
# Requires: hermes (v0.23.1+), sui (Move compiler). A Rust toolchain and maturin
# are used only when MNEMOS_BACKEND is not python and mnemos_core is missing.
# The testnet faucet is an automated proof-of-work. If it rate-limits or a
# future faucet asks for a captcha, fund the printed address from
# https://faucet.sui.io and rerun with the same MNEMOS_SECRET.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
HERMES_BIN=${HERMES_BIN:-hermes}
SUI_BIN=${SUI_BIN:-sui}
PY=${MNEMOS_PYTHON:-python3}
export HERMES_SRC=${HERMES_SRC:-}

if [ "${MNEMOS_BACKEND:-}" != "python" ] && ! "$PY" -c 'import mnemos_core' >/dev/null 2>&1; then
  echo "building native core"
  MNEMOS_PYTHON="$PY" MATURIN="${MATURIN:-maturin}" "$ROOT/scripts/build_core.sh"
fi
command -v "$SUI_BIN" >/dev/null 2>&1 || {
  echo "sui CLI is required to compile move/MemoryIndex. Install it from the Mysten Labs release archive." >&2
  exit 1
}
command -v "$HERMES_BIN" >/dev/null 2>&1 || {
  echo "hermes CLI is required." >&2
  exit 1
}

# Profiles under the default Hermes home share its dependency install. A bare
# HERMES_HOME elsewhere boots without those packages.
BASE=${HERMES_INSTALL_HOME:-$HOME/.hermes}
WORK=$(mktemp -d)
PROFILE_A=$BASE/profiles/mnemos-demo-a
PROFILE_B=$BASE/profiles/mnemos-demo-b
cleanup() {
  rm -rf "$PROFILE_A" "$PROFILE_B" "$WORK"
}
trap cleanup EXIT
if [ -n "${MNEMOS_SECRET:-}" ]; then
  SECRET=$MNEMOS_SECRET
else
  SECRET=$("$PY" -c 'import secrets; print(secrets.token_hex(16))')
fi
export MNEMOS_SECRET=$SECRET
printf '%s\n' "$SECRET" > "$WORK/secret"
echo "backend ${MNEMOS_BACKEND:-auto}"
echo "demo workspace $WORK"
echo "secret saved at $WORK/secret (removed when the script exits; copy it if you need to rerun)"

setup_profile() {
  home=$1
  mkdir -p "$home/plugins" "$home/mnemos"
  ln -sfn "$ROOT" "$home/plugins/mnemos-sui"
  printf '%s\n' 'memory:' '  provider: mnemos-sui' > "$home/config.yaml"
  printf 'MNEMOS_SECRET=%s\n' "$SECRET" > "$home/.env"
}

echo "compiling MemoryIndex"
(cd "$ROOT/move/MemoryIndex" && "$SUI_BIN" move build --dump-bytecode-as-base64 > "$WORK/bytecode.json")

setup_profile "$PROFILE_A"
export HERMES_HOME=$PROFILE_A
echo "profile A $HERMES_HOME"
"$HERMES_BIN" mnemos-sui init --bytecode "$WORK/bytecode.json" | tee "$WORK/init.json"
"$PY" - "$WORK/init.json" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
# The CLI prints only JSON. Progress lines, if any, are on stderr.
if not data.get("index_object_id") or not data.get("package_id"):
    raise SystemExit(f"init did not return chain ids: {data}")
open(sys.argv[1] + ".ids", "w").write(data["index_object_id"] + "\n" + data["package_id"] + "\n")
print("index", data["index_object_id"])
print("package", data["package_id"])
PY
INDEX=$(sed -n '1p' "$WORK/init.json.ids")
PACKAGE=$(sed -n '2p' "$WORK/init.json.ids")

echo "writing the fact through Hermes"
export HERMES_SRC
"$PY" "$ROOT/scripts/drive.py" write

echo "hermes memory status (profile A)"
"$HERMES_BIN" memory status

echo "wiping profile A"
rm -rf "$PROFILE_A"
setup_profile "$PROFILE_B"
export HERMES_HOME=$PROFILE_B
"$PY" - "$PROFILE_B" "$PACKAGE" "$INDEX" <<'PY'
import json, sys
from pathlib import Path
home, package, index = sys.argv[1:]
path = Path(home) / "mnemos" / "config.json"
path.write_text(json.dumps({
    "package_id": package,
    "index_object_id": index,
    "network": "testnet",
}, indent=2) + "\n")
PY

echo "restoring into profile B"
"$HERMES_BIN" mnemos-sui restore --object-id "$INDEX"
echo "hermes mnemos-sui doctor"
"$HERMES_BIN" mnemos-sui doctor
echo "hermes memory status (profile B)"
"$HERMES_BIN" memory status
echo "recalling through Hermes"
"$PY" "$ROOT/scripts/drive.py" recall
echo "demo ok"
