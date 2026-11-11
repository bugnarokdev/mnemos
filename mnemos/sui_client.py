"""Sui testnet transactions from Python.

The fullnode selects gas during SimulateTransaction. We sign the BCS it
returns and submit ExecuteTransaction. Snapshot bytes themselves are built by
``mnemos.chain`` so they match the Rust core.
"""

from __future__ import annotations

import hashlib
from typing import Any

import grpc
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import sui_exec_pb2 as pb
from . import sui_exec_pb2_grpc as rpc
from .chain import ed25519_public_from_seed


def _channel(rpc_url: str) -> grpc.Channel:
    target = rpc_url.replace("https://", "").replace("http://", "").rstrip("/")
    if ":" not in target:
        target += ":443"
    return grpc.secure_channel(target, grpc.ssl_channel_credentials())


def _stub(rpc_url: str) -> rpc.TransactionExecutionServiceStub:
    return rpc.TransactionExecutionServiceStub(_channel(rpc_url))


# Genesis checkpoint digest of Sui testnet (``4c78adac`` in short form).
TESTNET_CHAIN_ID = "69WiPg3DAQiwdxfncX6wYQ2siKwAe6L9BZthQea3JNMD"
_SERVICE_INFO = "/sui.rpc.v2.LedgerService/GetServiceInfo"


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while True:
        if pos >= len(data) or shift > 63:
            raise ValueError("truncated varint")
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7


def parse_service_info(data: bytes) -> dict[str, str]:
    """Read ``chain_id`` (1) and ``chain`` (2) from a GetServiceInfoResponse."""
    out: dict[str, str] = {}
    pos = 0
    while pos < len(data):
        key, pos = _read_varint(data, pos)
        field, wire = key >> 3, key & 7
        if wire == 0:
            _, pos = _read_varint(data, pos)
        elif wire == 1:
            pos += 8
        elif wire == 5:
            pos += 4
        elif wire == 2:
            length, pos = _read_varint(data, pos)
            chunk = data[pos : pos + length]
            if len(chunk) != length:
                raise ValueError("truncated field")
            pos += length
            if field == 1:
                out["chain_id"] = chunk.decode("utf-8")
            elif field == 2:
                out["chain"] = chunk.decode("utf-8")
        else:
            raise ValueError(f"unsupported wire type {wire}")
    return out


def service_info(channel: grpc.Channel, timeout: float = 15) -> dict[str, str]:
    call = channel.unary_unary(
        _SERVICE_INFO,
        request_serializer=lambda _request: b"",
        response_deserializer=parse_service_info,
    )
    return call(None, timeout=timeout)


def check_rpc(rpc_url: str) -> None:
    """``require_testnet`` on a fresh channel, for a check before any other work."""
    require_testnet(_channel(rpc_url), rpc_url)


def require_testnet(channel: grpc.Channel, rpc_url: str) -> None:
    """Refuse unless the RPC reports the Sui testnet chain id. Errors refuse too."""
    try:
        info = service_info(channel)
    except Exception as exc:
        raise RuntimeError(f"refusing to sign: could not read the chain id of {rpc_url} ({exc.__class__.__name__})") from exc
    chain_id = info.get("chain_id") or ""
    if chain_id != TESTNET_CHAIN_ID:
        name = info.get("chain") or "unknown"
        raise RuntimeError(
            f"refusing to sign: {rpc_url} is on chain {name} ({chain_id or 'no chain id'}), "
            "Mnemos only writes to Sui testnet"
        )


def bcs_uleb(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def bcs_bytes(payload: bytes) -> bytes:
    return bcs_uleb(len(payload)) + payload


def bcs_u64(value: int) -> bytes:
    return int(value).to_bytes(8, "little")


def bcs_address(text: str) -> bytes:
    raw = bytes.fromhex(text.lower().removeprefix("0x"))
    if len(raw) > 32:
        raise ValueError(f"address too long: {text}")
    return raw.rjust(32, b"\x00")


def pure_vec_u8(payload: bytes) -> bytes:
    return bcs_bytes(payload)


def _input_pure(payload: bytes) -> pb.Input:
    return pb.Input(kind=pb.PURE, pure=payload)


def _input_owned(object_id: str, version: int, digest: str) -> pb.Input:
    # Leave `mutable` unset. The fullnode treats an explicit mutable flag on an
    # owned object as an unresolved input and rejects the transaction.
    return pb.Input(
        kind=pb.IMMUTABLE_OR_OWNED,
        object_id=object_id,
        version=int(version),
        digest=digest,
    )


def resolve_owned(graphql_url: str, object_id: str) -> pb.Input:
    from .py_engine import graphql_object

    obj = graphql_object(graphql_url, object_id)
    return _input_owned(object_id, int(obj["version"]), obj["digest"])


def _arg(index: int) -> pb.Argument:
    return pb.Argument(kind=pb.INPUT, input=index)


def move_call(package: str, module: str, function: str, arg_indexes: list[int]) -> pb.Command:
    return pb.Command(
        move_call=pb.MoveCall(
            package=package,
            module=module,
            function=function,
            arguments=[_arg(index) for index in arg_indexes],
        )
    )


def publish_command(modules: list[bytes], dependencies: list[str]) -> pb.Command:
    return pb.Command(publish=pb.Publish(modules=modules, dependencies=dependencies))


def transfer_command(object_result: int, address_input: int) -> pb.Command:
    return pb.Command(
        transfer_objects=pb.TransferObjects(
            objects=[pb.Argument(kind=pb.RESULT, result=object_result)],
            address=_arg(address_input),
        )
    )


def _sign(seed: bytes, transaction_bcs: bytes) -> bytes:
    digest = hashlib.blake2b(b"\x00\x00\x00" + transaction_bcs, digest_size=32).digest()
    signature = Ed25519PrivateKey.from_private_bytes(seed).sign(digest)
    public = ed25519_public_from_seed(seed)
    return b"\x00" + signature + public


def execute(
    rpc_url: str,
    sender: str,
    inputs: list[pb.Input],
    commands: list[pb.Command],
    sign_seed: bytes,
    budget: int,
) -> str:
    kind = pb.TransactionKind(
        kind=pb.PROGRAMMABLE_TRANSACTION,
        programmable_transaction=pb.ProgrammableTransaction(inputs=inputs, commands=commands),
    )
    draft = pb.Transaction(
        sender=sender,
        kind=kind,
        gas_payment=pb.GasPayment(owner=sender, price=1000, budget=budget),
    )
    channel = _channel(rpc_url)
    require_testnet(channel, rpc_url)
    stub = rpc.TransactionExecutionServiceStub(channel)
    simulated = stub.SimulateTransaction(
        pb.SimulateTransactionRequest(
            transaction=draft,
            do_gas_selection=True,
            read_mask=pb.google_dot_protobuf_dot_field__mask__pb2.FieldMask(
                paths=["transaction.transaction.bcs", "transaction.effects", "transaction.digest"]
            ),
        ),
        timeout=90,
    )
    executed = simulated.transaction
    status = executed.effects.status
    if status.HasField("error") or (status.HasField("success") and not status.success):
        detail = status.error.description or "simulation failed"
        raise RuntimeError(detail)
    bcs = executed.transaction.bcs.value
    if not bcs:
        raise RuntimeError("Sui simulation did not return transaction bytes")
    signature = _sign(sign_seed, bcs)
    result = stub.ExecuteTransaction(
        pb.ExecuteTransactionRequest(
            transaction=pb.Transaction(bcs=pb.Bcs(name="TransactionData", value=bcs)),
            signatures=[pb.UserSignature(bcs=pb.Bcs(value=signature))],
            read_mask=pb.google_dot_protobuf_dot_field__mask__pb2.FieldMask(
                paths=["digest", "effects"]
            ),
        ),
        timeout=90,
    )
    done = result.transaction
    done_status = done.effects.status
    if done_status.HasField("error") or (done_status.HasField("success") and not done_status.success):
        detail = done_status.error.description or "execution failed"
        raise RuntimeError(detail)
    digest = done.digest or done.effects.transaction_digest or executed.digest
    if not digest:
        raise RuntimeError("Sui execution returned no digest")
    return digest


def find_owned(graphql_url: str, owner: str, type_suffix: str) -> dict[str, Any] | None:
    from .py_engine import graphql

    query = """
    query($owner: SuiAddress!, $type: String!) {
      objects(first: 10, filter: { owner: $owner, type: $type }) {
        nodes { address asMoveObject { contents { json } } }
      }
    }
    """
    data = graphql(graphql_url, query, {"owner": owner, "type": type_suffix})
    nodes = (((data.get("data") or {}).get("objects") or {}).get("nodes")) or []
    if not nodes:
        return None
    node = nodes[-1]
    contents = ((node.get("asMoveObject") or {}).get("contents") or {})
    return {"address": node.get("address"), "contents": contents}


def read_move_json(graphql_url: str, object_id: str) -> dict[str, Any]:
    from .py_engine import graphql_object

    obj = graphql_object(graphql_url, object_id)
    contents = ((obj.get("asMoveObject") or {}).get("contents") or {}).get("json")
    if not isinstance(contents, dict):
        raise RuntimeError(f"Sui object {object_id} has no Move content")
    return contents
