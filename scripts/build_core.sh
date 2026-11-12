#!/bin/sh
# Build the native core from source and install it into the active interpreter.
# MNEMOS_FEATURES=seal adds the cooperative sharing lane (Beta preview, testnet).
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
PY=${MNEMOS_PYTHON:-python3}
MATURIN=${MATURIN:-maturin}
cd "$ROOT/native"
if ! command -v "$MATURIN" >/dev/null 2>&1; then
  "$PY" -m pip install 'maturin>=1.8,<2'
  MATURIN="$PY -m maturin"
fi
OUT=$(mktemp -d)
"$MATURIN" build --release --interpreter "$PY" -o "$OUT" ${MNEMOS_FEATURES:+--features "$MNEMOS_FEATURES"}
"$PY" - "$OUT" <<'PY'
import site, sys, zipfile
from pathlib import Path
wheel = next(Path(sys.argv[1]).glob("*.whl"))
dest = Path(site.getsitepackages()[0])
with zipfile.ZipFile(wheel) as archive:
    archive.extractall(dest)
print(f"installed {wheel.name} into {dest}")
PY
rm -rf "$OUT"
"$PY" -c 'import mnemos_core; print("mnemos_core", mnemos_core.__file__)'
