"""BETA (testnet preview): the sharing lane in pure Python, on the sui-seal-client bindings.

Same spaces, same on-chain calls, same ``share.json`` and same encrypted entry format as
the native lane (``native/src/share.rs``), so Rust and Python members share one space.
Seal itself (encryption, session keys, key requests, decryption) runs in the
sui-seal-client v0.2.0 Python bindings, pinned by commit and built with
``scripts/build_seal_py.sh``. Sui transactions use ``mnemos.sui_client`` like the
personal Python backend. Testnet only; nothing here is production software.

Like the native lane, this module never reads or writes the personal MemoryIndex, the
local store or ``config.json``. Keys fetched from key servers stay in memory.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from typing import Any

from . import chain, reads, sui_client
from . import sui_exec_pb2 as pb
from . import sui_exec_pb2_grpc as rpc
from .py_engine import DEFAULTS, walrus_get, walrus_put

# Same constants as native/src/share.rs.
DEFAULT_SEAL_PACKAGE = "0xe10fbae08333748f310adc926904980c6ced589a75a774bff4dfc462567d7f1b"
DEFAULT_SPACE_PACKAGE = "0xa40e74c6ae866f18e86e9dcdfc9d29af436597264991fc81b4a289519f3500d1"
NONCE_LEN = 16
ENTRY_FORMAT = "mnemos-share-entry-v1"
SESSION_TTL_MIN = 10
GAS_BUDGET = 50_000_000
PROFILE_TESTNET = "testnet"
PROFILE_COMMITTEE = "committee"
# Mysten testnet key servers (sui-seal-client v0.2.0 constants).
MYSTEN_TESTNET_1 = "0x73d05d62c18d9374e3ea529e8e0ed6161da1a141a94d3f76ae3fe4e99356db75"
MYSTEN_TESTNET_2 = "0xf5d14a81a982144ae441cd7d64b09027f116a468bd36e7eca494f750591623c8"
MYSTEN_TESTNET_COMMITTEE = "0xb012378c9f3799fb5b1a7083da74a4069e3c3f1c93de0b27212a5799ce1e1e98"
MYSTEN_TESTNET_AGGREGATOR = "https://seal-aggregator-testnet.mystenlabs.com"
SHARED_OWNER_KIND = 3  # sui.rpc.v2 Owner.OwnerKind.SHARED
_HEX = set("0123456789abcdefABCDEF")


def bindings():
    """The sui-seal-client Python module, or ImportError."""
    import sui_seal_client

    return sui_seal_client


class ShareError(RuntimeError):
    """Every failure of this lane (the native lane raises RuntimeError too)."""


def parse_address(raw: Any, what: str = "address") -> str:
    """Like ``sui_sdk_types::Address::from_str``: optional ``0x``, 1 to 64 hex digits."""
    text = raw if isinstance(raw, str) else ""
    body = text[2:] if text.startswith("0x") else text
    if not body or len(body) > 64 or any(ch not in _HEX for ch in body):
        raise ShareError(what)
    return "0x" + body.lower().rjust(64, "0")


def _type_package(type_repr: str) -> str:
    head = type_repr.split("::", 1)[0]
    try:
        return parse_address(head)
    except ShareError:
        return head


def _text(value: dict, key: str, default: str) -> str:
    item = value.get(key)
    return item if isinstance(item, str) and item else default


def _uint(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def transport_config(raw: str) -> dict[str, Any]:
    """Transport settings, with the defaults of ``engine::parse_config``."""
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        value = None
    value = value if isinstance(value, dict) else {}
    cfg = dict(DEFAULTS)
    for key in ("sui_rpc", "graphql_url", "walrus_publisher", "walrus_aggregator", "read_source"):
        cfg[key] = _text(value, key, DEFAULTS[key])
    cfg["read_source"] = cfg["read_source"].lower()
    for key in ("epochs", "graphql_max_lag"):
        item = value.get(key)
        if isinstance(item, str) and item.isdigit():
            item = int(item)
        cfg[key] = _uint(item) if _uint(item) is not None else DEFAULTS[key]
    return cfg


class ShareConfig:
    """Port of ``ShareConfig`` in native/src/share.rs (same keys, defaults and refusals)."""

    def __init__(self, seal_package: str, space_package: str, profile: str, key_servers: list[str],
                 threshold: int, aggregators: dict[str, str]):
        self.seal_package = seal_package
        self.space_package = space_package
        self.profile = profile
        self.key_servers = key_servers
        self.threshold = threshold
        self.aggregators = aggregators

    @classmethod
    def from_json(cls, raw: str) -> "ShareConfig":
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            value = None
        value = value if isinstance(value, dict) else {}
        seal_package = parse_address(_text(value, "seal_package", DEFAULT_SEAL_PACKAGE), "seal_package")
        space_package = parse_address(_text(value, "space_package", DEFAULT_SPACE_PACKAGE), "space_package")
        profile = _text(value, "key_server_profile", PROFILE_TESTNET)
        if profile == PROFILE_TESTNET:
            profile_servers, profile_threshold = [MYSTEN_TESTNET_1, MYSTEN_TESTNET_2], 2
        elif profile == PROFILE_COMMITTEE:
            profile_servers, profile_threshold = [MYSTEN_TESTNET_COMMITTEE], 1
        else:
            raise ShareError(f"key_server_profile {json.dumps(profile)}: expected \"testnet\" or \"committee\"")
        servers_raw = value.get("key_servers")
        if servers_raw is None:
            key_servers = profile_servers
        elif isinstance(servers_raw, list) and servers_raw:
            key_servers = [parse_address(item, "key_servers") for item in servers_raw]
        else:
            raise ShareError("key_servers must be a non-empty list of key server object ids")
        if len(set(key_servers)) != len(key_servers):
            raise ShareError("key_servers lists the same server twice (weighted servers are not supported)")
        threshold_raw = value.get("threshold")
        if threshold_raw is None:
            threshold = profile_threshold
        else:
            threshold = _uint(threshold_raw)
            if threshold is None:
                raise ShareError("threshold must be a positive integer")
            if threshold > 255:
                raise ShareError("threshold out of range")
        if threshold == 0 or threshold > len(key_servers):
            raise ShareError(f"threshold {threshold} must be between 1 and {len(key_servers)}")
        aggregators = {MYSTEN_TESTNET_COMMITTEE: MYSTEN_TESTNET_AGGREGATOR}
        agg_raw = value.get("aggregators")
        if isinstance(agg_raw, dict):
            for key, url in agg_raw.items():
                server = parse_address(key, "aggregators: key server id")
                if not isinstance(url, str):
                    raise ShareError(f"aggregators: URL of {server} must be a string")
                if not url.startswith("https://") or len(url) <= len("https://"):
                    raise ShareError(f"aggregators: URL of {server} must start with https://")
                aggregators[server] = url.rstrip("/")
        elif agg_raw is not None:
            raise ShareError("aggregators must be an object {key server id: https URL}")
        return cls(seal_package, space_package, profile, key_servers, threshold, aggregators)

    def trusted_servers(self) -> set[str]:
        return set(self.key_servers) | set(self.aggregators) | {MYSTEN_TESTNET_1, MYSTEN_TESTNET_2, MYSTEN_TESTNET_COMMITTEE}

    def seal_json(self) -> dict[str, Any]:
        return {"key_server_profile": self.profile, "key_servers": list(self.key_servers), "threshold": self.threshold}


def server_set_from_header(servers: list[str], threshold: int, trusted: set[str]) -> tuple[tuple[str, ...], int]:
    """Port of ``ServerSet::from_header``."""
    servers = [parse_address(item, "key server") for item in servers]
    if len(set(servers)) != len(servers):
        raise ShareError("weighted key servers are not supported by this preview")
    if threshold == 0 or threshold > len(servers):
        raise ShareError(f"header threshold {threshold} is not between 1 and {len(servers)}")
    for server in servers:
        if server not in trusted:
            raise ShareError(f"untrusted key server {server} (add it to key_servers or aggregators to accept it)")
    return tuple(servers), int(threshold)


def _set_json(server_set: tuple[tuple[str, ...], int]) -> dict[str, Any]:
    return {"key_servers": list(server_set[0]), "threshold": server_set[1]}


def key_id(list_id: str, epoch: int, nonce: bytes) -> bytes:
    """``[list id (32)][epoch (u64 LE)][nonce]``, the id format ``allowlist::seal_approve`` checks."""
    return sui_client.bcs_address(parse_address(list_id)) + int(epoch).to_bytes(8, "little") + bytes(nonce)


def parse_key_id(value: bytes) -> tuple[str, int]:
    if len(value) <= 40:
        raise ShareError(f"key id has {len(value)} bytes, expected more than 40")
    return "0x" + value[:32].hex(), int.from_bytes(value[32:40], "little")


def _dumps(value) -> str:
    """Serialise like serde_json's `json!(..).to_string()`: sorted keys, compact, raw UTF-8."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def entry_plaintext(text: str, kind: str, author: str, ts_ms: int) -> bytes:
    """Byte-identical to the native lane (serde_json: sorted keys, compact, raw UTF-8)."""
    doc = {"format": ENTRY_FORMAT, "text": text, "kind": kind, "author": author, "ts": int(ts_ms)}
    return _dumps(doc).encode("utf-8")


def parse_entry_plaintext(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ShareError("entry plaintext is not JSON") from exc
    if not isinstance(value, dict) or value.get("format") != ENTRY_FORMAT:
        fmt = value.get("format") if isinstance(value, dict) else None
        raise ShareError(f"entry plaintext has format {json.dumps(fmt)}, expected {ENTRY_FORMAT}")
    return value


def _as_u64(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def _norm(address: Any) -> str:
    try:
        return reads._norm(address if isinstance(address, str) else "")
    except reads.ReadError as exc:
        raise ShareError(str(exc)) from exc


def parse_space(value: dict[str, Any]) -> dict[str, Any]:
    """Port of ``parse_space``: the JSON of a ``space::Space``, with id checks."""
    list_id = _norm(value.get("list_id"))
    creator = _norm(value.get("creator"))
    entries = []
    for index, item in enumerate(value.get("entries") or []):
        blob_sha256 = chain.as_bytes(item.get("blob_sha256"))
        if len(blob_sha256) != 32:
            raise ShareError(f"entry {index}: blob_sha256 is not 32 bytes")
        kid = chain.as_bytes(item.get("key_id"))
        epoch = _as_u64(item.get("epoch"))
        list_of_id, id_epoch = parse_key_id(kid)
        if list_of_id != list_id or id_epoch != epoch:
            raise ShareError(f"entry {index}: key id does not match the space allowlist and epoch")
        entries.append({
            "index": index,
            "blob_id": str(item.get("blob_id") or ""),
            "key_id": kid,
            "epoch": epoch,
            "author": _norm(item.get("author")),
            "blob_sha256": blob_sha256,
        })
    return {"list_id": list_id, "creator": creator, "entries": entries}


def _bech32_decode(text: str) -> tuple[str, bytes]:
    charset = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
    text = text.strip().lower()
    hrp, _, data = text.rpartition("1")
    if not hrp or len(data) < 6 or any(ch not in charset for ch in data):
        raise ShareError("sui key: not a bech32 string")
    values = [charset.index(ch) for ch in data]
    gen = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for value in [ord(ch) >> 5 for ch in hrp] + [0] + [ord(ch) & 31 for ch in hrp] + values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ value
        for i in range(5):
            chk ^= gen[i] if (top >> i) & 1 else 0
    if chk != 1:
        raise ShareError("sui key: bad bech32 checksum")
    acc, bits, out = 0, 0, bytearray()
    for value in values[:-6]:
        acc = (acc << 5) | value
        bits += 5
        if bits >= 8:
            bits -= 8
            out.append((acc >> bits) & 0xFF)
    return hrp, bytes(out)


def load_seed(signer_key: str, mnemos_secret: str) -> bytes:
    """Port of ``Share::new`` + ``engine::load_sui_key``: suiprivkey, keystore base64, 32-byte
    hex, else the Sui key derived from ``MNEMOS_SECRET``. Only Ed25519 keys."""
    explicit = (signer_key or "").strip()
    if not explicit:
        if not mnemos_secret:
            raise ShareError("no signer: pass a share key or MNEMOS_SECRET")
        return bytes(chain.derive_keys(mnemos_secret)["sui_seed"])
    if explicit.startswith("suiprivkey"):
        hrp, raw = _bech32_decode(explicit)
        if hrp != "suiprivkey" or len(raw) != 33 or raw[0] != 0:
            raise ShareError("sui key: not an Ed25519 suiprivkey")
        return raw[1:]
    try:
        raw = base64.b64decode(explicit, validate=True)
    except ValueError:
        raw = b""
    if len(raw) == 33 and raw[0] == 0:
        return raw[1:]
    try:
        raw = bytes.fromhex(explicit.removeprefix("0x"))
    except ValueError:
        raw = b""
    if len(raw) == 32:
        return raw
    raise ShareError("MNEMOS_SUI_PRIVATE_KEY must be a suiprivkey, keystore base64, or 32-byte hex seed")


def _shared_input(cfg: dict[str, Any], object_id: str, mutable: bool) -> pb.Input:
    """A shared object input at its initial shared version, read from the fullnode."""
    stub = rpc.LedgerServiceStub(reads._verified_channel(cfg))
    request = pb.GetObjectRequest(object_id=object_id)
    request.read_mask.paths.extend(["object_id", "owner"])
    owner = stub.GetObject(request, timeout=20).object.owner
    if owner.kind != SHARED_OWNER_KIND:
        raise ShareError(f"{object_id} is not a shared object")
    return pb.Input(kind=pb.SHARED, object_id=object_id, version=int(owner.version), mutable=mutable)


def _input_arg(index: int) -> pb.Argument:
    return pb.Argument(kind=pb.INPUT, input=index)


def _nested(command: int, sub: int) -> pb.Argument:
    return pb.Argument(kind=pb.RESULT, result=command, subresult=sub)


def _call(package: str, module: str, function: str, args: list[pb.Argument]) -> pb.Command:
    return pb.Command(move_call=pb.MoveCall(package=package, module=module, function=function, arguments=args))


def _now_ms() -> int:
    return int(time.time() * 1000)


class Share:
    """Same methods and JSON results as the native ``mnemos_core.Share``."""

    backend = "python"

    def __init__(self, config_json: str, signer_key: str = "", mnemos_secret: str = ""):
        self._seal = bindings()
        self._cfg = transport_config(config_json)
        self._share = ShareConfig.from_json(config_json)
        self._seed = load_seed(signer_key, mnemos_secret)
        self._address = chain.sui_address(chain.ed25519_public_from_seed(self._seed))
        keystore = base64.b64encode(b"\x00" + self._seed).decode("ascii")
        self._signer = self._seal.Signer.from_str(keystore)
        if parse_address(self._signer.address) != self._address:
            raise ShareError("signer address mismatch between Mnemos and sui-seal-client")
        self._clients: dict[tuple[tuple[str, ...], int], Any] = {}
        self._session = None
        self._keys: dict[bytes, Any] = {}

    def address(self) -> str:
        return self._address

    # -- reads -------------------------------------------------------------------------
    def _object(self, object_id: str) -> dict[str, Any]:
        try:
            return reads.get_object(self._cfg, object_id)
        except (LookupError, RuntimeError) as exc:
            raise ShareError(str(exc)) from exc

    def _list_epoch(self, list_id: str) -> int:
        obj = self._object(list_id)
        type_repr = reads.move_type(obj)
        if not type_repr.endswith("::allowlist::Allowlist"):
            raise ShareError(f"{list_id} is a {type_repr}, not an allowlist")
        if _type_package(type_repr) != self._share.seal_package:
            raise ShareError(f"allowlist {list_id} is not from package {self._share.seal_package}")
        return _as_u64(reads.move_json(obj).get("epoch"))

    def _space(self, space_id: str) -> dict[str, Any]:
        obj = self._object(space_id)
        type_repr = reads.move_type(obj)
        expected = f"{self._share.space_package}::space::Space"
        if type_repr.split("::", 1)[-1] != "space::Space" or _type_package(type_repr) != self._share.space_package:
            raise ShareError(f"{space_id} is a {type_repr}, not a {expected}")
        return parse_space(reads.move_json(obj))

    def _wait_readable(self, object_id: str) -> None:
        last = ""
        for _ in range(20):
            try:
                reads.get_object(self._cfg, object_id)
                return
            except Exception as exc:  # noqa: BLE001 - retried, last error reported
                last = str(exc)
            time.sleep(1)
        raise ShareError(f"{object_id} was not readable: {last}")

    # -- Seal --------------------------------------------------------------------------
    def _seal_client(self, server_set: tuple[tuple[str, ...], int]):
        client = self._clients.get(server_set)
        if client is not None:
            return client
        servers, threshold = server_set
        entries: list[Any] = []
        for server in servers:
            url = self._share.aggregators.get(server)
            entries.append({"object_id": server, "aggregator_url": url} if url else server)
        try:
            config = self._seal.SealConfig(entries, threshold, fullnode_url=self._cfg["sui_rpc"])
        except self._seal.SealError as exc:
            raise ShareError(f"seal config: {exc}") from exc
        sui_client.check_rpc(self._cfg["sui_rpc"])
        try:
            client = self._seal.SealClient.connect(config)
        except self._seal.SealError as exc:
            raise ShareError(f"seal: {exc}") from exc
        self._clients[server_set] = client
        return client

    def _write_set(self) -> tuple[tuple[str, ...], int]:
        return tuple(self._share.key_servers), self._share.threshold

    def _servers_for(self, kid: bytes) -> int:
        keys = self._keys.get(kid)
        return keys.servers_for(self._share.seal_package, kid) if keys is not None else 0

    def _fetch(self, client, list_arg, ids: list[bytes]):
        approval = self._seal.ApprovalTx(self._share.seal_package)
        for kid in ids:
            approval.call("allowlist", "seal_approve", kid, [list_arg])
        if self._session is None or self._session.is_expired():
            self._session = client.session_key(self._share.seal_package, SESSION_TTL_MIN, self._signer)
        keys = client.fetch_keys(self._session, approval)
        for kid in ids:
            self._keys[kid] = keys

    # -- transactions ------------------------------------------------------------------
    def _execute(self, inputs: list[pb.Input], commands: list[pb.Command]) -> dict[str, Any]:
        try:
            return sui_client.execute_with_effects(
                self._cfg["sui_rpc"], self._address, inputs, commands, self._seed, GAS_BUDGET
            )
        except sui_client.ChainError:
            raise
        except Exception as exc:
            raise ShareError(f"transaction failed: {exc}") from exc

    def create(self) -> str:
        seal_pkg, space_pkg = self._share.seal_package, self._share.space_package
        inputs = [pb.Input(kind=pb.PURE, pure=sui_client.bcs_address(self._address))]
        cap, lst, me = _nested(0, 0), _nested(0, 1), _input_arg(0)
        commands = [
            _call(seal_pkg, "allowlist", "create", []),
            _call(seal_pkg, "allowlist", "add", [lst, cap, me]),
            _call(space_pkg, "space", "create", [lst]),
            _call(seal_pkg, "allowlist", "share", [lst]),
            pb.Command(transfer_objects=pb.TransferObjects(objects=[cap], address=me)),
        ]
        done = self._execute(inputs, commands)
        digest = done["digest"]

        def find(suffix: str) -> str:
            found = [item["object_id"] for item in done["created"] if str(item.get("object_type", "")).endswith(suffix)]
            if len(found) != 1:
                raise ShareError(f"transaction {digest} created {len(found)} {suffix}")
            return _norm(found[0])

        list_id, cap_id, space_id = find("::allowlist::Allowlist"), find("::allowlist::Cap"), find("::space::Space")
        self._wait_readable(space_id)
        return _dumps({
            "space_id": space_id, "list_id": list_id, "cap_id": cap_id, "owner": self._address, "epoch": 0,
            "digest": digest, "seal_package": seal_pkg, "space_package": space_pkg, "seal": self._share.seal_json(),
        })

    def _admin(self, function: str, list_id: str, cap_id: str, member: str) -> str:
        list_id = parse_address(list_id, "list id")
        cap_id = parse_address(cap_id, "cap id")
        member = parse_address(member, "member address")
        before = self._list_epoch(list_id)
        inputs = [
            _shared_input(self._cfg, list_id, True),
            sui_client.resolve_owned(self._cfg, cap_id),
            pb.Input(kind=pb.PURE, pure=sui_client.bcs_address(member)),
        ]
        commands = [_call(self._share.seal_package, "allowlist", function, [_input_arg(0), _input_arg(1), _input_arg(2)])]
        digest = self._execute(inputs, commands)["digest"]
        expected = before + 1 if function == "remove" else before
        epoch = before
        for _ in range(20):
            epoch = self._list_epoch(list_id)
            if epoch == expected:
                break
            time.sleep(1)
        if epoch != expected:
            raise ShareError(f"{function} in {digest}: epoch is {epoch}, expected {expected}")
        return _dumps({"digest": digest, "member": member, "epoch_before": before, "epoch": epoch})

    def add(self, list_id: str, cap_id: str, member: str) -> str:
        return self._admin("add", list_id, cap_id, member)

    def remove(self, list_id: str, cap_id: str, member: str) -> str:
        return self._admin("remove", list_id, cap_id, member)

    def push(self, space_id: str, entries_json: str) -> str:
        try:
            items = json.loads(entries_json)
        except ValueError as exc:
            raise ShareError(f"entries json: {exc}") from exc
        if not isinstance(items, list) or not items:
            raise ShareError("nothing to push")
        state = self._space(space_id)
        list_id = state["list_id"]
        epoch = self._list_epoch(list_id)
        client = self._seal_client(self._write_set())
        prepared = []
        for item in items:
            text = item.get("text") if isinstance(item, dict) else None
            if not isinstance(text, str):
                raise ShareError("entry without text")
            kind = item.get("kind") if isinstance(item.get("kind"), str) else "fact"
            kid = key_id(list_id, epoch, secrets.token_bytes(NONCE_LEN))
            plaintext = entry_plaintext(text, kind, self._address, _now_ms())
            try:
                ciphertext = client.encrypt(self._share.seal_package, kid, plaintext)
            except self._seal.SealError as exc:
                raise ShareError(f"seal encrypt: {exc}") from exc
            digest = hashlib.sha256(ciphertext).digest()
            blob_id = walrus_put(self._cfg["walrus_publisher"], ciphertext, int(self._cfg["epochs"]))
            prepared.append((blob_id, kid, digest))
        space_addr = parse_address(space_id, "space id")
        inputs = [_shared_input(self._cfg, space_addr, True), _shared_input(self._cfg, list_id, False)]
        commands = []
        for blob_id, kid, digest in prepared:
            base = len(inputs)
            inputs += [
                pb.Input(kind=pb.PURE, pure=sui_client.bcs_bytes(blob_id.encode("utf-8"))),
                pb.Input(kind=pb.PURE, pure=sui_client.bcs_bytes(kid)),
                pb.Input(kind=pb.PURE, pure=sui_client.bcs_bytes(digest)),
            ]
            commands.append(_call(self._share.space_package, "space", "publish",
                                  [_input_arg(0), _input_arg(1), _input_arg(base), _input_arg(base + 1), _input_arg(base + 2)]))
        tx_digest = self._execute(inputs, commands)["digest"]
        first = len(state["entries"])
        published = [{"index": first + offset, "blob_id": blob_id, "key_id": kid.hex(), "epoch": epoch,
                      "blob_sha256": digest.hex()} for offset, (blob_id, kid, digest) in enumerate(prepared)]
        expected = first + len(prepared)
        for _ in range(20):
            if len(self._space(space_id)["entries"]) >= expected:
                break
            time.sleep(1)
        return _dumps({"digest": tx_digest, "epoch": epoch, "entries": published, "seal": self._share.seal_json()})

    def pull(self, space_id: str, fetch: bool = True) -> str:
        seal = self._seal
        state = self._space(space_id)
        list_id = state["list_id"]
        trusted = self._share.trusted_servers()
        blobs: list[bytes] = []
        sets: list[Any] = []
        for entry in state["entries"]:
            blob = walrus_get(self._cfg["walrus_aggregator"], entry["blob_id"])
            if hashlib.sha256(blob).digest() != entry["blob_sha256"]:
                raise ShareError(f"entry {entry['index']}: Walrus blob {entry['blob_id']} does not match its sha256")
            try:
                info = seal.parse_encrypted_object(blob)
            except seal.SealError as exc:
                raise ShareError(f"entry {entry['index']}: {exc}") from exc
            if parse_address(info.package_id) != self._share.seal_package or bytes(info.id) != entry["key_id"]:
                raise ShareError(f"entry {entry['index']}: ciphertext header does not match the published key id")
            try:
                sets.append(server_set_from_header(list(info.key_servers), int(info.threshold), trusted))
            except ShareError as exc:
                sets.append(str(exc))
            blobs.append(blob)
        groups = sorted({item for item in sets if isinstance(item, tuple)})
        clients = {group: self._seal_client(group) for group in groups}
        refused: list[int] = []
        wrong_owner: list[int] = []
        errors: dict[int, str] = {}
        if fetch and groups:
            list_arg = None
            for group in groups:
                client = clients[group]
                missing = [entry["index"] for entry, item in zip(state["entries"], sets)
                           if item == group and self._servers_for(entry["key_id"]) < group[1]]
                if not missing:
                    continue
                if list_arg is None:
                    try:
                        list_arg = client.shared_object(list_id)
                    except seal.SealError as exc:
                        raise ShareError(f"allowlist: {exc}") from exc
                ids = [state["entries"][index]["key_id"] for index in missing]
                try:
                    self._fetch(client, list_arg, ids)
                except seal.WrongOwnerError:
                    wrong_owner.extend(missing)
                except seal.NoAccessError:
                    # Classify entry by entry: some ids may be approvable, others not.
                    for index in missing:
                        try:
                            self._fetch(client, list_arg, [state["entries"][index]["key_id"]])
                        except seal.NoAccessError:
                            refused.append(index)
                        except seal.WrongOwnerError:
                            wrong_owner.append(index)
                        except seal.SealError as exc:
                            errors[index] = str(exc)
                except seal.SealError as exc:
                    raise ShareError(f"fetch keys: {exc}") from exc
        out = []
        counts = {"ok": 0, "no_access": 0, "wrong_owner": 0, "no_cached_key": 0, "error": 0}
        for entry, blob, item in zip(state["entries"], blobs, sets):
            row: dict[str, Any] = {
                "index": entry["index"], "epoch": entry["epoch"], "author": entry["author"],
                "blob_id": entry["blob_id"], "key_id": entry["key_id"].hex(),
            }
            if isinstance(item, str):
                row["error"] = item
                status = "error"
            else:
                row["seal"] = _set_json(item)
                if entry["index"] in refused:
                    status = "no_access"
                elif entry["index"] in wrong_owner:
                    row["error"] = "the key request used an object owned by another address; check the space and allowlist ids"
                    status = "wrong_owner"
                elif entry["index"] in errors:
                    row["error"] = errors[entry["index"]]
                    status = "error"
                elif self._servers_for(entry["key_id"]) < item[1]:
                    status = "no_cached_key"
                else:
                    try:
                        value = parse_entry_plaintext(bytes(clients[item].decrypt(blob, self._keys[entry["key_id"]])))
                    except (seal.SealError, ShareError) as exc:
                        row["error"] = str(exc)
                        status = "error"
                    else:
                        row["text"], row["kind"], row["ts"] = value.get("text"), value.get("kind"), value.get("ts")
                        author = value.get("author")
                        if (_norm(author) if isinstance(author, str) else None) != entry["author"]:
                            row["error"] = "author in plaintext does not match the publisher"
                            status = "error"
                        else:
                            status = "ok"
            row["status"] = status
            counts[status] += 1
            out.append(row)
        return _dumps({"space_id": space_id, "list_id": list_id, "reader": self._address, "fetched": fetch,
                           "entries": out, "counts": counts})

    def status(self, space_id: str) -> str:
        state = self._space(space_id)
        epoch = self._list_epoch(state["list_id"])
        per_epoch: dict[str, int] = {}
        for entry in sorted(state["entries"], key=lambda item: item["epoch"]):
            per_epoch[str(entry["epoch"])] = per_epoch.get(str(entry["epoch"]), 0) + 1
        return _dumps({
            "space_id": space_id, "list_id": state["list_id"], "creator": state["creator"], "epoch": epoch,
            "entries": len(state["entries"]), "entries_per_epoch": per_epoch, "address": self._address,
            "seal": self._share.seal_json(),
        })
