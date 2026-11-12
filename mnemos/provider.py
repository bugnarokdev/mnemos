"""Hermes MemoryProvider adapter. Network I/O stays off the prefetch path."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Optional

from agent.memory_provider import MemoryProvider, RecallStatus, spawn_context_thread
from agent.secret_scope import get_secret

from .engine import open_engine
from .py_engine import ensure_package_id, load_file_config, save_file_config

logger = logging.getLogger(__name__)

_TOOLS = ("mnemos_remember", "mnemos_recall", "mnemos_status", "mnemos_flush")


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or ""
                if text:
                    parts.append(str(text))
        return "\n".join(parts)
    return ""


class MnemosProvider(MemoryProvider):
    """Profile-scoped sovereign memory. Construct a new instance per load."""

    pre_compress_checkpoint_api_version = 2

    def __init__(self) -> None:
        self._home = ""
        self._session_id = ""
        self._agent_context = "primary"
        self._engine = None
        self._cache: dict[str, tuple[str, str, int]] = {}
        self._cache_lock = threading.Lock()
        self._last_count = 0
        self._last_ms = 0.0
        self._last_had_content = False
        self._flush_lock = threading.Lock()

    @property
    def name(self) -> str:
        return "mnemos"

    def is_available(self) -> bool:
        return bool(get_secret("MNEMOS_SECRET"))

    def unavailable_reason(self) -> str:
        return "Set MNEMOS_SECRET with `hermes memory setup` or the profile .env. No network is contacted until a flush."

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        secret = get_secret("MNEMOS_SECRET") or ""
        if not secret:
            raise RuntimeError(self.unavailable_reason())
        home = str(kwargs.get("hermes_home") or "")
        if not home:
            raise RuntimeError("mnemos requires hermes_home from initialize()")
        self._home = home
        self._session_id = session_id or "default"
        self._agent_context = str(kwargs.get("agent_context") or "primary")
        config = load_file_config(home)
        sui_key = get_secret("MNEMOS_SUI_PRIVATE_KEY") or ""
        if sui_key:
            config["sui_private_key"] = sui_key
        ensure_package_id(home, config)
        db_path = str(Path(home) / "mnemos" / "mnemos.sqlite")
        self._engine = open_engine(db_path, secret, config)
        self._warm("")

    def _writes_enabled(self) -> bool:
        return self._agent_context in ("", "primary")

    def _require_engine(self):
        if self._engine is None:
            raise RuntimeError("mnemos is not initialized")
        return self._engine

    def system_prompt_block(self) -> str:
        return (
            "Mnemos is the active sovereign memory. Durable facts are encrypted on this "
            "machine (XChaCha20-Poly1305), uploaded as Walrus blobs, and indexed by an "
            "owned Sui MemoryIndex whose hash chain is signed by the user's key. "
            "Use mnemos_remember to store a fact, mnemos_recall to search the local cache, "
            "and mnemos_flush to commit the outbox. Prefetched context is local only."
        )

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "mnemos_remember",
                "description": "Store a durable fact in Mnemos. It is queued locally and committed to Walrus and Sui on flush.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "The fact to remember."},
                        "kind": {
                            "type": "string",
                            "description": "fact, preference, or note.",
                            "default": "fact",
                        },
                    },
                    "required": ["content"],
                },
            },
            {
                "name": "mnemos_recall",
                "description": "Search the local Mnemos cache. Does not contact Walrus or Sui.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Words to match."},
                        "limit": {"type": "integer", "description": "Maximum hits.", "default": 5},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "mnemos_status",
                "description": "Show the local Mnemos store, chain head, active backend, and why it was selected.",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "mnemos_flush",
                "description": "Commit the local outbox: encrypt, PUT the blob to Walrus, and append the MemoryIndex on Sui.",
                "parameters": {"type": "object", "properties": {}},
            },
        ]

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs: Any) -> str:
        try:
            if tool_name == "mnemos_remember":
                result = self._require_engine().remember(
                    str(args.get("content") or ""),
                    self._session_id,
                    str(args.get("kind") or "fact"),
                )
                return json.dumps({"ok": True, **result})
            if tool_name == "mnemos_recall":
                found = self._require_engine().recall(str(args.get("query") or ""), int(args.get("limit") or 5))
                return json.dumps(found)
            if tool_name == "mnemos_status":
                return json.dumps(self._require_engine().status())
            if tool_name == "mnemos_flush":
                return json.dumps(self._require_engine().flush())
            return json.dumps({"error": f"unknown tool {tool_name}"})
        except Exception as exc:
            logger.warning("mnemos tool %s failed: %s", tool_name, exc)
            return json.dumps({"error": str(exc)})

    def _recall_text(self, query: str) -> tuple[str, int]:
        found = self._require_engine().recall(query, 5)
        hits = found.get("hits") or []
        if not hits:
            return "", 0
        lines = ["[mnemos local recall]"]
        for hit in hits:
            lines.append(f"- ({hit.get('kind') or 'fact'}) {hit.get('text')}")
        return "\n".join(lines), len(hits)

    def _warm(self, query: str) -> None:
        try:
            text, count = self._recall_text(query)
        except Exception as exc:
            logger.warning("mnemos warm failed: %s", exc)
            return
        with self._cache_lock:
            self._cache[self._session_id] = (query, text, count)

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        started = time.perf_counter()
        key = session_id or self._session_id
        with self._cache_lock:
            cached = self._cache.get(key)
        if cached and cached[0] == query:
            text, count = cached[1], cached[2]
        else:
            text, count = self._recall_text(query)
            with self._cache_lock:
                self._cache[key] = (query, text, count)
        self._last_ms = (time.perf_counter() - started) * 1000.0
        self._last_count = count
        self._last_had_content = bool(text)
        return text

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        key = session_id or self._session_id

        def _run() -> None:
            try:
                text, count = self._recall_text(query)
                with self._cache_lock:
                    self._cache[key] = (query, text, count)
            except Exception as exc:
                logger.warning("mnemos prefetch failed: %s", exc)

        spawn_context_thread(_run, name="mnemos-prefetch").start()

    def recall_status(self) -> Optional[RecallStatus]:
        if not self._last_had_content:
            return None
        return RecallStatus(provider_label="Mnemos", count=self._last_count)

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[list[dict[str, Any]]] = None,
        turn_author: Optional[dict[str, Any]] = None,
    ) -> None:
        if not self._writes_enabled() or self._engine is None:
            return
        sid = session_id or self._session_id
        if user_content and user_content.strip():
            self._engine.remember(user_content.strip()[:4000], sid, "turn_user")
        if assistant_content and assistant_content.strip():
            self._engine.remember(assistant_content.strip()[:4000], sid, "turn_assistant")
        self._kick_flush()

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        if not self._writes_enabled() or self._engine is None:
            return
        if action not in {"add", "replace", "patch"}:
            return
        text = (content or "").strip()
        if not text:
            return
        self._engine.remember(text[:4000], self._session_id, f"memory_{target}")
        self._kick_flush()

    def on_pre_compress(self, messages: list[dict[str, Any]], *, require_checkpoint: bool = False) -> str:
        engine = self._require_engine()
        rows = []
        for message in messages or []:
            text = _message_text(message).strip()
            if not text:
                continue
            role = str(message.get("role") or "user")
            rows.append(
                {
                    "kind": "checkpoint",
                    "text": f"{role}: {text[:4000]}",
                    "session_id": self._session_id,
                }
            )
        idem = hashlib.sha256(
            json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if not rows:
            return "checkpoint: noop"
        result = engine.checkpoint(rows, idem, self._session_id)
        blob_id = str(result.get("blob_id") or "")
        if not blob_id:
            raise RuntimeError(result.get("error") or "mnemos checkpoint was not durable")
        return f"checkpoint: {blob_id}"

    def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        if not self._writes_enabled() or self._engine is None:
            return
        done = threading.Event()

        def _run() -> None:
            try:
                self._engine.flush()
            except Exception as exc:
                logger.warning("mnemos session flush failed: %s", exc)
            finally:
                done.set()

        spawn_context_thread(_run, name="mnemos-session-end").start()
        done.wait(timeout=20.0)

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        rewound: bool = False,
        **kwargs: Any,
    ) -> None:
        self._session_id = new_session_id or self._session_id
        if self._engine is not None:
            self._warm("")

    def on_turn_start(self, turn_number: int, message: str, **kwargs: Any) -> None:
        resumed = bool(kwargs.get("resumed"))
        previous = kwargs.get("previous_message") or ""
        if previous:
            self.queue_prefetch(str(previous), session_id=self._session_id)
        elif message:
            self.queue_prefetch(message, session_id=self._session_id)
        if resumed and self._writes_enabled():
            self._kick_flush()

    def _kick_flush(self) -> None:
        if not self._writes_enabled() or self._engine is None:
            return
        if not getattr(self._engine, "chain_writes", False):
            return

        def _run() -> None:
            if not self._flush_lock.acquire(blocking=False):
                return
            try:
                self._engine.flush()
            except Exception as exc:
                logger.warning("mnemos flush failed: %s", exc)
            finally:
                self._flush_lock.release()

        spawn_context_thread(_run, name="mnemos-flush").start()

    def get_config_schema(self) -> list[dict[str, Any]]:
        return [
            {
                "key": "mnemos_secret",
                "description": "Passphrase. Derives the encryption key and the snapshot signing key. It never leaves this machine.",
                "secret": True,
                "required": True,
                "env_var": "MNEMOS_SECRET",
            },
            {
                "key": "sui_private_key",
                "description": "Optional Sui ed25519 key (suiprivkey or hex). When unset, Mnemos derives the gas key from MNEMOS_SECRET.",
                "secret": True,
                "required": False,
                "env_var": "MNEMOS_SUI_PRIVATE_KEY",
            },
            {
                "key": "index_object_id",
                "description": "Owned MemoryIndex object id. Leave empty and run `hermes mnemos-sui init` to create one.",
                "required": False,
                "default": "",
            },
        ]

    def save_config(self, values: dict[str, Any], hermes_home: str) -> None:
        allowed = {
            key: values[key]
            for key in ("index_object_id", "package_id", "sui_rpc", "graphql_url", "walrus_publisher", "walrus_aggregator", "epochs", "network", "faucet_url", "read_source", "graphql_max_lag")
            if key in values
        }
        save_file_config(hermes_home, allowed)

    def get_status_config(self, provider_config: dict[str, Any]) -> dict[str, Any]:
        file_cfg: dict[str, Any] = {}
        try:
            from hermes_constants import get_hermes_home

            file_cfg = load_file_config(str(get_hermes_home()))
        except Exception:
            file_cfg = {}
        merged = dict(file_cfg)
        if isinstance(provider_config, dict):
            merged.update({k: v for k, v in provider_config.items() if v not in (None, "")})
        return {
            "network": merged.get("network") or "testnet",
            "index_object_id": merged.get("index_object_id") or "(unset)",
            "package_id": merged.get("package_id") or "(unset)",
            "sui_rpc": merged.get("sui_rpc") or file_cfg.get("sui_rpc") or "https://fullnode.testnet.sui.io:443",
            "walrus_publisher": merged.get("walrus_publisher") or "https://publisher.walrus-testnet.walrus.space",
            "epochs": merged.get("epochs", 5),
        }

    def identity_signature(self) -> dict[str, Any]:
        secret = get_secret("MNEMOS_SECRET") or ""
        if not secret:
            return {}
        from .chain import derive_keys

        return {"mnemos.pubkey": derive_keys(secret)["pubkey_hex"]}

    def backup_paths(self) -> list[str]:
        return []

    def shutdown(self) -> None:
        if self._engine is not None:
            try:
                if getattr(self._engine, "chain_writes", False):
                    self._engine.flush()
            except Exception as exc:
                logger.warning("mnemos shutdown flush failed: %s", exc)
            closer = getattr(self._engine, "close", None)
            if closer:
                closer()
            self._engine = None


def register(ctx) -> None:
    ctx.register_memory_provider(MnemosProvider())
