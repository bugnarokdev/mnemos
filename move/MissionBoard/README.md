# Mnemos mission board (BETA)

**BETA (testnet preview), not published, not audited.** Move package for the cooperative
mission board described in `docs/fr/design-mission-board.md` (phase P1: local build and
tests only).

- Module `mnemos_board::board`: one shared `Board` per project with a single project lead
  (two-step transfer), one shared `Mission` per task carrying the claim lock,
  `seal_approve` / `seal_approve_parent` for Seal key servers, per-board reputation and
  non-transferable `DeliveryReceipt`s.
- Depends only on the implicit Sui framework and Move stdlib.

```sh
sui move build --lint      # no warnings
sui move test              # unit tests (tests/board_tests.move)
SUI=sui negative/check.sh  # receipt and admin cap must not be transferable
```

Seal never takes back a key already served: expiry, revocation or a lead transfer cut
access to future keys only.
