"""BETA (testnet preview): cooperative sharing lane on Sui testnet and Walrus testnet only.

A shared space is a Seal allowlist plus a ``mnemos_share::space::Space`` that lists
Seal-encrypted Walrus blobs. The lane runs in the native core built with the ``seal``
feature; the Python backend has no Seal implementation and refuses these commands.

It is separate from personal memory: it never writes the MemoryIndex, the local store
or ``config.json``. ``push --from-memory`` only reads the local store (``recall``).
Shared state lives in ``<HERMES_HOME>/mnemos/share.json`` and what a member has read
is cached in ``<HERMES_HOME>/mnemos/shared/<space>.json``.

Key servers (BETA): a space created without options uses the Mysten open testnet
servers, 2-of-2, as in the first preview. ``create --committee`` uses the Mysten testnet
committee (3-of-5 behind the Mysten aggregator); ``--key-server`` and ``--threshold`` set
an explicit list. The choice is stored per space in ``share.json`` (``seal``) and used
by ``push``. ``pull`` reads the servers from each entry's header, so readers need no
option.

Standalone use (outside Hermes)::

    python -m mnemos.share --home <hermes home> <create|join|add|remove|push|pull|status|whoami> ...
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

PULL_EXIT_NO_ACCESS = 3
PULL_EXIT_WRONG_OWNER = 4
STATE_FILE = "share.json"
CACHE_DIR = "shared"
# Settings the share lane reads from config.json (transport only).
TRANSPORT_KEYS = ("sui_rpc", "graphql_url", "walrus_publisher", "walrus_aggregator", "epochs", "read_source", "graphql_max_lag")
# Optional overrides stored in share.json under "config".
SHARE_KEYS = ("seal_package", "space_package", "key_server_profile", "key_servers", "threshold", "aggregators")
# Per-space key server settings stored in share.json under spaces.<name>.seal.
SPACE_SEAL_KEYS = ("key_server_profile", "key_servers", "threshold")


class ShareUnavailable(RuntimeError):
    pass


def native_share():
    """Return the native ``Share`` class or raise ``ShareUnavailable`` with the reason."""
    if os.environ.get("MNEMOS_BACKEND", "").strip().lower() == "python":
        raise ShareUnavailable(
            "the sharing lane needs the native core (Seal is not implemented in the Python backend); "
            "unset MNEMOS_BACKEND=python and build mnemos_core with the seal feature"
        )
    try:
        import mnemos_core
    except ImportError as exc:
        raise ShareUnavailable("the sharing lane needs mnemos_core built with the seal feature") from exc
    available = getattr(mnemos_core, "share_available", None)
    if available is None or not available() or not hasattr(mnemos_core, "Share"):
        raise ShareUnavailable(
            "this mnemos_core was built without the seal feature: MNEMOS_FEATURES=seal scripts/build_core.sh"
        )
    return mnemos_core.Share


def _mnemos_dir(home: str) -> Path:
    return Path(home) / "mnemos"


def load_state(home: str) -> dict[str, Any]:
    path = _mnemos_dir(home) / STATE_FILE
    if not path.is_file():
        return {"spaces": {}, "config": {}}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        return {"spaces": {}, "config": {}}
    loaded.setdefault("spaces", {})
    loaded.setdefault("config", {})
    return loaded


def save_state(home: str, state: dict[str, Any]) -> None:
    path = _mnemos_dir(home) / STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def share_config(home: str, state: dict[str, Any], seal: dict[str, Any] | None = None) -> dict[str, Any]:
    """Transport settings from config.json, share overrides from share.json, then the
    key server settings of one space (``seal``), which win."""
    from .py_engine import load_file_config

    base = load_file_config(home)
    config = {key: base[key] for key in TRANSPORT_KEYS if key in base}
    config.update({key: value for key, value in (state.get("config") or {}).items() if key in SHARE_KEYS})
    if seal:
        if seal.get("key_server_profile") and "key_servers" not in seal:
            # A profile brings its own servers and threshold: drop global ones.
            config.pop("key_servers", None)
            config.pop("threshold", None)
        config.update({key: value for key, value in seal.items() if key in SPACE_SEAL_KEYS})
    return config


def seal_from_args(args) -> dict[str, Any]:
    """Key server settings given on ``create`` / ``join`` (empty: defaults)."""
    seal: dict[str, Any] = {}
    servers = list(getattr(args, "key_server", None) or [])
    if getattr(args, "committee", False):
        if servers:
            raise SystemExit("--committee and --key-server are exclusive")
        seal["key_server_profile"] = "committee"
    if servers:
        seal["key_servers"] = servers
    threshold = getattr(args, "threshold", None)
    if threshold is not None:
        if threshold < 1:
            raise SystemExit("--threshold must be at least 1")
        seal["threshold"] = threshold
    return seal


def read_key_file(path: str) -> str:
    """Read a signer key file. The content is passed to the native core, never printed."""
    if not path:
        return ""
    raw = Path(path).expanduser().read_text(encoding="utf-8").strip()
    if not raw:
        raise SystemExit(f"key file {path} is empty")
    return raw


def open_share(home: str, secret: str, key_file: str = "", seal: dict[str, Any] | None = None, state=None):
    state = load_state(home) if state is None else state
    cls = native_share()
    key = read_key_file(key_file or os.environ.get("MNEMOS_SHARE_KEY_FILE", ""))
    if not key and not secret:
        raise SystemExit("no signer: set MNEMOS_SHARE_KEY_FILE (or --key-file) or MNEMOS_SECRET")
    try:
        return cls(json.dumps(share_config(home, state, seal)), key, secret), state
    except RuntimeError as exc:
        # Invalid key server settings are refused here, before any transaction.
        raise SystemExit(f"share settings: {exc}") from exc


def _space_seal(args, state: dict[str, Any]) -> dict[str, Any]:
    """Key server settings for this command: flags on create/join, else the space's own."""
    if args.share_command in ("create", "join"):
        return seal_from_args(args)
    space = (state.get("spaces") or {}).get(getattr(args, "name", ""))
    return dict((space or {}).get("seal") or {})


def _space(state: dict[str, Any], name: str) -> dict[str, Any]:
    space = (state.get("spaces") or {}).get(name)
    if not space:
        raise SystemExit(f"no shared space named {name!r}: run `share create` or `share join --space <id>`")
    return space


def _cache_path(home: str, space_id: str) -> Path:
    return _mnemos_dir(home) / CACHE_DIR / f"{space_id}.json"


def _load_cache(home: str, space_id: str) -> dict[str, Any]:
    path = _cache_path(home, space_id)
    if path.is_file():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            return loaded
    return {"entries": {}}


def _save_cache(home: str, space_id: str, cache: dict[str, Any]) -> None:
    path = _cache_path(home, space_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2) + "\n", encoding="utf-8")


def merge_pull(cache: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Store newly read entries in the cache; mark refused entries already read before."""
    entries = cache.setdefault("entries", {})
    for row in result.get("entries", []):
        key = row["key_id"]
        if row.get("status") == "ok":
            entries[key] = {k: row.get(k) for k in ("index", "epoch", "author", "blob_id", "text", "kind", "ts")}
        elif key in entries:
            row["cached_text"] = entries[key]["text"]
    return cache


def entries_from_args(args, home: str, secret: str) -> list[dict[str, str]]:
    items = [{"text": text, "kind": args.kind} for text in (args.text or [])]
    if args.from_memory:
        if not secret:
            raise SystemExit("--from-memory reads the personal store and needs MNEMOS_SECRET")
        from .engine import open_engine
        from .py_engine import load_file_config

        db_path = str(_mnemos_dir(home) / "mnemos.sqlite")
        engine = open_engine(db_path, secret, load_file_config(home))
        try:
            hits = engine.recall(args.from_memory, int(args.limit)).get("hits", [])
        finally:
            engine.close()
        items.extend({"text": hit["text"], "kind": hit.get("kind") or "fact"} for hit in hits)
    if not items:
        raise SystemExit("nothing to push: pass --text or --from-memory")
    return items


def run(args, home: str, secret: str) -> int:
    cmd = args.share_command
    state = load_state(home)
    seal = _space_seal(args, state)
    try:
        share, state = open_share(home, secret, getattr(args, "key_file", ""), seal, state)
    except ShareUnavailable as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2
    name = args.name
    if cmd == "whoami":
        out: dict[str, Any] = {"address": share.address()}
    elif cmd == "create":
        if name in state["spaces"]:
            raise SystemExit(f"a shared space named {name!r} already exists")
        out = json.loads(share.create())
        state["spaces"][name] = {k: out[k] for k in ("space_id", "list_id", "cap_id", "owner")}
        if seal:
            state["spaces"][name]["seal"] = seal
        save_state(home, state)
    elif cmd == "join":
        status = json.loads(share.status(args.space))
        state["spaces"][name] = {"space_id": args.space, "list_id": status["list_id"], "owner": status["creator"]}
        if seal:
            state["spaces"][name]["seal"] = seal
        save_state(home, state)
        out = status
    elif cmd in ("add", "remove"):
        space = _space(state, name)
        if not space.get("cap_id"):
            raise SystemExit("only the creator of the space (holder of the allowlist Cap) can change members")
        call = share.add if cmd == "add" else share.remove
        out = json.loads(call(space["list_id"], space["cap_id"], args.member))
    elif cmd == "push":
        space = _space(state, name)
        out = json.loads(share.push(space["space_id"], json.dumps(entries_from_args(args, home, secret))))
    elif cmd == "pull":
        space = _space(state, name)
        out = json.loads(share.pull(space["space_id"], True))
        cache = merge_pull(_load_cache(home, space["space_id"]), out)
        _save_cache(home, space["space_id"], cache)
        print(json.dumps(out, indent=2))
        if out["counts"].get("wrong_owner"):
            return PULL_EXIT_WRONG_OWNER
        return PULL_EXIT_NO_ACCESS if out["counts"].get("no_access") else 0
    elif cmd == "status":
        out = json.loads(share.status(_space(state, name)["space_id"]))
    else:
        raise SystemExit("Usage: share <create|join|add|remove|push|pull|status|whoami>")
    print(json.dumps(out, indent=2))
    return 0


BETA = "BETA (testnet preview)"


def add_arguments(parser) -> None:
    parser.description = f"{BETA}: Seal-encrypted shared spaces on Sui and Walrus testnet. Not for production data."
    subs = parser.add_subparsers(dest="share_command", title=f"share commands, {BETA}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--name", default="default", help="Local name of the shared space")
    common.add_argument("--key-file", default="", help="Signer key file (default: MNEMOS_SHARE_KEY_FILE, else the Mnemos key)")
    subs.add_parser("whoami", parents=[common], help=f"{BETA}: Print the signer address")
    servers = argparse.ArgumentParser(add_help=False)
    servers.add_argument("--committee", action="store_true",
                         help=f"{BETA}: encrypt to the Mysten testnet committee (3-of-5 behind the Mysten aggregator)")
    servers.add_argument("--key-server", action="append", metavar="ID",
                         help="Key server object id (repeatable, replaces the default servers)")
    servers.add_argument("--threshold", type=int, default=None,
                         help="Key servers needed to decrypt (default: 2 of the 2 Mysten open servers, 1 for the committee)")
    subs.add_parser("create", parents=[common, servers],
                    help=f"{BETA}: Create an allowlist and a shared space (you are the first member)")
    join = subs.add_parser("join", parents=[common, servers], help=f"{BETA}: Register a space created by someone else")
    join.add_argument("--space", required=True, help="Space object id")
    verbs = {"add": "Add a member (epoch unchanged)", "remove": "Remove a member (bumps the epoch)"}
    for verb, text in verbs.items():
        sub = subs.add_parser(verb, parents=[common], help=f"{BETA}: {text}")
        sub.add_argument("member", help="Sui address")
    push = subs.add_parser("push", parents=[common], help=f"{BETA}: Seal-encrypt entries, store them on Walrus, publish references")
    push.add_argument("--text", action="append", help="Entry text (repeatable)")
    push.add_argument("--kind", default="fact")
    push.add_argument("--from-memory", default="", help="Share the local memory entries matching this query")
    push.add_argument("--limit", default=5, type=int)
    subs.add_parser("pull", parents=[common], help=f"{BETA}: Fetch keys and decrypt every entry you are allowed to read")
    subs.add_parser("status", parents=[common], help=f"{BETA}: Epoch and entry counts of the space")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mnemos.share")
    parser.add_argument("--home", default=os.environ.get("HERMES_HOME", ""), help="Hermes home (profile directory)")
    add_arguments(parser)
    args = parser.parse_args(argv)
    if not args.home:
        parser.error("--home or HERMES_HOME is required")
    if not args.share_command:
        parser.error("missing share command")
    return run(args, args.home, os.environ.get("MNEMOS_SECRET", ""))


if __name__ == "__main__":
    raise SystemExit(main())
