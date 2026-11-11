# Overview

Mnemos is a Hermes memory provider. Hermes already keeps `MEMORY.md` and `USER.md`. Mnemos is the external provider selected with `memory.provider: mnemos`. It adds a portable store the agent can rebuild on another machine.

What the agent can do:

- `mnemos_remember` stores a fact in the local cache and the outbox.
- `mnemos_recall` searches that cache. It does not call the network.
- `mnemos_flush` encrypts the outbox, uploads one blob to Walrus, and appends the owned `MemoryIndex` on Sui.
- `mnemos_status` and `hermes mnemos doctor` report the local store, the active backend, and why that backend was selected.

Hermes also calls the provider on its own. `prefetch` injects cached recall before a turn. `sync_turn` and `on_memory_write` enqueue work without blocking the turn. `on_pre_compress` (checkpoint API v2) archives the transcript evidence before compaction and raises if that archive did not commit. `on_session_end` tries to flush, then gives up after 20 seconds so an unattended run cannot hang.

Restore is `hermes mnemos restore`. Hermes does not offer a plugin slot named `hermes memory restore`. `hermes memory status` is the built-in status command and prints the Mnemos config. Doctor and restore live on `hermes mnemos`.

This release targets Sui testnet and Walrus testnet. Mainnet, Seal sharing, and a history browser are intentionally later.
