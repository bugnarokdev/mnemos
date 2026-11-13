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


def _native_available() -> bool:
    try:
        share.native_share()
        return True
    except share.ShareUnavailable:
        return False


needs_native = pytest.mark.skipif(
    not _native_available() and os.environ.get("MNEMOS_REQUIRE_RUST") != "1",
    reason="sharing lane needs mnemos_core built with the seal feature",
)


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


def test_python_backend_refuses_share(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MNEMOS_BACKEND", "python")
    with pytest.raises(share.ShareUnavailable, match="native core"):
        share.native_share()
    home = _home(tmp_path)
    before = _digest_tree(home)
    assert share.run(_args(), str(home), SECRET) == 2
    assert "native core" in capsys.readouterr().err
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


@needs_native
def test_native_share_signer_defaults_to_mnemos_address():
    cls = share.native_share()
    assert cls("{}", "", SECRET).address() == SECRET_ADDRESS
    assert cls("{}", SUI_SEED_HEX).address() == SECRET_ADDRESS
    with pytest.raises(RuntimeError, match="no signer"):
        cls("{}", "", "")
    with pytest.raises(RuntimeError, match="threshold"):
        cls(json.dumps({"threshold": 3}), SUI_SEED_HEX)
    with pytest.raises(RuntimeError, match="key_server_profile"):
        cls(json.dumps({"key_server_profile": "mainnet"}), SUI_SEED_HEX)
    assert cls(json.dumps({"key_server_profile": "committee"}), SUI_SEED_HEX).address() == SECRET_ADDRESS


@needs_native
def test_bad_key_server_settings_fail_before_any_transaction(tmp_path):
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


@needs_native
def test_whoami_reads_key_file_without_printing_it(tmp_path, capsys):
    home = _home(tmp_path)
    key_file = tmp_path / "signer.key"
    key_file.write_text(SUI_SEED_HEX + "\n", encoding="utf-8")
    before = _digest_tree(home)
    assert share.run(_args(key_file=str(key_file)), str(home), "") == 0
    out = capsys.readouterr()
    assert json.loads(out.out) == {"address": SECRET_ADDRESS}
    assert SUI_SEED_HEX not in out.out + out.err
    assert _digest_tree(home) == before, "whoami must not touch the profile"


@needs_native
def test_admin_commands_need_the_cap(tmp_path):
    home = _home(tmp_path)
    share.save_state(str(home), {"spaces": {"default": {"space_id": "0x5", "list_id": "0x6", "owner": "0x7"}}, "config": {}})
    with pytest.raises(SystemExit, match="Cap"):
        share.run(_args(share_command="add", member="0xb"), str(home), SECRET)
