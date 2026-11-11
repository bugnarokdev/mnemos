"""Signing refuses unless the RPC reports Sui testnet."""

import pytest

from mnemos import sui_client


def _field(num: int, text: str) -> bytes:
    raw = text.encode()
    return bytes([(num << 3) | 2, len(raw)]) + raw


def test_parse_service_info():
    data = _field(1, sui_client.TESTNET_CHAIN_ID) + bytes([3 << 3, 0x96, 0x01]) + _field(2, "testnet")
    assert sui_client.parse_service_info(data) == {"chain_id": sui_client.TESTNET_CHAIN_ID, "chain": "testnet"}


def test_testnet_chain_id_short_form():
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    value = 0
    for ch in sui_client.TESTNET_CHAIN_ID:
        value = value * 58 + alphabet.index(ch)
    assert value.to_bytes(32, "big")[:4].hex() == "4c78adac"


@pytest.mark.parametrize(
    "reply",
    [
        {"chain_id": "4btiuiMPvEENsttpZC7CZ53DruC3MAgfznDbASZ7DR6S", "chain": "mainnet"},
        {"chain": "testnet"},
        {},
    ],
)
def test_non_testnet_is_refused_before_signing(monkeypatch, reply):
    monkeypatch.setattr(sui_client, "service_info", lambda channel, timeout=15: reply)
    monkeypatch.setattr(sui_client, "_sign", lambda *a: pytest.fail("signed on a non-testnet RPC"))
    with pytest.raises(RuntimeError, match="refusing to sign"):
        sui_client.execute("https://rpc.invalid", "0x1", [], [], bytes(32), 1)


def test_rpc_error_fails_closed(monkeypatch):
    def boom(channel, timeout=15):
        raise TimeoutError("slow")

    monkeypatch.setattr(sui_client, "service_info", boom)
    monkeypatch.setattr(sui_client, "_sign", lambda *a: pytest.fail("signed without a chain check"))
    with pytest.raises(RuntimeError, match="could not read the chain id"):
        sui_client.execute("https://rpc.invalid", "0x1", [], [], bytes(32), 1)


def test_testnet_passes_the_guard(monkeypatch):
    seen = []
    monkeypatch.setattr(sui_client, "service_info", lambda channel, timeout=15: {"chain_id": sui_client.TESTNET_CHAIN_ID})
    sui_client.require_testnet(object(), "https://rpc.invalid")
    seen.append(1)
    assert seen == [1]
