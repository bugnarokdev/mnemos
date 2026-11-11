"""Fullnode-first reads with a GraphQL fallback that refuses stale data."""

import grpc
import pytest

from mnemos import py_engine, reads, sui_client

CFG = {
    "sui_rpc": "https://rpc.invalid",
    "graphql_url": "https://gql.invalid",
    "read_source": "auto",
    "graphql_max_lag": 100,
    "package_id": "0xabc",
}
TYPE = "0xabc::memory_index::MemoryIndex"


def _obj(source, type_repr=TYPE):
    return {"address": "0x1", "version": 3, "digest": "d", "source": source,
            "asMoveObject": {"contents": {"type": {"repr": type_repr}, "json": {"version": "0"}}}}


class _Rpc(grpc.RpcError):
    def __init__(self, code):
        self._code = code

    def code(self):
        return self._code

    def details(self):
        return "boom"


def _fresh(monkeypatch, node_height=1000, indexed=990, chain=sui_client.TESTNET_CHAIN_ID, node_chain=None):
    monkeypatch.setattr(reads, "_channel", lambda config: object())
    monkeypatch.setattr(
        sui_client,
        "service_info",
        lambda channel, timeout=15: {"chain_id": node_chain or sui_client.TESTNET_CHAIN_ID, "checkpoint_height": node_height},
    )
    calls = []

    def fake_graphql(url, query, variables=None, timeout=20):
        calls.append(query)
        return {"data": {"chainIdentifier": chain, "checkpoint": {"sequenceNumber": str(indexed)}}}

    monkeypatch.setattr(py_engine, "graphql", fake_graphql)
    monkeypatch.setattr(py_engine, "graphql_object", lambda url, oid, timeout=20: _obj("graphql"))
    return calls


def _grpc_fails(monkeypatch, exc=None):
    def boom(config, oid, timeout=20):
        raise exc or _Rpc(grpc.StatusCode.UNAVAILABLE)

    monkeypatch.setattr(reads, "grpc_object", boom)


def test_fullnode_is_used_first(monkeypatch):
    monkeypatch.setattr(reads, "grpc_object", lambda config, oid, timeout=20: _obj("grpc"))
    monkeypatch.setattr(py_engine, "graphql_object", lambda *a, **k: pytest.fail("GraphQL must not be read"))
    assert reads.get_object(CFG, "0x1")["source"] == "grpc"


def test_fresh_graphql_is_the_fallback(monkeypatch):
    _grpc_fails(monkeypatch)
    _fresh(monkeypatch, node_height=1000, indexed=950)
    assert reads.get_object(CFG, "0x1")["source"] == "graphql"


def test_stale_graphql_is_refused(monkeypatch):
    _grpc_fails(monkeypatch)
    _fresh(monkeypatch, node_height=405415078, indexed=405395971)
    with pytest.raises(reads.ReadError, match="19107 checkpoints behind"):
        reads.get_object(CFG, "0x1")


def test_lag_limit_is_configurable(monkeypatch):
    _grpc_fails(monkeypatch)
    _fresh(monkeypatch, node_height=1000, indexed=950)
    with pytest.raises(reads.ReadError, match="limit 10"):
        reads.get_object({**CFG, "graphql_max_lag": 10}, "0x1")


def test_graphql_on_another_chain_is_refused(monkeypatch):
    _grpc_fails(monkeypatch)
    _fresh(monkeypatch, chain="4btiuiMPvEENsttpZC7CZ53DruC3MAgfznDbASZ7DR6S")
    with pytest.raises(reads.ReadError, match="not Sui testnet"):
        reads.get_object(CFG, "0x1")


def test_unverifiable_freshness_is_refused(monkeypatch):
    _grpc_fails(monkeypatch)
    _fresh(monkeypatch)

    def down(channel, timeout=15):
        raise TimeoutError("down")

    monkeypatch.setattr(sui_client, "service_info", down)
    with pytest.raises(reads.ReadError, match="cannot check GraphQL freshness"):
        reads.get_object(CFG, "0x1")


def test_grpc_only_mode_never_falls_back(monkeypatch):
    _grpc_fails(monkeypatch)
    calls = _fresh(monkeypatch)
    with pytest.raises(reads.ReadError, match="fullnode read failed"):
        reads.get_object({**CFG, "read_source": "grpc"}, "0x1")
    assert calls == []


def test_not_found_is_final(monkeypatch):
    _grpc_fails(monkeypatch, LookupError("Sui object 0x1 was not found"))
    calls = _fresh(monkeypatch)
    with pytest.raises(LookupError):
        reads.get_object(CFG, "0x1")
    assert calls == []


def test_chain_guard_failure_is_not_retried_on_graphql(monkeypatch):
    _grpc_fails(monkeypatch, sui_client.ChainError("refusing to sign: mainnet"))
    calls = _fresh(monkeypatch)
    with pytest.raises(sui_client.ChainError):
        reads.get_object(CFG, "0x1")
    assert calls == []


def test_memory_index_type_is_checked(monkeypatch):
    monkeypatch.setattr(reads, "get_object", lambda config, oid, timeout=20: _obj("grpc", "0x2::coin::Coin"))
    with pytest.raises(reads.ReadError, match="not a MemoryIndex"):
        reads.memory_index(CFG, "0x1")


def test_memory_index_package_is_checked_for_writes(monkeypatch):
    monkeypatch.setattr(reads, "get_object", lambda config, oid, timeout=20: _obj("grpc", "0xdef::memory_index::MemoryIndex"))
    reads.memory_index(CFG, "0x1")
    with pytest.raises(reads.ReadError, match="belongs to package"):
        reads.memory_index(CFG, "0x1", check_package=True)


def test_fullnode_answer_for_another_object_is_refused(monkeypatch):
    from mnemos import sui_exec_pb2 as pb

    class Stub:
        def __init__(self, channel):
            pass

        def GetObject(self, request, timeout=20):
            return pb.GetObjectResponse(object=pb.Object(object_id="0x2", version=1, digest="d", object_type=TYPE))

    monkeypatch.setattr(reads, "_verified_channel", lambda config: object())
    monkeypatch.setattr(reads.rpc, "LedgerServiceStub", Stub)
    with pytest.raises(reads.ReadError, match="returned object 0x2"):
        reads.grpc_object(CFG, "0x1")


def test_reads_verify_the_chain_once(monkeypatch):
    seen = []
    monkeypatch.setattr(reads, "_VERIFIED", {})
    monkeypatch.setattr(reads, "_channel", lambda config: object())
    monkeypatch.setattr(sui_client, "require_testnet", lambda channel, url: seen.append(url))
    reads._verified_channel(CFG)
    reads._verified_channel(CFG)
    assert seen == ["https://rpc.invalid"]
