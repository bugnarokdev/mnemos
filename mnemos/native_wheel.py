"""Optional download of an attested native wheel. Failure keeps the Python backend."""

from __future__ import annotations

import hashlib
import io
import os
import platform
import sys
import urllib.request
import zipfile
from pathlib import Path

RELEASE_TAG = "mnemos-native-v0.2.1"
DEFAULT_RELEASE = f"https://github.com/bugnarokdev/mnemos/releases/download/{RELEASE_TAG}"
_REASON = ""


def last_reason() -> str:
    return _REASON


def try_install() -> str:
    """Fetch the wheel for this OS and CPU, check its sha256, and put it on sys.path.

    The GitHub Actions workflow attests each wheel. This function checks the
    checksum published next to the asset. A missing release or a bad checksum
    leaves the Python backend in place.
    """
    global _REASON
    base = os.environ.get("MNEMOS_NATIVE_BASE", DEFAULT_RELEASE).rstrip("/")
    name = _wheel_name()
    if not name:
        _REASON = f"no published wheel for {platform.system()} {platform.machine()}"
        return _REASON
    try:
        sums = _get(f"{base}/SHA256SUMS", timeout=8).decode("utf-8")
        expected = ""
        for line in sums.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == name:
                expected = parts[0]
        if not expected:
            _REASON = f"checksum for {name} is not in the release"
            return _REASON
        blob = _get(f"{base}/{name}", timeout=20)
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
