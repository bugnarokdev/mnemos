#!/bin/sh
# BETA (testnet preview): build the sui-seal-client Python bindings used by the Python
# sharing lane, from the pinned commit only, and install them into the interpreter.
#
#   MNEMOS_PYTHON   interpreter to install into (default python3)
#   MATURIN         maturin binary (default: maturin, else pip-installed maturin==1.16.0)
#   SEAL_CLIENT_GIT clone source (default the GitHub repository; a local mirror works,
#                   the commit is checked either way)
#   CARGO_TARGET_DIR  optional build cache
set -eu
PIN=da579f64223ff3f7a83897272937cfc4d2d55153   # sui-seal-client tag v0.2.0
SRC=${SEAL_CLIENT_GIT:-https://github.com/bugnarokdev/sui-seal-client}
PY=${MNEMOS_PYTHON:-python3}
MATURIN=${MATURIN:-maturin}
if ! command -v "$MATURIN" >/dev/null 2>&1; then
  "$PY" -m pip install 'maturin==1.16.0'
  MATURIN="$PY -m maturin"
fi
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
git clone --quiet --no-checkout "$SRC" "$WORK/src"
git -C "$WORK/src" -c advice.detachedHead=false checkout --quiet "$PIN"
test "$(git -C "$WORK/src" rev-parse HEAD)" = "$PIN" || { echo "sui-seal-client is not at $PIN" >&2; exit 1; }
cd "$WORK/src/python"
$MATURIN build --release --locked --interpreter "$PY" -o "$WORK/dist"
"$PY" - "$WORK/dist" <<'PY'
import shutil, site, sys, zipfile
from pathlib import Path
wheel = next(Path(sys.argv[1]).glob("*.whl"))
dest = Path(site.getsitepackages()[0])
for old in list(dest.glob("sui_seal_client*")):
    shutil.rmtree(old) if old.is_dir() else old.unlink()
with zipfile.ZipFile(wheel) as archive:
    archive.extractall(dest)
print(f"installed {wheel.name} into {dest}")
PY
"$PY" -c 'import sui_seal_client as s; print("sui_seal_client", s.__version__, s.__file__)'
