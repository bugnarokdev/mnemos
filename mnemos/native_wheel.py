"""Opt-in download of an attested native wheel. Failure keeps the Python backend.

Runs only when MNEMOS_NATIVE_AUTO=1. The wheel comes from the official GitHub
release of this repository and must match a sha256 pinned in this file.
"""

from __future__ import annotations

import hashlib
import io
import platform
import sys
import urllib.request
import zipfile
from pathlib import Path

RELEASE_TAG = "mnemos-native-v0.2.1"
DEFAULT_RELEASE = f"https://github.com/bugnarokdev/mnemos/releases/download/{RELEASE_TAG}"
_REASON = ""

# sha256 of each asset in the mnemos-native-v0.2.1 release (same values as its SHA256SUMS).
PINNED_SHA256 = {
    "mnemos_core-0.2.0-cp311-abi3-macosx_10_12_x86_64.whl": "9e6684a3d7af1ca27b15e1599779ebe31fc17caede818262e0077cdf53b17d46",
    "mnemos_core-0.2.0-cp311-abi3-macosx_11_0_arm64.whl": "856199502ca591d5d81f8e92900c6bfd8caaff00fd40b5a4afbb813cab3cb22f",
    "mnemos_core-0.2.0-cp311-abi3-manylinux_2_34_aarch64.whl": "1fb92128d696dd550b5631c8faf26d07353156d2bd673b10dd6c087b9f7c5a73",
    "mnemos_core-0.2.0-cp311-abi3-manylinux_2_34_x86_64.whl": "0c03327b3d41f08fc520c37cfd333d52822cd6e1e94f1d9741cbe9a1be978a95",
    "mnemos_core-0.2.0-cp311-abi3-win_amd64.whl": "caac6301790f2186a9767f790ddf9c42c03194120336de31ef7b321ad8a7cef8",
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
        cache = Path.home() / ".cache" / "mnemos" / "native"
        cache.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            archive.extractall(cache)
        if str(cache) not in sys.path:
            sys.path.insert(0, str(cache))
        _REASON = f"loaded attested wheel {name}"
        return _REASON
    except Exception as exc:
        _REASON = f"native wheel not used ({exc.__class__.__name__})"
        return _REASON


def _wheel_name() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    version = "0.2.0"
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
