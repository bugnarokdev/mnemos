#!/bin/sh
# BETA (testnet preview): end-to-end check of the sharing lane on Sui testnet, Walrus testnet and
# the Mysten Seal testnet key servers. Needs mnemos_core built with the seal feature:
#   MNEMOS_FEATURES=seal scripts/build_core.sh
#
# Required environment (key files are read, never printed):
#   SHARE_KEY_A, SHARE_KEY_B, SHARE_KEY_C  signer key files (suiprivkey, keystore base64
#                                          or 32-byte hex). A needs testnet SUI for gas.
#   MNEMOS_SECRET                          A's personal secret; its derived Sui address pays
#                                          for A's fresh MemoryIndex.
# Optional:
#   MNEMOS_PACKAGE_ID  MemoryIndex package (default: the 0.2.3 test package)
#   MNEMOS_PYTHON      interpreter with mnemos_core installed (default python3)
#   E2E_WORK, E2E_REPORT
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
PY=${MNEMOS_PYTHON:-python3}
: "${SHARE_KEY_A:?set SHARE_KEY_A}" "${SHARE_KEY_B:?set SHARE_KEY_B}" "${SHARE_KEY_C:?set SHARE_KEY_C}"
: "${MNEMOS_SECRET:?set MNEMOS_SECRET}"
export MNEMOS_PACKAGE_ID=${MNEMOS_PACKAGE_ID:-0xc1222936acba3d4b12e87341b43c689d932c04a026805ed1b3758f32a37f1f4a}
CLEAN=0
if [ -z "${E2E_WORK:-}" ]; then
  E2E_WORK=$(mktemp -d)
  CLEAN=1
fi
export E2E_WORK SHARE_KEY_A SHARE_KEY_B SHARE_KEY_C MNEMOS_SECRET
unset MNEMOS_BACKEND
status=0
"$PY" "$ROOT/scripts/e2e_coop.py" || status=$?
if [ "$CLEAN" = 1 ]; then rm -rf "$E2E_WORK"; fi
exit $status
