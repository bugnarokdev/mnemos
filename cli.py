"""``hermes mnemos`` commands. Active only when memory.provider is mnemos."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _home() -> str:
    from hermes_constants import get_hermes_home

    return str(get_hermes_home())


def _engine():
    from agent.secret_scope import get_secret

    from .mnemos.engine import open_engine
    from .mnemos.py_engine import load_file_config

    secret = get_secret("MNEMOS_SECRET") or ""
    if not secret:
        raise SystemExit("MNEMOS_SECRET is not set for this profile.")
    home = _home()
    config = load_file_config(home)
    sui_key = get_secret("MNEMOS_SUI_PRIVATE_KEY") or ""
    if sui_key:
        config["sui_private_key"] = sui_key
    db_path = str(Path(home) / "mnemos" / "mnemos.sqlite")
    return open_engine(db_path, secret, config), home, config


def mnemos_command(args) -> None:
    cmd = getattr(args, "mnemos_command", None) or "status"
    if cmd == "status":
        _status()
    elif cmd == "doctor":
        _doctor()
    elif cmd == "restore":
        _restore(getattr(args, "object_id", "") or "")
    elif cmd == "flush":
        _flush()
    elif cmd == "init":
        _init(getattr(args, "bytecode", "") or "")
    else:
        raise SystemExit("Usage: hermes mnemos <status|doctor|restore|flush|init>")


def _status() -> None:
    engine, _home_path, _config = _engine()
    try:
        print(json.dumps(engine.status(), indent=2))
    finally:
        engine.close()


def _doctor() -> None:
    engine, _home_path, _config = _engine()
    try:
        report = engine.doctor()
    finally:
        engine.close()
    print(json.dumps(report, indent=2))
    if not report.get("ok"):
        raise SystemExit(1)


def _restore(object_id: str) -> None:
    from .mnemos.py_engine import save_file_config

    engine, home, config = _engine()
    try:
        result = engine.restore(object_id or config.get("index_object_id") or "")
    finally:
        engine.close()
    if result.get("index_object_id"):
        save_file_config(home, {"index_object_id": result["index_object_id"]})
    print(json.dumps(result, indent=2))


def _flush() -> None:
    engine, _home_path, _config = _engine()
    try:
        print(json.dumps(engine.flush(), indent=2))
    finally:
        engine.close()


def _init(bytecode_path: str) -> None:
    from .mnemos.py_engine import save_file_config

    if not bytecode_path:
        raise SystemExit("hermes mnemos init --bytecode <sui-move-build.json>")
    raw = Path(bytecode_path).read_text(encoding="utf-8")
    engine, home, _config = _engine()
    try:
        result = engine.init_chain(raw)
    finally:
        engine.close()
    save_file_config(
        home,
        {
            "package_id": result.get("package_id") or "",
            "index_object_id": result.get("index_object_id") or "",
            "network": "testnet",
        },
    )
    print(json.dumps(result, indent=2))


def register_cli(subparser) -> None:
    subs = subparser.add_subparsers(dest="mnemos_command")
    subs.add_parser("status", help="Show the local store and chain head")
    subs.add_parser("doctor", help="Check the secret, store, Walrus, and Sui")
    restore = subs.add_parser("restore", help="Rebuild the local cache from a MemoryIndex")
    restore.add_argument("--object-id", default="", help="Sui object id (defaults to config)")
    flush = subs.add_parser("flush", help="Commit the outbox to Walrus and Sui")
    del flush
    init = subs.add_parser("init", help="Fund the gas key, publish MemoryIndex, create the object")
    init.add_argument("--bytecode", required=True, help="JSON from sui move build --dump-bytecode-as-base64")
    subparser.set_defaults(func=mnemos_command)
