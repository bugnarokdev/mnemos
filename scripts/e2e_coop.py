"""BETA (testnet preview): end-to-end check of the sharing lane on Sui testnet,
Walrus testnet and Seal.

Not production software. Run through scripts/e2e_coop.sh. Every check is an assertion; the
script exits non-zero on the first failure. Secrets are read from files and never
printed.

Environment:
  SHARE_KEY_A, SHARE_KEY_B, SHARE_KEY_C   signer key files (A pays gas; B, C only sign)
  MNEMOS_SECRET                           A's personal Mnemos secret (its derived address pays
                                          for the personal MemoryIndex)
  MNEMOS_PACKAGE_ID                       MemoryIndex package to create A's fresh index with
  E2E_WORK                                scratch directory (fresh profiles A, B, C)
  E2E_REPORT                              where to write the JSON record of the run
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mnemos import history, reads  # noqa: E402
from mnemos.engine import open_engine  # noqa: E402
from mnemos.py_engine import DEFAULTS, load_file_config, save_file_config, walrus_get  # noqa: E402
from mnemos.share import native_share  # noqa: E402

PY = sys.executable
WORK = Path(os.environ["E2E_WORK"])
REPORT = Path(os.environ.get("E2E_REPORT", WORK / "coop-report.json"))
KEYS = {who: os.environ[f"SHARE_KEY_{who.upper()}"] for who in ("a", "b", "c")}
SECRET = os.environ["MNEMOS_SECRET"]
PACKAGE = os.environ["MNEMOS_PACKAGE_ID"]
RUN = os.environ.get("E2E_TAG") or time.strftime("%H%M%S")
record: dict = {"run": RUN, "steps": []}
checks = 0


def check(condition: bool, message: str) -> None:
    global checks
    if not condition:
        REPORT.write_text(json.dumps(record, indent=2))
        raise SystemExit(f"FAIL: {message}")
    checks += 1
    print(f"  ok  {message}")


def home(who: str) -> Path:
    return WORK / who


def cli(who: str, *args: str, expect: int = 0) -> dict:
    """Run `python -m mnemos.share` (the code behind `hermes mnemos-sui share`)."""
    env = dict(os.environ, MNEMOS_SHARE_KEY_FILE=KEYS[who])
    env.pop("MNEMOS_BACKEND", None)
    cmd = [PY, "-m", "mnemos.share", "--home", str(home(who)), *args]
    done = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
    if done.returncode != expect:
        sys.stderr.write(done.stdout + done.stderr)
        check(False, f"{who}: share {' '.join(args)} exited {done.returncode}, expected {expect}")
    out = json.loads(done.stdout) if done.stdout.strip() else {}
    record["steps"].append({"who": who, "cmd": ["share", *args], "exit": done.returncode, "out": out})
    return out


def texts(pull: dict, status: str = "ok") -> list[str]:
    return [row["text"] for row in pull["entries"] if row["status"] == status]


def tree_digest(path: Path) -> dict:
    return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(path.rglob("*")) if p.is_file() and not p.name.endswith(("-wal", "-shm"))}


def personal_open(db: Path):
    os.environ.pop("MNEMOS_BACKEND", None)
    engine = open_engine(str(db), SECRET, load_file_config(home("a")))
    assert engine.backend == "rust", engine.reason
    return engine


def main() -> None:
    shutil.rmtree(WORK, ignore_errors=True)
    for who in ("a", "b", "c"):
        (home(who) / "mnemos").mkdir(parents=True)
    print(f"BETA (testnet preview) coop e2e, run {RUN}, work {WORK}")
    native_share()  # fails fast if the core lacks the seal feature

    # --- A's personal memory: fresh MemoryIndex, two facts, two snapshots ---------------
    print("A personal memory")
    db = home("a") / "mnemos" / "mnemos.sqlite"
    save_file_config(home("a"), {"package_id": PACKAGE, "network": "testnet"})
    engine = personal_open(db)
    init = engine.init_chain("{}")
    save_file_config(home("a"), {"index_object_id": init["index_object_id"]})
    engine.close()
    engine = personal_open(db)
    personal_facts = [f"coop {RUN} personal fact {word}: the lighthouse key is under stone {word}" for word in ("alpha", "bravo")]
    flushes = []
    for fact in personal_facts:
        engine.remember(fact, "e2e-coop", "fact")
        flushes.append(engine.flush())
    personal_head = engine.status()
    engine.close()
    record["personal"] = {"init": init, "flushes": flushes}
    check(all(f.get("digest") for f in flushes), "A flushed two personal snapshots on Sui")
    personal_before = tree_digest(home("a") / "mnemos")

    # --- Shared space ---------------------------------------------------------------
    print("A creates a space and adds B")
    addr = {who: cli(who, "whoami")["address"] for who in ("a", "b", "c")}
    created = cli("a", "create")
    record["space"] = created
    space = created["space_id"]
    check(created["epoch"] == 0 and created["owner"] == addr["a"], "space created at epoch 0, owned by A")
    added = cli("a", "add", addr["b"])
    record["add_b"] = added
    check(added["epoch"] == 0, "adding B keeps epoch 0")

    print("A pushes three entries (two texts, one from personal memory)")
    shared = [f"coop {RUN} shared entry {i}: meet at pier {i}" for i in (1, 2)]
    push1 = cli("a", "push", "--text", shared[0], "--text", shared[1])
    push2 = cli("a", "push", "--from-memory", "alpha", "--limit", "1")
    record["push_epoch0"] = [push1, push2]
    check(len(push1["entries"]) == 2 and push1["epoch"] == 0, "two entries published at epoch 0")
    check(len(push2["entries"]) == 1 and push2["epoch"] == 0, "one personal memory entry published at epoch 0")
    check(tree_digest(home("a") / "mnemos").get("mnemos.sqlite") == personal_before.get("mnemos.sqlite"),
          "sharing from memory left A's personal store unchanged")
    expected_epoch0 = shared + [personal_facts[0]]

    print("B joins and pulls")
    cli("b", "join", "--space", space)
    pull_b = cli("b", "pull")
    record["pull_b_epoch0"] = pull_b
    check(pull_b["counts"] == {"ok": 3, "no_access": 0, "no_cached_key": 0, "error": 0}, "B reads all 3 entries")
    check(texts(pull_b) == expected_epoch0, "B gets the exact plaintexts, in order")
    check(all(row["author"] == addr["a"] for row in pull_b["entries"]), "every entry is attributed to A")

    print("C (never a member) is refused")
    cli("c", "join", "--space", space)
    pull_c = cli("c", "pull", expect=3)
    record["pull_c"] = pull_c
    check(pull_c["counts"]["no_access"] == 3 and pull_c["counts"]["ok"] == 0, "C is refused for all 3 entries")
    check(not any("text" in row for row in pull_c["entries"]), "C sees no plaintext")

    # B in a long-lived process: keys fetched now stay in memory.
    share_cls = native_share()
    with open(KEYS["b"], encoding="utf-8") as fh:
        b_live = share_cls(json.dumps({}), fh.read().strip())
    live = json.loads(b_live.pull(space, True))
    check(texts(live) == expected_epoch0, "B (live session) fetched keys for the 3 epoch-0 entries")

    print("A removes B and pushes a new entry")
    removed = cli("a", "remove", addr["b"])
    record["remove_b"] = removed
    check(removed["epoch_before"] == 0 and removed["epoch"] == 1, "removing B bumps the epoch 0 -> 1")
    new_text = f"coop {RUN} shared entry 3: after B left, the pier moved"
    push3 = cli("a", "push", "--text", new_text)
    record["push_epoch1"] = push3
    check(push3["epoch"] == 1, "new entry encrypted under epoch 1")
    check(push3["entries"][0]["key_id"][64:80] == (1).to_bytes(8, "little").hex(), "its key id carries epoch 1")

    status = cli("a", "status")
    record["status"] = status
    check(status["epoch"] == 1 and status["entries"] == 4, "space has 4 entries, epoch 1")
    check(status["entries_per_epoch"] == {"0": 3, "1": 1}, "3 entries at epoch 0, 1 at epoch 1")

    print("B after removal")
    pull_b2 = cli("b", "pull", expect=3)
    record["pull_b_after_remove"] = pull_b2
    check(pull_b2["counts"]["no_access"] == 4 and pull_b2["counts"]["ok"] == 0,
          "B (new session) is refused for every entry, old and new")
    old_rows = [row for row in pull_b2["entries"] if row["epoch"] == 0]
    check([row.get("cached_text") for row in old_rows] == expected_epoch0,
          "B's local cache still holds what B read before the removal")
    check("cached_text" not in pull_b2["entries"][3] and "text" not in pull_b2["entries"][3],
          "B has no plaintext of the new entry")

    offline = json.loads(b_live.pull(space, False))
    record["b_live_cached_only"] = offline
    check(texts(offline) == expected_epoch0,
          "DOCUMENTED: keys B fetched before the removal still decrypt the 3 old entries")
    check(offline["entries"][3]["status"] == "no_cached_key", "those keys do not open the epoch-1 entry")
    live2 = json.loads(b_live.pull(space, True))
    record["b_live_after_remove"] = live2
    check(live2["entries"][3]["status"] == "no_access", "B's live session is refused the epoch-1 key")
    check(texts(live2) == expected_epoch0, "and still reads only the 3 old entries from its held keys")

    pull_a = cli("a", "pull")
    record["pull_a"] = pull_a
    check(texts(pull_a) == expected_epoch0 + [new_text], "A (still a member) reads all 4 entries")
    pull_c2 = cli("c", "pull", expect=3)
    check(pull_c2["counts"]["no_access"] == 4, "C is still refused for all 4 entries")

    # --- A's personal memory is unchanged ---------------------------------------------
    print("A personal memory after sharing")
    after = tree_digest(home("a") / "mnemos")
    for name in ("mnemos.sqlite", "config.json"):
        check(after.get(name) == personal_before.get(name), f"sharing did not modify {name}")
    engine = personal_open(db)
    check(engine.status() == personal_head, "personal status unchanged")
    engine.close()
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(str(db) + suffix).unlink(missing_ok=True)
    check(not db.exists(), "A's local personal store wiped")
    engine = personal_open(db)
    restored = engine.restore(init["index_object_id"])
    hits = engine.recall("lighthouse stone", 5)["hits"]
    engine.close()
    record["personal"]["restore"] = restored
    check(restored["entries"] == 2 and restored.get("kdf") == "argon2id", "restore rebuilt 2 personal entries (argon2id)")
    check(sorted(h["text"] for h in hits) == sorted(personal_facts), "restored personal facts are exact")
    check(not any("shared entry" in h["text"] for h in hits), "no shared entry leaked into personal memory")
    _obj, contents = reads.memory_index(dict(DEFAULTS), init["index_object_id"])
    report = history.verify_history(history.parse_index(contents), lambda b: walrus_get(DEFAULTS["walrus_aggregator"], b))
    record["personal"]["verify"] = report["verdict"]
    check(report["ok"] and report["verdict"] == "OK: 2 versions verified", "history --verify: OK: 2 versions verified")

    record["checks"] = checks
    REPORT.write_text(json.dumps(record, indent=2))
    print(f"PASS: {checks} checks, record {REPORT}")


if __name__ == "__main__":
    main()
