#!/usr/bin/env python3
"""Exercise Mnemos through Hermes MemoryManager.

Usage: drive.py write | drive.py recall | drive.py checkpoint
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
hermes_src = os.environ.get("HERMES_SRC", "")
if hermes_src and hermes_src not in sys.path:
    sys.path.insert(0, hermes_src)

FACT = os.environ.get("MNEMOS_FACT", "The harbor passphrase is silver-kelp.")
QUERY = os.environ.get("MNEMOS_QUERY", "harbor passphrase")


def manager():
    from agent.memory_manager import MemoryManager

    from mnemos.provider import MnemosProvider

    home = os.environ["HERMES_HOME"]
    provider = MnemosProvider()
    mgr = MemoryManager()
    mgr.add_provider(provider)
    mgr.initialize_all(
        session_id="mnemos-demo",
        hermes_home=home,
        platform="cli",
        agent_context="primary",
    )
    return mgr, provider


def measure(provider) -> None:
    provider.queue_prefetch(QUERY, session_id="mnemos-demo")
    time.sleep(0.05)
    samples = []
    text = ""
    for _ in range(200):
        started = time.perf_counter()
        text = provider.prefetch(QUERY, session_id="mnemos-demo")
        samples.append((time.perf_counter() - started) * 1000.0)
    samples.sort()
    p50 = samples[len(samples) // 2]
    p95 = samples[int(len(samples) * 0.95)]
    print(f"prefetch_ms min={samples[0]:.4f} p50={p50:.4f} p95={p95:.4f} max={samples[-1]:.4f}")
    print("prefetch_text:")
    print(text)
    if "silver-kelp" not in text:
        raise SystemExit("prefetch did not recall the fact")


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else "write"
    mgr, provider = manager()
    try:
        if action == "write":
            remembered = mgr.handle_tool_call("mnemos_remember", {"content": FACT, "kind": "fact"})
            print("remember", remembered)
            flushed = mgr.handle_tool_call("mnemos_flush", {})
            print("flush", flushed)
            payload = json.loads(flushed)
            if payload.get("error"):
                raise SystemExit(payload["error"])
            if not payload.get("blob_id"):
                raise SystemExit(f"flush did not return a blob id: {flushed}")
            checkpoint = provider.on_pre_compress(
                [{"role": "user", "content": f"Remember this: {FACT}"}],
                require_checkpoint=True,
            )
            print("checkpoint", checkpoint)
            if not str(checkpoint).startswith("checkpoint:"):
                raise SystemExit(f"checkpoint was not durable: {checkpoint}")
            measure(provider)
        elif action == "recall":
            measure(provider)
            status = mgr.handle_tool_call("mnemos_status", {})
            print("status", status)
        else:
            raise SystemExit("usage: drive.py write|recall")
    finally:
        provider.shutdown()


if __name__ == "__main__":
    main()
