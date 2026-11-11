"""Opt-in download of an attested native wheel. Failure keeps the Python backend.

Runs only when MNEMOS_NATIVE_AUTO=1. The wheel comes from the official GitHub
release of this repository and must match a sha256 pinned in this file.
"""

from __future__ import annotations

import hashlib
import io
import os
import platform
import sys
import urllib.request
import zipfile
from pathlib import Path

RELEASE_TAG = "mnemos-native-v0.2.3"
WHEEL_VERSION = "0.2.3"
DEFAULT_RELEASE = f"https://github.com/bugnarokdev/mnemos/releases/download/{RELEASE_TAG}"
_REASON = ""

# sha256 of each asset in the mnemos-native-v0.2.3 release (same values as its SHA256SUMS).
# The release is built and attested from the tagged commit 82fba13; this commit only adds this table.
PINNED_SHA256 = {
    "mnemos_core-0.2.3-cp311-abi3-macosx_10_12_x86_64.whl": "2b0ec16f5d02df7759fdb080cc8c826f4bfc88d0f5c7410daf6ee3375e0b26fe",
    "mnemos_core-0.2.3-cp311-abi3-macosx_11_0_arm64.whl": "ff28f82f1b010ec459e3720591ebfc8f9a85dffa84d75aa882ae0fb0f9cb9038",
    "mnemos_core-0.2.3-cp311-abi3-manylinux_2_34_aarch64.whl": "3bd1b5245781f31cb3391712bc54ee1ea19ad3032ce7a701391d16d85ab07c78",
    "mnemos_core-0.2.3-cp311-abi3-manylinux_2_34_x86_64.whl": "d7bd9ea2f9cd55bbde33822b4b39670bcfa40ad929232bae6d6455b24844ea01",
    "mnemos_core-0.2.3-cp311-abi3-win_amd64.whl": "97f1190fb50d76b13ed19dd2beae89c96b6607a549ebf37db921bd0ebd7207e7",
}


def last_reason() -> str:
    return _REASON


def try_install() -> str:
    """Fetch the wheel for this OS and CPU, check its pinned sha256, and put it on sys.path.

    The GitHub Actions workflow attests each wheel. The URL is fixed to the
    official release and the expected sha256 is pinned in PINNED_SHA256, so no
    checksum is read from the network. A missing release or a bad checksum
    leaves the Python backend in place.
    """
    global _REASON
    name = _wheel_name()
    if not name:
        _REASON = f"no published wheel for {platform.system()} {platform.machine()}"
        return _REASON
    expected = PINNED_SHA256.get(name, "")
    if not expected:
        _REASON = f"no pinned checksum for {name}"
        return _REASON
    try:
        blob = _get(f"{DEFAULT_RELEASE}/{name}", timeout=20)
        actual = hashlib.sha256(blob).hexdigest()
        if actual != expected:
            _REASON = "native wheel checksum did not match"
            return _REASON
        cache = _hermes_home() / "mnemos" / "native" / RELEASE_TAG
        cache.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            archive.extractall(cache)
        # Appended, so it never shadows a module already on the path.
        if str(cache) not in sys.path:
            sys.path.append(str(cache))
        _REASON = f"loaded attested wheel {name}"
        return _REASON
    except Exception as exc:
        _REASON = f"native wheel not used ({exc.__class__.__name__})"
        return _REASON


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))


def _wheel_name() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    version = WHEEL_VERSION
    if system == "linux" and machine in {"x86_64", "amd64"}:
        tag = "manylinux_2_34_x86_64"
    elif system == "linux" and machine in {"aarch64", "arm64"}:
        tag = "manylinux_2_34_aarch64"
    elif system == "darwin" and machine in {"arm64", "aarch64"}:
        tag = "macosx_11_0_arm64"
    elif system == "darwin" and machine == "x86_64":
        tag = "macosx_10_12_x86_64"
    elif system == "windows" and machine in {"amd64", "x86_64"}:
        tag = "win_amd64"
    else:
        return ""
    return f"mnemos_core-{version}-cp311-abi3-{tag}.whl"


def _get(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "mnemos"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()
