"""Sui reads: the fullnode first, the GraphQL indexer only when it is provably fresh.

Writes already need the fullnode (``sui_rpc``): the chain guard, gas selection
and execution all go through it. Reading from the same node removes a second
service from the path, and the fullnode is the source the indexer copies from,
so it is never behind it. GraphQL stays as a fallback for a fullnode that
refuses a read (for example a node without the owned-object index). Before a
fallback read, the fullnode's checkpoint and chain id are compared with
GraphQL's; a lag above ``graphql_max_lag`` checkpoints, another chain, or a
check that cannot run refuses the read. Nothing silently reads stale state.
"""

from __future__ import annotations

import time
from typing import Any, Callable

import grpc
from google.protobuf import json_format

from . import sui_client
from . import sui_exec_pb2 as pb
from . import sui_exec_pb2_grpc as rpc

GRAPHQL_MAX_LAG = 100
_OBJECT_PATHS = ["object_id", "version", "digest", "object_type", "json"]
_VERIFIED: dict[str, float] = {}
_VERIFY_TTL = 300.0


class ReadError(RuntimeError):
    pass


def _mode(config: dict[str, Any]) -> str:
    mode = str(config.get("read_source") or "auto").strip().lower()
    if mode not in {"auto", "grpc"}:
        raise ReadError(f"read_source must be auto or grpc, not {mode!r}")
    return mode


def _max_lag(config: dict[str, Any]) -> int:
    try:
        return max(0, int(config.get("graphql_max_lag", GRAPHQL_MAX_LAG)))
    except (TypeError, ValueError):
        return GRAPHQL_MAX_LAG


def _norm(address: str) -> str:
    text = str(address or "").strip().lower().removeprefix("0x")
    if not text or len(text) > 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise ReadError(f"not a Sui address: {address!r}")
    return "0x" + text.rjust(64, "0")


def _channel(config: dict[str, Any]) -> grpc.Channel:
    return sui_client._channel(config["sui_rpc"])


def _verified_channel(config: dict[str, Any]) -> grpc.Channel:
    """A channel to sui_rpc after confirming it is Sui testnet (cached a few minutes)."""
    url = config["sui_rpc"]
    channel = _channel(config)
    if time.monotonic() - _VERIFIED.get(url, -1e9) > _VERIFY_TTL:
        sui_client.require_testnet(channel, url)
        _VERIFIED[url] = time.monotonic()
    return channel


def _object_dict(obj: pb.Object) -> dict[str, Any]:
    contents = json_format.MessageToDict(obj.json) if obj.HasField("json") else None
    return {
        "address": obj.object_id,
        "version": int(obj.version),
        "digest": obj.digest,
        "asMoveObject": {"contents": {"type": {"repr": obj.object_type}, "json": contents}},
        "source": "grpc",
    }


def grpc_object(config: dict[str, Any], object_id: str, timeout: float = 20) -> dict[str, Any]:
    stub = rpc.LedgerServiceStub(_verified_channel(config))
    request = pb.GetObjectRequest(object_id=object_id)
    request.read_mask.paths.extend(_OBJECT_PATHS)
    try:
        reply = stub.GetObject(request, timeout=timeout)
    except grpc.RpcError as exc:
        if exc.code() == grpc.StatusCode.NOT_FOUND:
            raise LookupError(f"Sui object {object_id} was not found") from exc
        raise
    obj = reply.object
    if _norm(obj.object_id) != _norm(object_id):
        raise ReadError(f"fullnode returned object {obj.object_id} for {object_id}")
    return _object_dict(obj)


def grpc_owned(config: dict[str, Any], owner: str, type_name: str, timeout: float = 20) -> list[dict[str, Any]]:
    stub = rpc.StateServiceStub(_verified_channel(config))
    out: list[dict[str, Any]] = []
    token = b""
    for _ in range(20):
        request = pb.ListOwnedObjectsRequest(owner=owner, object_type=type_name, page_size=50)
        request.read_mask.paths.extend(_OBJECT_PATHS)
        if token:
            request.page_token = token
        reply = stub.ListOwnedObjects(request, timeout=timeout)
        out.extend(_object_dict(obj) for obj in reply.objects)
        token = reply.next_page_token
        if not token:
            break
    return out


def grpc_balance(config: dict[str, Any], owner: str, timeout: float = 20) -> int:
    stub = rpc.StateServiceStub(_verified_channel(config))
    reply = stub.GetBalance(pb.GetBalanceRequest(owner=owner, coin_type="0x2::sui::SUI"), timeout=timeout)
    return int(reply.balance.balance)


def check_graphql_fresh(config: dict[str, Any], timeout: float = 10) -> dict[str, Any]:
    """Raise unless GraphQL is on Sui testnet and within graphql_max_lag of the fullnode."""
    from .py_engine import graphql

    try:
        node = sui_client.service_info(_channel(config), timeout=timeout)
    except Exception as exc:
        raise ReadError(f"cannot check GraphQL freshness: fullnode unreachable ({exc.__class__.__name__})") from exc
    if node.get("chain_id") != sui_client.TESTNET_CHAIN_ID:
        raise ReadError("cannot check GraphQL freshness: sui_rpc is not Sui testnet")
    try:
        data = graphql(config["graphql_url"], "{ chainIdentifier checkpoint { sequenceNumber } }", timeout=timeout)
    except Exception as exc:
        raise ReadError(f"GraphQL unreachable ({exc})") from exc
    body = data.get("data") or {}
    if body.get("chainIdentifier") != sui_client.TESTNET_CHAIN_ID:
        raise ReadError(f"GraphQL is on chain {body.get('chainIdentifier')!r}, not Sui testnet")
    indexed = int((body.get("checkpoint") or {}).get("sequenceNumber") or 0)
    lag = int(node.get("checkpoint_height") or 0) - indexed
    if lag > _max_lag(config):
        raise ReadError(
            f"GraphQL is {lag} checkpoints behind the fullnode (limit {_max_lag(config)}); refusing a stale read"
        )
    return {"graphql_checkpoint": indexed, "fullnode_checkpoint": node.get("checkpoint_height"), "lag": lag}


def _with_fallback(config: dict[str, Any], what: str, primary: Callable[[], Any], fallback: Callable[[], Any]) -> Any:
    try:
        return primary()
    except LookupError:
        raise
    except sui_client.ChainError:
        raise
    except Exception as exc:
        if _mode(config) == "grpc":
            raise ReadError(f"{what}: fullnode read failed ({exc.__class__.__name__}: {_detail(exc)})") from exc
        try:
            check_graphql_fresh(config)
        except ReadError as stale:
            raise ReadError(f"{what}: fullnode read failed ({_detail(exc)}) and {stale}") from exc
        return fallback()


def _detail(exc: Exception) -> str:
    if isinstance(exc, grpc.RpcError):
        return f"{exc.code().name} {exc.details() or ''}".strip()
    return str(exc)


def get_object(config: dict[str, Any], object_id: str, timeout: float = 20) -> dict[str, Any]:
    from .py_engine import graphql_object

    def fallback() -> dict[str, Any]:
        obj = dict(graphql_object(config["graphql_url"], object_id, timeout=timeout))
        obj["source"] = "graphql"
        return obj

    return _with_fallback(config, f"read {object_id}", lambda: grpc_object(config, object_id, timeout), fallback)


def owned(config: dict[str, Any], owner: str, type_name: str) -> list[dict[str, Any]]:
    from .py_engine import graphql

    def fallback() -> list[dict[str, Any]]:
        query = """
        query($owner: SuiAddress!, $type: String!) {
          objects(first: 50, filter: { owner: $owner, type: $type }) {
            nodes { address version digest asMoveObject { contents { type { repr } json } } }
          }
        }
        """
        data = graphql(config["graphql_url"], query, {"owner": owner, "type": type_name})
        nodes = (((data.get("data") or {}).get("objects") or {}).get("nodes")) or []
        return [dict(node, source="graphql") for node in nodes]

    return _with_fallback(config, f"list {type_name}", lambda: grpc_owned(config, owner, type_name), fallback)


def balance(config: dict[str, Any], owner: str) -> int:
    from .faucet import graphql_balance

    return _with_fallback(
        config, "balance", lambda: grpc_balance(config, owner), lambda: graphql_balance(config["graphql_url"], owner)
    )


def move_json(obj: dict[str, Any]) -> dict[str, Any]:
    contents = ((obj.get("asMoveObject") or {}).get("contents") or {}).get("json")
    if not isinstance(contents, dict):
        raise ReadError(f"Sui object {obj.get('address')} has no Move content")
    return contents


def move_type(obj: dict[str, Any]) -> str:
    return str((((obj.get("asMoveObject") or {}).get("contents") or {}).get("type") or {}).get("repr") or "")


def memory_index(
    config: dict[str, Any], object_id: str, timeout: float = 20, check_package: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read a MemoryIndex and fail closed if the object is anything else."""
    obj = get_object(config, object_id, timeout)
    type_repr = move_type(obj)
    if not type_repr.endswith("::memory_index::MemoryIndex"):
        raise ReadError(f"Sui object {object_id} is a {type_repr or 'non-Move object'}, not a MemoryIndex")
    package = str(config.get("package_id") or "").strip()
    if check_package and package and _norm(type_repr.split("::")[0]) != _norm(package):
        raise ReadError(f"MemoryIndex {object_id} belongs to package {type_repr.split('::')[0]}, not {package}")
    return obj, move_json(obj)
