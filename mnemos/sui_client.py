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
    stub = _stub(rpc_url)
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
