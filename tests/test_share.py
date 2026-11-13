"""Sharing lane, BETA (testnet preview): offline checks of the adapter and the native binding.

The testnet scenario (members, refusal, revocation) is scripts/e2e_coop.sh.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from mnemos import share

os.environ.setdefault("MNEMOS_NATIVE_AUTO", "0")

ROOT = Path(__file__).resolve().parents[1]
SECRET = "correct horse battery staple"
# Same vector as tests/test_kdf.py: the Sui address derived from SECRET.
SECRET_ADDRESS = "0x2ea7a84f5f226a7335cf0664e50574bbf701711cb5665ee1b426924efd665a83"
SUI_SEED_HEX = "dd1172ec73161443121bad1b25f48bc072171429cd9d1d5198024c6ca02dac48"


def _available(loader) -> bool:
    """Whether a lane is built, whatever MNEMOS_BACKEND the suite runs under."""
    forced = os.environ.pop("MNEMOS_BACKEND", None)
    try:
        loader()
        return True
    except share.ShareUnavailable:
        return False
    finally:
        if forced is not None:
            os.environ["MNEMOS_BACKEND"] = forced


def _native_available() -> bool:
    return _available(share.native_share)


def _python_available() -> bool:
    return _available(share.python_share)


needs_native = pytest.mark.skipif(
    not _native_available() and os.environ.get("MNEMOS_REQUIRE_RUST") != "1",
    reason="sharing lane needs mnemos_core built with the seal feature",
)
needs_python = pytest.mark.skipif(
    not _python_available() and os.environ.get("MNEMOS_REQUIRE_SEAL_PY") != "1",
    reason="Python sharing lane needs the sui-seal-client bindings (scripts/build_seal_py.sh)",
)
needs_both = pytest.mark.skipif(
    not (_native_available() and _python_available())
    and os.environ.get("MNEMOS_REQUIRE_RUST") != "1" and os.environ.get("MNEMOS_REQUIRE_SEAL_PY") != "1",
    reason="parity checks need both sharing lanes",
)


@pytest.fixture(params=["rust", "python"])
def lane(request, monkeypatch):
    """Each sharing lane in turn, selected the way users select it: MNEMOS_BACKEND."""
    if request.param == "rust" and not _native_available() and os.environ.get("MNEMOS_REQUIRE_RUST") != "1":
        pytest.skip("native sharing lane not built")
    if request.param == "python" and not _python_available() and os.environ.get("MNEMOS_REQUIRE_SEAL_PY") != "1":
        pytest.skip("Python sharing lane needs the sui-seal-client bindings")
    monkeypatch.setenv("MNEMOS_BACKEND", request.param)
    name, cls = share.share_backend()
    assert name == request.param
    return cls


def _args(**values):
    base = {"name": "default", "key_file": "", "share_command": "whoami"}
    base.update(values)
    return SimpleNamespace(**base)


def _home(tmp_path):
    home = tmp_path / "home"
    (home / "mnemos").mkdir(parents=True)
    config = {"package_id": "0x1", "index_object_id": "0x2", "network": "testnet"}
    (home / "mnemos" / "config.json").write_text(json.dumps(config), encoding="utf-8")
    return home


def _digest_tree(path: Path) -> dict:
    return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(path.rglob("*")) if p.is_file()}


def test_backend_selection(monkeypatch):
    def refuse(reason):
        def loader():
            raise share.ShareUnavailable(reason)
        return loader

    monkeypatch.setattr(share, "native_share", lambda: "native-cls")
    monkeypatch.setattr(share, "python_share", lambda: "python-cls")
    for forced, expected in (("", "rust"), ("rust", "rust"), ("python", "python"), ("PYTHON", "python")):
        monkeypatch.setenv("MNEMOS_BACKEND", forced)
        assert share.share_backend()[0] == expected
    # No native seal feature: fall back to the Python lane, unless Rust is forced.
    monkeypatch.setattr(share, "native_share", refuse("no seal feature"))
    monkeypatch.delenv("MNEMOS_BACKEND")
    assert share.share_backend() == ("python", "python-cls")
    monkeypatch.setenv("MNEMOS_BACKEND", "rust")
    with pytest.raises(share.ShareUnavailable, match="no seal feature"):
        share.share_backend()
    monkeypatch.delenv("MNEMOS_BACKEND")
    monkeypatch.setattr(share, "python_share", refuse("no bindings"))
    with pytest.raises(share.ShareUnavailable, match="no seal feature; no bindings"):
        share.share_backend()


def test_missing_bindings_refuse_without_touching_the_profile(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MNEMOS_BACKEND", "python")
    monkeypatch.setitem(sys.modules, "sui_seal_client", None)  # import fails like a missing build
    with pytest.raises(share.ShareUnavailable, match="build_seal_py.sh"):
        share.python_share()
    home = _home(tmp_path)
    before = _digest_tree(home)
    assert share.run(_args(), str(home), SECRET) == 2
    assert "build_seal_py.sh" in capsys.readouterr().err
    assert _digest_tree(home) == before


def test_share_config_passes_transport_only(tmp_path):
    home = _home(tmp_path)
    state = {"spaces": {}, "config": {"space_package": "0xabc", "sui_private_key": "nope", "threshold": 2}}
    config = share.share_config(str(home), state)
    assert config["sui_rpc"].startswith("https://fullnode.testnet")
    assert config["space_package"] == "0xabc" and config["threshold"] == 2
    for personal in ("package_id", "index_object_id", "sui_private_key", "network"):
        assert personal not in config


def test_state_roundtrip_and_separate_file(tmp_path):
    home = _home(tmp_path)
    config_before = (home / "mnemos" / "config.json").read_bytes()
    state = share.load_state(str(home))
    assert state == {"spaces": {}, "config": {}}
    state["spaces"]["team"] = {"space_id": "0x5", "list_id": "0x6", "owner": "0x7"}
    share.save_state(str(home), state)
    assert share.load_state(str(home))["spaces"]["team"]["space_id"] == "0x5"
    assert (home / "mnemos" / "config.json").read_bytes() == config_before


def test_merge_pull_keeps_what_was_read():
    cache = {"entries": {}}
    first = {"entries": [{"key_id": "aa", "status": "ok", "text": "kept", "index": 0, "epoch": 0, "author": "0x1", "blob_id": "b", "kind": "fact", "ts": 1}]}
    share.merge_pull(cache, first)
    later = {"entries": [
        {"key_id": "aa", "status": "no_access"},
        {"key_id": "bb", "status": "no_access"},
    ]}
    share.merge_pull(cache, later)
    assert later["entries"][0]["cached_text"] == "kept"
    assert "cached_text" not in later["entries"][1]
    assert set(cache["entries"]) == {"aa"}


def test_share_commands_parse():
    parser = argparse.ArgumentParser()
    share.add_arguments(parser)
    assert "BETA (testnet preview)" in parser.description
    assert parser.format_help().count("BETA (testnet preview)") >= 9
    args = parser.parse_args(["push", "--text", "one", "--text", "two", "--name", "team"])
    assert (args.share_command, args.text, args.name) == ("push", ["one", "two"], "team")
    args = parser.parse_args(["remove", "0xb"])
    assert (args.share_command, args.member, args.name) == ("remove", "0xb", "default")
    args = parser.parse_args(["join", "--space", "0x9"])
    assert args.space == "0x9"
    with pytest.raises(SystemExit):
        parser.parse_args(["join"])


def test_hermes_cli_registers_share():
    spec = importlib.util.spec_from_file_location(
        "mnemos_sui_plugin", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules["mnemos_sui_plugin"] = package
    try:
        spec.loader.exec_module(package)
        from mnemos_sui_plugin import cli

        parser = argparse.ArgumentParser()
        cli.register_cli(parser)
        args = parser.parse_args(["share", "pull", "--name", "team"])
        assert (args.mnemos_command, args.share_command, args.name) == ("share", "pull", "team")
        args = parser.parse_args(["history", "--verify"])
        assert args.mnemos_command == "history" and args.verify
    finally:
        for name in [n for n in sys.modules if n == "mnemos_sui_plugin" or n.startswith("mnemos_sui_plugin.")]:
            del sys.modules[name]


def test_space_seal_settings_from_flags():
    parser = argparse.ArgumentParser()
    share.add_arguments(parser)
    args = parser.parse_args(["create", "--committee", "--name", "team"])
    assert share.seal_from_args(args) == {"key_server_profile": "committee"}
    args = parser.parse_args(["create", "--key-server", "0x1", "--key-server", "0x2", "--threshold", "1"])
    assert share.seal_from_args(args) == {"key_servers": ["0x1", "0x2"], "threshold": 1}
    args = parser.parse_args(["join", "--space", "0x9", "--threshold", "1"])
    assert share.seal_from_args(args) == {"threshold": 1}
    assert share.seal_from_args(parser.parse_args(["create"])) == {}
    with pytest.raises(SystemExit, match="exclusive"):
        share.seal_from_args(parser.parse_args(["create", "--committee", "--key-server", "0x1"]))
    with pytest.raises(SystemExit, match="at least 1"):
        share.seal_from_args(parser.parse_args(["create", "--threshold", "0"]))
    for verb in ("push", "pull", "status"):
        assert not hasattr(parser.parse_args([verb]), "committee"), f"{verb} must use the space settings"


def test_share_config_merges_space_settings(tmp_path):
    home = _home(tmp_path)
    state = {"spaces": {}, "config": {"key_servers": ["0x1", "0x2", "0x3"], "threshold": 2, "aggregators": {"0x3": "https://a.example"}}}
    assert share.share_config(str(home), state)["threshold"] == 2
    merged = share.share_config(str(home), state, {"threshold": 3})
    assert merged["key_servers"] == ["0x1", "0x2", "0x3"] and merged["threshold"] == 3
    committee = share.share_config(str(home), state, {"key_server_profile": "committee"})
    assert committee["key_server_profile"] == "committee"
    assert "key_servers" not in committee and "threshold" not in committee, "a profile replaces global servers"
    assert committee["aggregators"] == {"0x3": "https://a.example"}
    ignored = share.share_config(str(home), state, {"seal_package": "0xevil", "threshold": 1})
    assert "seal_package" not in ignored, "per-space settings are limited to key servers"


def test_space_settings_follow_the_space(tmp_path):
    home = _home(tmp_path)
    state = {"spaces": {"team": {"space_id": "0x5", "seal": {"key_server_profile": "committee"}}, "old": {"space_id": "0x6"}}, "config": {}}
    assert share._space_seal(_args(share_command="push", name="team"), state) == {"key_server_profile": "committee"}
    assert share._space_seal(_args(share_command="push", name="old"), state) == {}, "spaces of the first preview keep the defaults"
    assert share._space_seal(_args(share_command="pull", name="missing"), state) == {}


def test_share_signer_defaults_to_mnemos_address(lane):
    cls = lane
    assert cls("{}", "", SECRET).address() == SECRET_ADDRESS
    assert cls("{}", SUI_SEED_HEX).address() == SECRET_ADDRESS
    with pytest.raises(RuntimeError, match="no signer"):
        cls("{}", "", "")
    with pytest.raises(RuntimeError, match="threshold"):
        cls(json.dumps({"threshold": 3}), SUI_SEED_HEX)
    with pytest.raises(RuntimeError, match="key_server_profile"):
        cls(json.dumps({"key_server_profile": "mainnet"}), SUI_SEED_HEX)
    assert cls(json.dumps({"key_server_profile": "committee"}), SUI_SEED_HEX).address() == SECRET_ADDRESS


def test_bad_key_server_settings_fail_before_any_transaction(lane, tmp_path):
    home = _home(tmp_path)
    key_file = tmp_path / "signer.key"
    key_file.write_text(SUI_SEED_HEX, encoding="utf-8")
    parser = argparse.ArgumentParser()
    share.add_arguments(parser)
    before = _digest_tree(home)
    for argv in (["create", "--threshold", "3"], ["create", "--committee", "--threshold", "2"],
                 ["create", "--key-server", "0x1", "--key-server", "0x1"]):
        args = parser.parse_args([*argv, "--key-file", str(key_file)])
        with pytest.raises(SystemExit, match="share settings"):
            share.run(args, str(home), "")
    assert _digest_tree(home) == before, "refused settings must not create share.json"


def test_whoami_reads_key_file_without_printing_it(lane, tmp_path, capsys):
    home = _home(tmp_path)
    key_file = tmp_path / "signer.key"
    key_file.write_text(SUI_SEED_HEX + "\n", encoding="utf-8")
    before = _digest_tree(home)
    assert share.run(_args(key_file=str(key_file)), str(home), "") == 0
    out = capsys.readouterr()
    assert json.loads(out.out) == {"address": SECRET_ADDRESS}
    assert SUI_SEED_HEX not in out.out + out.err
    assert _digest_tree(home) == before, "whoami must not touch the profile"


def test_admin_commands_need_the_cap(lane, tmp_path):
    home = _home(tmp_path)
    share.save_state(str(home), {"spaces": {"default": {"space_id": "0x5", "list_id": "0x6", "owner": "0x7"}}, "config": {}})
    with pytest.raises(SystemExit, match="Cap"):
        share.run(_args(share_command="add", member="0xb"), str(home), SECRET)


# --- Python lane: same rules and bytes as native/src/share.rs (BETA) -------------------------------

CONFIG_CORPUS = [
    "{}",
    "not json",
    '{"key_server_profile": "committee"}',
    '{"key_server_profile": "testnet", "threshold": 1}',
    '{"threshold": 1}',
    '{"key_servers": null, "threshold": null}',
    '{"seal_package": 5, "key_server_profile": 7}',
    '{"key_servers": ["0xb012378c9f3799fb5b1a7083da74a4069e3c3f1c93de0b27212a5799ce1e1e98", "0x73d05d62c18d9374e3ea529e8e0ed6161da1a141a94d3f76ae3fe4e99356db75"], "threshold": 2}',
    '{"key_servers": ["0X1"]}',
    '{"key_servers": ["0xAB", "0xab"]}',
    '{"key_servers": ["0x' + "1" * 65 + '"]}',
    '{"key_servers": ["0x"]}',
    '{"key_servers": ["1"], "threshold": 1}',
    '{"key_server_profile": "mainnet"}',
    '{"key_server_profile": "committee", "threshold": 2}',
    '{"key_servers": []}',
    '{"key_servers": "0x1"}',
    '{"key_servers": ["0x1", "0x1"]}',
    '{"key_servers": ["0xzz"]}',
    '{"key_servers": [1]}',
    '{"threshold": "2"}',
    '{"threshold": true}',
    '{"threshold": 1.0}',
    '{"threshold": -1}',
    '{"threshold": 0}',
    '{"threshold": 3}',
    '{"threshold": 256}',
    '{"seal_package": "0xzz"}',
    '{"space_package": "0x"}',
    '{"aggregators": {"0x1": "https://agg.example/"}}',
    '{"aggregators": {"0x1": "http://plain.example"}}',
    '{"aggregators": {"0x1": 3}}',
    '{"aggregators": ["https://a.example"]}',
    '{"aggregators": {"nope": "https://a.example"}}',
]


def _outcome(cls, raw: str):
    try:
        return "ok", cls(raw, SUI_SEED_HEX).address()
    except RuntimeError as exc:
        return "refused", str(exc)


@needs_both
@pytest.mark.parametrize("raw", CONFIG_CORPUS)
def test_lanes_accept_and_refuse_the_same_settings(monkeypatch, raw):
    monkeypatch.delenv("MNEMOS_BACKEND", raising=False)
    assert _outcome(share.python_share(), raw) == _outcome(share.native_share(), raw)


@needs_both
def test_lanes_derive_the_same_signer(monkeypatch):
    monkeypatch.delenv("MNEMOS_BACKEND", raising=False)
    py_share, rust = share.python_share(), share.native_share()
    seed = bytes.fromhex(SUI_SEED_HEX)
    import base64

    flagged = base64.b64encode(b"\x00" + seed).decode()
    for key, secret in ((SUI_SEED_HEX, ""), ("", SECRET), (flagged, ""), (SUI_SEED_HEX.upper(), ""),
                        ("bad", ""), ("", ""), (base64.b64encode(b"\x01" + seed).decode(), "")):
        assert _outcome(lambda raw, _k, k=key, s=secret: py_share(raw, k, s), "{}") == \
            _outcome(lambda raw, _k, k=key, s=secret: rust(raw, k, s), "{}"), (key[:6], bool(secret))


@needs_python
def test_python_lane_matches_rust_unit_vectors():
    from mnemos import py_share as ps

    seal = ps.bindings()
    for name in ("MYSTEN_TESTNET_1", "MYSTEN_TESTNET_2", "MYSTEN_TESTNET_COMMITTEE", "MYSTEN_TESTNET_AGGREGATOR"):
        assert getattr(ps, name) == getattr(seal, name)
    list_id = ps.parse_address("0x5c1b")
    kid = ps.key_id(list_id, 3, bytes([9]) * ps.NONCE_LEN)
    assert len(kid) == 32 + 8 + ps.NONCE_LEN and kid[32:40] == bytes([3, 0, 0, 0, 0, 0, 0, 0])
    assert kid == bytes(seal.allowlist_key_id(list_id, 3, bytes([9]) * ps.NONCE_LEN))
    assert ps.parse_key_id(kid) == (list_id, 3)
    with pytest.raises(ps.ShareError):
        ps.parse_key_id(kid[:40])
    cfg = ps.ShareConfig.from_json("{}")
    trusted = cfg.trusted_servers()
    one, two, committee = ps.MYSTEN_TESTNET_1, ps.MYSTEN_TESTNET_2, ps.MYSTEN_TESTNET_COMMITTEE
    assert ps.server_set_from_header([one, two], 2, trusted)[1] == 2
    assert ps.server_set_from_header([committee], 1, trusted)
    with pytest.raises(ps.ShareError, match="untrusted key server"):
        ps.server_set_from_header([one, ps.parse_address("0xbad")], 1, trusted)
    with pytest.raises(ps.ShareError, match="weighted"):
        ps.server_set_from_header([one, one, two], 2, trusted)
    for bad in (3, 0):
        with pytest.raises(ps.ShareError):
            ps.server_set_from_header([one, two], bad, trusted)


@needs_python
def test_python_entry_format_is_the_native_format():
    from mnemos import py_share as ps

    author = ps.parse_address("0xa")
    data = ps.entry_plaintext("clé du phare \u2603", "fact", author, 42)
    # serde_json: sorted keys, compact, raw UTF-8 (what native/src/share.rs writes).
    assert data == ('{"author":"' + author + '","format":"mnemos-share-entry-v1","kind":"fact",'
                    '"text":"clé du phare \u2603","ts":42}').encode("utf-8")
    assert ps.parse_entry_plaintext(data)["text"] == "clé du phare \u2603"
    for bad in (b'{"format":"other","text":"x"}', b"not json"):
        with pytest.raises(ps.ShareError):
            ps.parse_entry_plaintext(bad)


@needs_python
def test_python_space_json_is_checked():
    import base64

    from mnemos import py_share as ps

    list_id = ps.parse_address("0x5c1b")
    kid = ps.key_id(list_id, 1, bytes([7]) * ps.NONCE_LEN)
    good = {"list_id": list_id, "creator": "0xa", "entries": [{
        "blob_id": "blob", "key_id": base64.b64encode(kid).decode(), "epoch": "1", "author": "0xa",
        "blob_sha256": "01" * 32}]}
    state = ps.parse_space(good)
    assert len(state["entries"]) == 1 and state["entries"][0]["epoch"] == 1 and state["entries"][0]["key_id"] == kid
    for path, value in ((("entries", 0, "epoch"), "0"), (("entries", 0, "blob_sha256"), "0102"), (("list_id",), "0x5c1c")):
        bad = json.loads(json.dumps(good))
        target = bad
        for step in path[:-1]:
            target = target[step]
        target[path[-1]] = value
        with pytest.raises(ps.ShareError):
            ps.parse_space(bad)


@needs_python
def test_python_entry_roundtrip_offline():
    """Encrypt an entry the way push does and read it back the way pull does, with in-process
    key servers (no network): key id, header checks and plaintext format."""
    from mnemos import py_share as ps

    seal = ps.bindings()
    servers = [seal.LocalKeyServer(ps.parse_address(hex(i))) for i in (1, 2, 3)]
    client = seal.SealClient.with_local_servers(servers, 2)
    list_id = ps.parse_address("0x5c1b")
    kid = ps.key_id(list_id, 4, os.urandom(ps.NONCE_LEN))
    author = ps.parse_address("0xa")
    blob = bytes(client.encrypt(ps.DEFAULT_SEAL_PACKAGE, kid, ps.entry_plaintext("hello", "fact", author, 7)))
    info = seal.parse_encrypted_object(blob)
    assert ps.parse_address(info.package_id) == ps.DEFAULT_SEAL_PACKAGE
    assert bytes(info.id) == kid and ps.parse_key_id(bytes(info.id)) == (list_id, 4)
    trusted = ps.ShareConfig.from_json(json.dumps({"key_servers": [s.object_id for s in servers], "threshold": 2})).trusted_servers()
    assert ps.server_set_from_header(list(info.key_servers), info.threshold, trusted)[1] == 2
    with pytest.raises(ps.ShareError, match="untrusted"):
        ps.server_set_from_header(list(info.key_servers), info.threshold, ps.ShareConfig.from_json("{}").trusted_servers())
    keys = seal.derive_keys(servers[:2], ps.DEFAULT_SEAL_PACKAGE, kid)
    value = ps.parse_entry_plaintext(bytes(client.decrypt(blob, keys)))
    assert (value["text"], value["author"], value["ts"]) == ("hello", author, 7)
