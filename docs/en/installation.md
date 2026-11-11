# Installation

Requirements:

- Hermes Agent 0.23.1 or newer (`requires_hermes: ">=0.23.1"`). The floor matches the checkpoint API v2 that shipped in that release.
- Python 3.11 or newer. A current Hermes checkout uses 3.14.
- For chain writes: a Rust toolchain, `maturin`, and the `sui` CLI to compile the Move package.
- Network access to the testnet endpoints listed in the root README.

## Plugin layout

The repository root is the plugin.

```bash
ln -sfn /path/to/mnemos "$HERMES_HOME/plugins/mnemos"
```

`$HERMES_HOME/config.yaml`:

```yaml
memory:
  provider: mnemos
```

`$HERMES_HOME/.env`:

```bash
MNEMOS_SECRET=choose-a-long-passphrase
```

Hermes reads that secret with `agent.secret_scope.get_secret`. Mnemos does not open `.env` itself.

Check the install:

```bash
hermes plugins validate /path/to/mnemos --install-deps
hermes memory status
```

`validate` checks the manifest, loads `register()`, and installs the declared Python dependencies (`cryptography`, `grpcio`, `protobuf`, `argon2-cffi`). Those are enough for Walrus and Sui writes. Rust is not required.

## Native core

By default Mnemos uses the Python backend and downloads nothing. The native download is opt-in: with `MNEMOS_NATIVE_AUTO=1` and no importable `mnemos_core`, the first open downloads the matching wheel from the official `mnemos-native-v0.2.2` release at `https://github.com/bugnarokdev/mnemos`, compares it with the sha256 pinned in `mnemos/native_wheel.py`, extracts it under `$HERMES_HOME/mnemos/native/mnemos-native-v0.2.2/`, appends that directory to `sys.path`, and loads it. A `mnemos_core` older than 0.2.2 is ignored. The URL is fixed and no checksum file is read from the network. A missing release, a checksum mismatch, or `MNEMOS_BACKEND=python` keeps the Python backend. `hermes mnemos doctor` reports which backend is active and why.

GitHub Actions (`.github/workflows/native-wheels.yml`) builds the wheels and attests them with `actions/attest-build-provenance`. Nothing binary is committed.

`scripts/build_core.sh` still builds the extension locally when you want that:

```bash
export MNEMOS_PYTHON=/path/to/the/python/hermes/uses
export MATURIN=maturin
./scripts/build_core.sh
```

`MNEMOS_PYTHON` must be the interpreter that actually imports plugins. On a source checkout that is the virtualenv selected by Hermes, not a different system Python.

A catalog install does not compile Rust. The Python backend performs the same snapshot encryption, hash, and signature, then submits the Sui transaction over gRPC. Checkpoints succeed in that mode.

## First chain

```bash
cd move/MemoryIndex
sui move build --dump-bytecode-as-base64 > /tmp/mnemos-bytecode.json
hermes mnemos init --bytecode /tmp/mnemos-bytecode.json
```

`init` derives a Sui key from `MNEMOS_SECRET` unless `MNEMOS_SUI_PRIVATE_KEY` is set, asks the testnet faucet for gas, publishes `MemoryIndex`, and creates the owned object. It writes `package_id` and `index_object_id` to `$HERMES_HOME/mnemos/config.json`.

## Demo

```bash
export HERMES_BIN=hermes
export SUI_BIN=sui
export MNEMOS_PYTHON=/path/to/hermes/python
export HERMES_SRC=/path/to/hermes-agent   # only for a source checkout
./scripts/demo.sh
```

The script uses two profiles under `$HOME/.hermes/profiles/` (`mnemos-demo-a` and `mnemos-demo-b`) so they share the Hermes dependency install, then deletes them.
