"""Open the native engine when it is built, otherwise the Python fallback."""

from __future__ import annotations

import json
from typing import Any


class _RustEngine:
    backend = "rust"
    chain_writes = True
    reason = "the mnemos_core extension is loaded"

    def __init__(self, inner: Any):
        self._inner = inner

    def _call(self, name: str, *args: Any) -> Any:
        raw = getattr(self._inner, name)(*args)
        if isinstance(raw, str):
            return json.loads(raw)
        return raw

    def remember(self, text: str, session_id: str, kind: str = "fact") -> dict:
        return self._call("remember", text, session_id, kind)

    def recall(self, query: str, limit: int = 5) -> dict:
        return self._call("recall", query, int(limit))

    def checkpoint(self, entries: list[dict], idem_key: str, session_id: str) -> dict:
        return self._call("checkpoint", json.dumps(entries), idem_key, session_id)

    def flush(self) -> dict:
        return self._call("flush")

    def restore(self, object_id: str) -> dict:
        return self._call("restore", object_id or "")

    def doctor(self) -> dict:
        return self._call("doctor")

    def status(self) -> dict:
        return self._call("status")

    def init_chain(self, modules_json: str) -> dict:
        return self._call("init_chain", modules_json)

    def faucet(self) -> dict:
        return self._call("faucet")

    def pubkey_hex(self) -> str:
        return self._inner.pubkey_hex()

    def sui_address(self) -> str:
        return self._inner.sui_address()

    def close(self) -> None:
        closer = getattr(self._inner, "close", None)
        if closer:
            closer()


def open_engine(db_path: str, secret: str, config: dict):
    import os

    forced = os.environ.get("MNEMOS_BACKEND", "").strip().lower()
    auto = os.environ.get("MNEMOS_NATIVE_AUTO", "0").strip().lower() in {"1", "true", "yes"}
    reason = ""
    module = None
    if forced != "python":
        try:
            import mnemos_core as module
            reason = "the mnemos_core extension is already importable"
        except ImportError:
            module = None
            if auto:
                from .native_wheel import try_install

                reason = try_install()
                try:
                    import mnemos_core as module
                except ImportError:
                    module = None
            else:
                reason = "native download is opt-in (set MNEMOS_NATIVE_AUTO=1)"
    if module is not None and forced != "python":
        inner = module.Engine(db_path, secret, json.dumps(config))
        engine = _RustEngine(inner)
        engine.reason = reason or "the mnemos_core extension is loaded"
        return engine
    from .py_engine import Engine

    if forced == "python":
        reason = "MNEMOS_BACKEND=python"
    elif not reason:
        reason = "native module is not loaded; chain writes use the Python gRPC client"
    return Engine(db_path, secret, config, reason=reason)
