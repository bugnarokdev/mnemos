"""``hermes mnemos-sui`` commands. Active only when memory.provider is mnemos-sui."""

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
    from .mnemos.py_engine import ensure_package_id, load_file_config

    secret = get_secret("MNEMOS_SECRET") or ""
    if not secret:
        raise SystemExit("MNEMOS_SECRET is not set for this profile.")
    home = _home()
    config = load_file_config(home)
    sui_key = get_secret("MNEMOS_SUI_PRIVATE_KEY") or ""
    if sui_key:
        config["sui_private_key"] = sui_key
    ensure_package_id(home, config)
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
    elif cmd == "history":
        _history(getattr(args, "object_id", "") or "", bool(getattr(args, "verify", False)), bool(getattr(args, "json", False)))
    elif cmd == "share":
        _share(args)
    else:
        raise SystemExit("Usage: hermes mnemos-sui <status|doctor|restore|flush|init|history|share>")


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
        if result["index_object_id"] != config.get("index_object_id"):
            config["package_id"] = ""
            save_file_config(home, {"package_id": None})
        config["index_object_id"] = result["index_object_id"]
        package_id = ensure_package_id(home, config)
        if package_id:
            result["package_id"] = package_id
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
        raise SystemExit("hermes mnemos-sui init --bytecode <sui-move-build.json>")
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


def _history(object_id: str, verify: bool, as_json: bool) -> None:
    """List or verify the MemoryIndex history. Needs no secret."""
    from .mnemos import history
    from .mnemos.py_engine import load_file_config, walrus_get
    from .mnemos.reads import memory_index

    config = load_file_config(_home())
    object_id = (object_id or config.get("index_object_id") or "").strip()
    if not object_id:
        raise SystemExit("No MemoryIndex: pass --object-id or run `hermes mnemos-sui init`.")
    try:
        _obj, contents = memory_index(config, object_id)
    except Exception as exc:
        raise SystemExit(str(exc)) from exc
    index = history.parse_index(contents)
    if not verify:
        rows = history.list_history(index)
        if as_json:
            print(json.dumps({"index_object_id": object_id, "versions": rows}, indent=2))
        else:
            print(f"MemoryIndex {object_id}: {len(rows)} version{'' if len(rows) == 1 else 's'}")
            for row in rows:
                print(f"v{row['version']:<4} {row['content_hash'][:16]}  session {row['session_id']}  blob {row['blob_id']}")
        return
    report = history.verify_history(index, lambda blob_id: walrus_get(config["walrus_aggregator"], blob_id))
    report["index_object_id"] = object_id
    if as_json:
        print(json.dumps(report, indent=2))
    else:
        print(f"MemoryIndex {object_id}  pubkey {report.get('pubkey', '')[:16]}")
        for row in report["rows"]:
            print(history.format_row(row))
        print(report["verdict"])
    if not report["ok"]:
        raise SystemExit(1)


def _share(args) -> None:
    """Cooperative sharing lane (Beta preview, testnet). Native core with the seal feature only."""
    from agent.secret_scope import get_secret

    from .mnemos import share

    if not getattr(args, "share_command", None):
        raise SystemExit("Usage: hermes mnemos-sui share <create|join|add|remove|push|pull|status|whoami>")
    code = share.run(args, _home(), get_secret("MNEMOS_SECRET") or "")
    if code:
        raise SystemExit(code)


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
    hist = subs.add_parser("history", help="List the MemoryIndex history, or check it with --verify")
    hist.add_argument("--object-id", default="", help="Sui object id (defaults to config)")
    hist.add_argument("--verify", action="store_true", help="Fetch each blob, recompute hashes, check signatures")
    hist.add_argument("--json", action="store_true", help="Print JSON instead of one line per version")
    from .mnemos.share import add_arguments as share_arguments

    share_arguments(subs.add_parser("share", help="Beta preview: Seal-encrypted shared spaces on testnet"))
    subparser.set_defaults(func=mnemos_command)
