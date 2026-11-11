"""Sui testnet faucet proof-of-work. The challenge is Argon2d, not a captcha."""

from __future__ import annotations

import json
import time
import urllib.request

from argon2.low_level import Type, hash_secret_raw

from .py_engine import graphql, http_request


def sui_balance(graphql_url: str, address: str) -> int:
    query = """
    query($a: SuiAddress!) {
      address(address: $a) { balance(coinType: "0x2::sui::SUI") { totalBalance } }
    }
    """
    data = graphql(graphql_url, query, {"a": address})
    text = (((data.get("data") or {}).get("address") or {}).get("balance") or {}).get("totalBalance") or "0"
    return int(text)


def fund_if_needed(faucet_url: str, graphql_url: str, address: str, minimum: int) -> dict:
    balance = 0
    try:
        balance = sui_balance(graphql_url, address)
    except Exception:
        balance = 0
    if minimum > 0 and balance >= minimum:
        return {"funded": False, "balance": str(balance), "sui_address": address}
    if minimum == 0 and balance > 0:
        return {"funded": False, "balance": str(balance), "sui_address": address}
    last = "faucet was not attempted"
    for _ in range(3):
        try:
            digest = request(faucet_url, address)
            time.sleep(2)
            balance = sui_balance(graphql_url, address)
            return {"funded": True, "digest": digest, "balance": str(balance), "sui_address": address}
        except Exception as exc:
            last = str(exc)
    raise RuntimeError(
        f"{last}. Fund {address} from https://faucet.sui.io if the faucet is rate-limited "
        "or asks for a captcha, then retry with the same secret."
    )


def request(faucet_url: str, address: str) -> str:
    base = faucet_url.rstrip("/")
    status, body = http_request("GET", f"{base}/v3/challenge?recipient={address}", timeout=20)
    del status
    challenge = json.loads(body.decode("utf-8"))
    nonce, hash_hex = _solve(challenge)
    payload = json.dumps(
        {
            "recipient": address,
            "checkpointSeq": str(_u64(challenge.get("checkpointSeq"))),
            "nonce": str(nonce),
            "hashHex": hash_hex,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/v3/gas",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        parsed = json.loads(response.read().decode("utf-8"))
    digest = parsed.get("digest") or ""
    if not digest:
        raise RuntimeError(f"faucet returned no digest: {parsed}")
    return str(digest)


def _solve(challenge: dict) -> tuple[int, str]:
    memory = _u64(challenge.get("memorySize"))
    iterations = max(1, _u64(challenge.get("iterations")))
    parallelism = max(1, _u64(challenge.get("parallelism")))
    hash_len = _u64(challenge.get("hashLength"))
    threshold = _u64(challenge.get("threshold"))
    salt = str(challenge.get("salt") or "sui-faucet-pow-1").encode("utf-8")
    if hash_len < 8 or memory <= 0:
        raise RuntimeError("faucet challenge is missing proof-of-work parameters")
    import secrets

    nonce = int.from_bytes(secrets.token_bytes(8), "little")
    started = time.monotonic()
    domain = challenge.get("domain") or "sui-faucet-pow/1"
    chain_id = challenge.get("chainId") or ""
    checkpoint = _u64(challenge.get("checkpointSeq"))
    digest = challenge.get("checkpointDigest") or ""
    random_bytes = challenge.get("randomBytes") or ""
    faucet_address = challenge.get("faucetAddress") or ""
    recipient = challenge.get("recipient") or ""
    while time.monotonic() - started < 50:
        preimage = f"{domain}\n{chain_id}\n{checkpoint}\n{digest}\n{random_bytes}\n{faucet_address}\n{recipient}\n{nonce}"
        digest_bytes = hash_secret_raw(
            preimage.encode("utf-8"),
            salt,
            time_cost=iterations,
            memory_cost=memory,
            parallelism=parallelism,
            hash_len=hash_len,
            type=Type.D,
        )
        if int.from_bytes(digest_bytes[:8], "big") < threshold:
            return nonce, digest_bytes.hex()
        nonce = (nonce + 1) & 0xFFFFFFFFFFFFFFFF
    raise RuntimeError("faucet proof-of-work exceeded the 50s challenge window")


def _u64(value) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value:
        return int(value)
    return 0
