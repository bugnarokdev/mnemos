# Test report: shared spaces with Seal, BETA (testnet preview)

> **BETA (testnet preview).** These results cover a preview branch on Sui testnet, Walrus
> testnet and the Mysten Seal testnet key servers. They say nothing about mainnet.

## Run of 13 November 2026 (second): Python sharing lane (BETA)

| | |
| --- | --- |
| When | 13 November 2026, 02:07 to 02:28 Paris time (UTC+1) |
| Tested commit | `fd040df` on `preview/seal` (base `main` `f3dff2d`) |
| Runs | the full suite twice in a row (run 1 at 02:07, run 2 at 02:18), both green, zero errors |
| Network | as in the previous run: Sui testnet, Walrus testnet, `mysten-testnet-1`, `mysten-testnet-2`, the Mysten testnet committee through the aggregator |
| Toolchain | as in the previous run; the suite also builds the Python bindings with `scripts/build_seal_py.sh` (maturin 1.16.0) |
| Dependencies | sui-seal-client `v0.2.0` (`da579f6`) for both lanes: git dependency of the native core, and Python bindings built from the same commit (`sui_seal_client` 0.2.0); packages unchanged |

Results, identical in both runs:

| Suite | Result |
| --- | --- |
| `cargo fmt --check` | clean |
| `cargo test --release` (default features) | 8 passed |
| `cargo test --release --features seal` | 16 passed (native lane unchanged) |
| `cargo clippy --release --all-targets -D warnings` | clean, with and without `seal` |
| `sui move test` in `move/SharedSpace` | 7 passed |
| `pytest`, `MNEMOS_REQUIRE_RUST=1 MNEMOS_REQUIRE_SEAL_PY=1` | 106 passed (62 before + 44: `test_share.py` now holds 57 checks, see below) |
| `pytest`, `MNEMOS_BACKEND=python MNEMOS_REQUIRE_SEAL_PY=1` | 105 passed, 1 skipped (`test_chain.py`, native module optional, unrelated to sharing); no share test is skipped any more |
| Personal end-to-end: rust, python, cross, guard | all passed, fresh indexes in each run |
| Coop end-to-end, `scripts/e2e_coop.sh` | 84 assertions passed (the 52 of the previous run + 32 new) |

`tests/test_share.py` (57): lane selection and refusal without the bindings; the
per-lane tests (signer, refused settings, key file, Cap) on each lane; parity: both lanes
accept or refuse the same 34 settings with the same message and derive the same signer
from 7 key forms; the Python lane against the native unit vectors (key id, header server
sets, Space parsing), the exact entry bytes, and an offline encrypt/decrypt with
in-process key servers.

New coop assertions (cross-lane):

1. No gas: Python `whoami` gives the native addresses for A, B and C; Python refuses
   `create --threshold 3` before any transaction; Python `status` and `pull` of the
   default space equal the native output.
2. Python A creates a space (default 2-of-2, stored in `share.json` exactly like a native
   one) and adds B (epoch 0).
3. Python writes, native reads: native B reads the Python entry exactly (header 2-of-2,
   author A). Native writes, Python reads: native A pushes to the Python-created space and
   Python B reads both entries; Python and native pulls of B are identical. Python C is
   refused (exit 3).
4. Removal through the Python lane: epoch 0 → 1; the new Python entry carries epoch 1;
   `status` is identical on both lanes. B is refused for every entry on both lanes and
   keeps only what it read before. A live Python session of B still decrypts the old
   entries from held keys (`no_cached_key`, then `no_access` for the new one), as
   documented for the native lane. A reads all 3 entries, identically on both lanes.
5. Committee: Python B reads the native committee entry; Python A pushes to the committee
   space (the stored setting is followed) and native B reads it with a committee header.
6. 1-of-2: Python A reads the native entry; Python A pushes with threshold 1 and native A
   reads it.
7. The Python lane re-reads the space of 12 November identically.

Gas, measured from balances before and after each run: A paid 0.061473816 SUI per run
(19 transactions: the 11 of the previous run, 0.040277868 SUI, plus 8 cross-lane ones,
0.021195948 SUI), 0.122947632 SUI for both. Before the runs, a write smoke test of the
Python lane (create, add, push, remove: 4 transactions) cost A 0.01190498 SUI. The
personal Mnemos address paid 0.028414176 SUI per run, as before.

Run 1 (cross-lane transactions; the other spaces are as in the previous run):

| Space | Space object | Transactions |
| --- | --- | --- |
| created by the Python lane (2-of-2) | [`0x9b374945…5d97`](https://testnet.suivision.xyz/object/0x9b374945e5dc14ed03c4cd96171d370068d84998056efe1bc93cff2d15ad5d97) | Python create [`4FyKtfrrmY…`](https://testnet.suivision.xyz/txblock/4FyKtfrrmYQFEqb7FzW2FD6ZZpckAGJwE2sSZzJ3Hf1H), Python add B [`3uhuvRJ948…`](https://testnet.suivision.xyz/txblock/3uhuvRJ948mZYJnf2cUrMGQzvwHRmhkx7nG98mN9U9SS), Python push [`6Jb6a768uR…`](https://testnet.suivision.xyz/txblock/6Jb6a768uRrX37gFxuuqVxhaBx8jvvQWu1XuvgKdYV6A) · [`5v5o3RGM6S…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/5v5o3RGM6SWJOevmcx593G8UYLeW7L_4wZaTzV6XhTg), native push [`7jSJLsL18K…`](https://testnet.suivision.xyz/txblock/7jSJLsL18K6L7APkeYEHpujMTycRZqPPw7pyLWwcFeEk) · [`wbmepVztU_…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/wbmepVztU_knX5-ZvcBwYjscxQ1OLPfDHXYITjewG1w), Python remove B [`ARTddNLsVC…`](https://testnet.suivision.xyz/txblock/ARTddNLsVC5CHqaQeJHK23ZxXSVMaRM3WXGYZKVyfN4R), Python push [`HfzrK5FB1J…`](https://testnet.suivision.xyz/txblock/HfzrK5FB1JnhZoZ4odiQ3Qm7gmxsXL3WiiwdztNfdNsy) · [`H8RNoQZEyc…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/H8RNoQZEycXKpPFLGY_a9xLBbAEgUu2dFkab2aMX8MA) |
| committee (the run's) | [`0x88a39ff7…e217`](https://testnet.suivision.xyz/object/0x88a39ff7800dd1921cdb3502b453edb9a8dce3a8ec836efc55127185452de217) | Python push [`Ee14qjTnak…`](https://testnet.suivision.xyz/txblock/Ee14qjTnakEqHo6rjsNJyos5uQUnUqZoSP4khUAQ6hbP) · [`dBpyoRCfcn…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/dBpyoRCfcn-UeVeE9EXgNmOZ5HYw6SGHPRWdzSzzhXY) |
| 1-of-2 (the run's) | [`0xae7605f8…f87d`](https://testnet.suivision.xyz/object/0xae7605f84abaa52fb76bc026f2c626e1fd1e1f78aa7af402975b66e5e43cf87d) | Python push [`4Lo3HzFtuV…`](https://testnet.suivision.xyz/txblock/4Lo3HzFtuVbiWAbC6ZzP8Z5WwFTomM2BYpaoBboF8dL4) · [`a6w2Phl9PO…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/a6w2Phl9POD7cjsC79exTUDColKj8fEgo8rmp3c8gYE) |

Run 2:

| Space | Space object | Transactions |
| --- | --- | --- |
| created by the Python lane (2-of-2) | [`0x148254f5…bb0f`](https://testnet.suivision.xyz/object/0x148254f58f5e1cf4f570ee1d2da906122f6186941d009c983849fdbe44e8bb0f) | Python create [`EkVfxi41uV…`](https://testnet.suivision.xyz/txblock/EkVfxi41uVNZPJLQpBUqcwHZHJjeUBsRDcuphjA63Jfq), Python add B [`9hLaEvDakA…`](https://testnet.suivision.xyz/txblock/9hLaEvDakAx3Q7zS4PUWo3rYrTTjF9YRs1oRHt1kqV1A), Python push [`3iexDinELq…`](https://testnet.suivision.xyz/txblock/3iexDinELqWHytYxrEcx1fzmCJW9Q71qFpamgTFY1srB) · [`VXMVm8-Q1N…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/VXMVm8-Q1NztuBwh2j4uWeAJqWiVvpY8R7eqRw4auCY), native push [`9U26PTMeex…`](https://testnet.suivision.xyz/txblock/9U26PTMeexaQbS6ig1WYirzDYrc8RocknQRNAbEjFiZU) · [`o7ScgiqCvQ…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/o7ScgiqCvQhFsLbmd33pn25ADvWrn1a0YgG219PVB9M), Python remove B [`BZsN2Bv33S…`](https://testnet.suivision.xyz/txblock/BZsN2Bv33Sv48YwzrX8251ZZCheTEH7YGojuwMexbktr), Python push [`DiTZ4ucaV5…`](https://testnet.suivision.xyz/txblock/DiTZ4ucaV5VgkXx8L9u9fF1JZk7x72UPTSCaRPSNGmAP) · [`8D_RKiardW…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/8D_RKiardWSbnAZzyIwyoDVesgHL3HiwEWCfiH8SDvg) |
| committee (the run's) | [`0x56ee047f…db3f`](https://testnet.suivision.xyz/object/0x56ee047f151b15ecd1731aa7716071f51414d0fa2b2639a0b2a417b5ad78db3f) | Python push [`DmUBroFd1c…`](https://testnet.suivision.xyz/txblock/DmUBroFd1c9NE9VNgVgU59N3TxGbbVeVuJCVftX5yfuM) · [`mifFrPEK-f…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/mifFrPEK-ffmfSDvsJ55JmJGDSbZFA55wt3n9QT0WnM) |
| 1-of-2 (the run's) | [`0xf6fbdd7f…dcca`](https://testnet.suivision.xyz/object/0xf6fbdd7f48252f07274f47bff625b7f649ca6d695dcd9d0e49bab19287c9dcca) | Python push [`G4adaGVo5a…`](https://testnet.suivision.xyz/txblock/G4adaGVo5awGdWYfFhzzkNh6Nf3RGZ8dEFP97w1xPcsv) · [`MiXSSrxS90…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/MiXSSrxS90lFQ3eFtIfhouBQol3DXLsEGKvlTwrQi-s) |

## Run of 13 November 2026: sui-seal-client v0.2.0, key servers (BETA)

| | |
| --- | --- |
| When | 13 November 2026, 01:23 to 01:37 Paris time (UTC+1) |
| Tested commit | `4e4096c` on `preview/seal` (base `main` `f3dff2d`) |
| Runs | the full suite twice in a row (run 1 at 01:23, run 2 at 01:30), both green, zero errors |
| Network | Sui testnet, Walrus testnet, Seal `mysten-testnet-1`, `mysten-testnet-2` and the Mysten testnet committee `0xb012…1e98` through `seal-aggregator-testnet.mystenlabs.com` |
| Toolchain | Linux x86_64, rustc 1.99.1, Python 3.12.14, pytest 9.2.0, sui CLI 1.84.0, mnemos_core 0.2.3 built from the tested commit with `--features seal` |
| Dependencies | sui-seal-client `v0.2.0` (`da579f6`, git), allowlist package `0xe10f…7f1b`, Space package `0xa40e…00d1` (unchanged) |

Results, identical in both runs:

| Suite | Result |
| --- | --- |
| `cargo fmt --check` | clean (new in this run; `50e5dbb` formats the core, no code change) |
| `cargo test --release` (default features) | 8 passed |
| `cargo test --release --features seal` | 16 passed: the 8 existing + 8 for the lane (4 new: key server profiles, refused settings, header server sets, fetch errors incl. wrong owner) |
| `cargo clippy --release --all-targets -D warnings` | clean, with and without `seal` |
| `sui move test` in `move/SharedSpace` | 7 passed |
| `pytest`, `MNEMOS_REQUIRE_RUST=1` | 62 passed (58 before + 4 new in `test_share.py`) |
| `pytest`, `MNEMOS_BACKEND=python` | 57 passed, 5 skipped (the native-only share tests) |
| Personal end-to-end: rust, python, cross, guard | all passed, fresh indexes in each run |
| Coop end-to-end, `scripts/e2e_coop.sh` | 52 assertions passed (the 34 below + 18 new) |

New coop assertions:

1. The default space still reports the first preview's settings (2-of-2 on the two open
   servers), nothing extra is stored for it, and its entry headers name those servers.
2. `create --threshold 3` is refused before any transaction; no space is recorded.
3. `create --committee`: entries go to the committee server with threshold 1, the choice
   is stored in `share.json`, and `push` uses it without a flag.
4. B, with default settings, reads the committee entry through the aggregator; the
   header names the committee. C (not a member) is refused by the committee.
5. `create --key-server … --key-server … --threshold 1`: A pushes and reads the 1-of-2
   entry, its header says threshold 1; C is still refused.
6. The default space reads 4 entries with 2-of-2 headers after the other spaces exist.
7. Backward compatibility: A re-reads the space written on 12 November by the previous
   client (`f4ffa2f`), [`0xe365cc79…eab8`](https://testnet.suivision.xyz/object/0xe365cc796cebbfd2269db66f48c49240919f46ba0647ab941a8671858501eab8): 4 entries, ok 4,
   exact texts, 2-of-2 headers. No gas.

Gas, measured from balances before and after each run: A paid 0.040277868 SUI per run
(11 transactions: 6 for the default space, 3 for the committee space, 2 for the 1-of-2
space), 0.080555736 SUI for both. The personal Mnemos address paid 0.028414176 SUI per
run for the personal end-to-end and A's MemoryIndex. Walrus uploads go through the
public testnet publisher.

Run 1:

| Space | Space object | Transactions |
| --- | --- | --- |
| default (2-of-2) | [`0x8b0fb738…1a86`](https://testnet.suivision.xyz/object/0x8b0fb73809661d7648d4232d57f7229ef31be5237676f74169771d3f752b1a86) | create [`71paHvaZvV…`](https://testnet.suivision.xyz/txblock/71paHvaZvVqRZEUUkiAtjmrPCbwp3uQwQfE1SNKci3gt) |
| committee | [`0x664e7e2a…8138`](https://testnet.suivision.xyz/object/0x664e7e2ad746811cd22ebfd95109377ac2bd422315f10c9ab43d6e26ecc98138) | create [`HkwfK6m6pd…`](https://testnet.suivision.xyz/txblock/HkwfK6m6pdfQJM3RxF8K2bZ5jemfkTqECwHYJWCvWhsq), add B [`9tX6WHNg2Q…`](https://testnet.suivision.xyz/txblock/9tX6WHNg2Q5XauwPdG18gMHv5XgPdGArShV9X6Gzsy5X), push [`DDr57ZixZ9…`](https://testnet.suivision.xyz/txblock/DDr57ZixZ9zE4VR8FPZguvGURB2proNc7MsLZLDLos3h) · [`PvAqMcWzs-…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/PvAqMcWzs-VH6bZHH6DbA7CEGmqLhens6sWlaElpe_U) |
| 1-of-2 | [`0xc6863a29…9fa0`](https://testnet.suivision.xyz/object/0xc6863a29996c3726a0223ed3900ca289e771b5d378d47c4b8dde8ea3a9ac9fa0) | create [`HjPTcEaRCJ…`](https://testnet.suivision.xyz/txblock/HjPTcEaRCJsHPAen9V2Ko4zrNJQBBPeJ9FT5KpoXyyy2), push [`HePoiStxfe…`](https://testnet.suivision.xyz/txblock/HePoiStxfeMJSdkU3fgwKrrHEKTBrVuCVQVxJGkpz4eq) · [`e80kSbr3Gk…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/e80kSbr3GkKshHN7xPKS1m3VoE8zDOy3EcyAxOYwsP8) |
| MemoryIndex A | [`0xe0969f98…fbf8`](https://testnet.suivision.xyz/object/0xe0969f989aa4551441363c3e9444019634d129f130129008cb2b4ea14998fbf8) | |

Run 2:

| Space | Space object | Transactions |
| --- | --- | --- |
| default (2-of-2) | [`0xe90d4b48…902c`](https://testnet.suivision.xyz/object/0xe90d4b485266e660675613b6fd377eaff5064b47b29746b58032b1ceffa1902c) | create [`BzjkpHuc8o…`](https://testnet.suivision.xyz/txblock/BzjkpHuc8oKYCULes3VMNawwDwEiLuLndNF2xLZGrGx3) |
| committee | [`0xb87e3655…ccfe`](https://testnet.suivision.xyz/object/0xb87e36557ccebc58e4d37fdc4d31c0e1866ccc4ef61199b9f4868910ee37ccfe) | create [`4VZyVXhubQ…`](https://testnet.suivision.xyz/txblock/4VZyVXhubQBAXRp7EJpsTKgySmjE7xhtvsmVPqophGqg), add B [`9UtqxeLDYu…`](https://testnet.suivision.xyz/txblock/9UtqxeLDYuUw6LxYvbB3TXouWdsBWtv9GNugvMkCUoBF), push [`4q2S2PMB4B…`](https://testnet.suivision.xyz/txblock/4q2S2PMB4BAK1p1mK5Ss63VxjRKMy5aJDFqyuidmE9Ee) · [`jjXIAQ3Ixj…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/jjXIAQ3IxjZSeWlC5aWZps29o1vVr-q5kTcXHPTj3Gs) |
| 1-of-2 | [`0x0c07f3ba…084b`](https://testnet.suivision.xyz/object/0x0c07f3bac63b2a8463ebcbc68c66bf0249971d304eb1380506cab346e186084b) | create [`9qcapV3W6v…`](https://testnet.suivision.xyz/txblock/9qcapV3W6vkQstghpUFZbiKWpNNAdEj3vJevay8cyrEL), push [`CoSbTaxE4M…`](https://testnet.suivision.xyz/txblock/CoSbTaxE4M2tca2gx6BA5dtRwRRqq6q45unPigbdDJve) · [`E168Vnvec3…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/E168Vnvec3DubC6wlN_EKYgLMRHd4DxGRClRbil8Xrc) |
| MemoryIndex A | [`0x8b7db4f1…3737`](https://testnet.suivision.xyz/object/0x8b7db4f1cf2cc30dd5be77f790769744cb23528a2fee01536227e84fa3153737) | |

## Run of 12 November 2026: first preview, sui-seal-client `f4ffa2f`

| | |
| --- | --- |
| When | 12 November 2026, 13:36 to 13:47 Paris time (UTC+1) |
| Tested commit | `0005597` on `preview/seal` (base `main` `f3dff2d`) |
| Runs | the full suite twice in a row (run 1 at 13:36, run 2 at 13:42), both green |
| Network | Sui testnet (chain id `4c78adac`), Walrus testnet, Seal `mysten-testnet-1` and `mysten-testnet-2` (threshold 2) |
| Toolchain | Linux x86_64, rustc 1.99.1, Python 3.12.14, pytest 9.2.0, sui CLI 1.84.0, mnemos_core 0.2.3 built from the tested commit with `--features seal` |
| Dependencies | sui-seal-client `f4ffa2f` (git), allowlist package `0xe10f…7f1b`, Space package `0xa40e…00d1` |

## Unit tests (identical in both runs)

| Suite | Result |
| --- | --- |
| `cargo test --release` (default features) | 8 passed (the existing 8, unchanged) |
| `cargo test --release --features seal` | 12 passed: the 8 existing + 4 for the lane |
| `cargo clippy --release --all-targets -D warnings` | clean, with and without `seal` |
| `sui move test` in `move/SharedSpace` | 7 passed |
| `pytest`, `MNEMOS_REQUIRE_RUST=1` | 58 passed: the existing 49 + 9 in `test_share.py` |
| `pytest`, `MNEMOS_BACKEND=python` | 54 passed, 4 skipped: the existing 48 passed and 1 skipped, + 6 passed and 3 skipped (native-only share tests) |

## Personal memory end-to-end (fresh indexes in each run)

Same scenario as the 0.2.3 report: two facts flushed, local store deleted, restore,
`history --verify`; then each backend extends the other's index; then the chain guard
(mainnet, devnet, unreachable RPC) on `init` and `flush`, both backends.
All passed in both runs: restore returned 2 entries, `OK: 2 versions verified`, cross
writes `OK: 3 versions verified`, and all 12 guard attempts refused before signing.

Run 1:

| Backend | MemoryIndex | Flushes | Restored entries |
| --- | --- | --- | --- |
| rust | [`0xad799252…43ad`](https://testnet.suivision.xyz/object/0xad799252ddb5e600bc09c5b0654fe7ae84c4a1066ac8b38ecbec99994beb43ad) | [`KUQXx4ft6x…`](https://testnet.suivision.xyz/txblock/KUQXx4ft6xPZdkQw3MJYDTBokpoLwwQ1fBGbabcdd99), [`BLPcZUkuXJ…`](https://testnet.suivision.xyz/txblock/BLPcZUkuXJffM6xu1uLieQU6ab2BJLqhXogNuJnNGFNh) | 2 |
| python | [`0x9f2e4108…0b32`](https://testnet.suivision.xyz/object/0x9f2e410848929e8cbb9f4985bfed6d5dd24d790d85103ac2aa696efaadc00b32) | [`D41d5VNnVq…`](https://testnet.suivision.xyz/txblock/D41d5VNnVqujfdRzHkBkF89KEzfJ6EtASob4RbfZgxag), [`2SwTFfb9zo…`](https://testnet.suivision.xyz/txblock/2SwTFfb9zo9T9JMHr8ovN1CzE6fuv537xXMTS7yY7PPk) | 2 |
| cross rust on python | | [`4Jpb5cbYb9…`](https://testnet.suivision.xyz/txblock/4Jpb5cbYb9ZGcBA7dbA798X6Y2b2CTDBSEbhgjPgRNxF) | 3 |
| cross python on rust | | [`3T8ypt8xxz…`](https://testnet.suivision.xyz/txblock/3T8ypt8xxzqGWMpZ7PzTG5c7CAPca1gAhiF5DuWNDXfd) | 3 |

Run 2:

| Backend | MemoryIndex | Flushes | Restored entries |
| --- | --- | --- | --- |
| rust | [`0xab495139…7057`](https://testnet.suivision.xyz/object/0xab495139313ceba1eb84eb5b7a902111d5a9905ef423b32b820c78d7dbfd7057) | [`56Y9kQB7f8…`](https://testnet.suivision.xyz/txblock/56Y9kQB7f8rFzsb7yZmE1sdRqRS2p3CP4UXFCcwsETwq), [`H1tVyYt94d…`](https://testnet.suivision.xyz/txblock/H1tVyYt94dcaFV6WhEyeCEg62PiSQKS4nv66Xo1pv8cy) | 2 |
| python | [`0x1481e1b8…28b3`](https://testnet.suivision.xyz/object/0x1481e1b8658755f4d38a49da4897e14b94d3697f999c3eaa88d74c341fbe28b3) | [`3BEaCbkiwn…`](https://testnet.suivision.xyz/txblock/3BEaCbkiwnxGFAYpZU86JiKs93WKq5oCkfTPAux5NucB), [`FzpeLbTTTr…`](https://testnet.suivision.xyz/txblock/FzpeLbTTTrXYYBooHLs7HUrh6t4ZqEGzBvYDASZogGA4) | 2 |
| cross rust on python | | [`HGZT4JF6Pc…`](https://testnet.suivision.xyz/txblock/HGZT4JF6PcrGL4NPPo6FZWp27LnasH4jUcHTJ9rUF8B9) | 3 |
| cross python on rust | | [`HzU1RdFNzF…`](https://testnet.suivision.xyz/txblock/HzU1RdFNzFZwvLJa2jdkjqGq9qsnCQ3PqHsqnHnk84vc) | 3 |

## Coop end-to-end, BETA (testnet preview): `scripts/e2e_coop.sh`

34 assertions, all passed in both runs. Signers: A `0xf857…322a` (pays gas),
B `0x0fd9…c263`, C `0x2802…38ee`.

1. A's personal MemoryIndex is created fresh and gets two facts.
2. A creates the space (epoch 0) and adds B.
3. A pushes 2 texts and 1 entry taken from its personal memory (`--from-memory`).
   A's personal store is byte-identical afterwards.
4. B pulls: ok 3, no_access 0, no_cached_key 0, error 0; exact plaintexts, in order, all by A.
5. C pulls: ok 0, no_access 3, no_cached_key 0, error 0; no plaintext returned (exit code 3).
6. A removes B: epoch 0 → 1. A pushes a new entry; its key id carries epoch 1.
7. B in a new process: ok 0, no_access 4, no_cached_key 0, error 0. B's local cache
   still holds the 3 texts read before; nothing for the new entry.
8. **Documented behavior:** B's long-lived session, using only keys fetched before the
   removal: ok 3, no_access 0, no_cached_key 1, error 0. The 3 old entries decrypt;
   the epoch-1 entry does not. Asking the key servers again:
   ok 3, no_access 1, no_cached_key 0, error 0 (the epoch-1 key is refused).
9. A pulls: ok 4, no_access 0, no_cached_key 0, error 0. C is still refused for all 4.
10. A's personal memory: `mnemos.sqlite` and `config.json` unchanged by sharing; local
    store wiped; restore returns the 2 exact facts (Argon2id), no shared entry leaks in;
    `history --verify`: `OK: 2 versions verified`.

Run 1 objects and transactions:

| Step | Link |
| --- | --- |
| Space | [`0xbc10f7fb…c09e`](https://testnet.suivision.xyz/object/0xbc10f7fb6a46c21467fd9ea1c0cfb3de9f5a4c64622e125471167410f9abc09e) |
| Allowlist | [`0xd6c5ec9f…2f37`](https://testnet.suivision.xyz/object/0xd6c5ec9fba06ec42cf6cfd5f3ae68a90a68c5a4c1fab21fa320bcb94a5fa2f37) |
| Cap | [`0x9333a1f9…ea86`](https://testnet.suivision.xyz/object/0x9333a1f9ea0981eb393de64e2dfc917ebba30b82e915b2d7321b320e2629ea86) |
| create | [`57smWQ9sZF…`](https://testnet.suivision.xyz/txblock/57smWQ9sZFyM1bwuUH7ErCdjkZAnmpYvhYfbUgbdAako) |
| add B | [`HEm5eeFvTy…`](https://testnet.suivision.xyz/txblock/HEm5eeFvTytLS8m3QmrDokhpa2TpD8rSqEME2rhKSGAF) |
| push epoch 0 (2 texts) | [`EJe6MFAD9g…`](https://testnet.suivision.xyz/txblock/EJe6MFAD9gUz7pFgtQ75DNwiwdf131S7GFPtSGLgxhqp) · [`MR_NNFvqfI…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/MR_NNFvqfIc7lfKIOlywb_nxuY6Ocafsob-5Wx2r5MM), [`_Cw8wiI-kH…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/_Cw8wiI-kHRha0Yebyw997G8yTjcit1lL4qOLFA413g) |
| push epoch 0 (--from-memory) | [`8YZg5ss9f3…`](https://testnet.suivision.xyz/txblock/8YZg5ss9f3abt2JDSDknkQdMKH5VApBosTkKDiNt3nMv) · [`NfcqNIkg92…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/NfcqNIkg92wgAiiklXojwKcSCW2blkQaw-nkwFMsfI8) |
| remove B (epoch 0 → 1) | [`5Rwn1i1VMs…`](https://testnet.suivision.xyz/txblock/5Rwn1i1VMsAk9A41v2RUBzDYcu1KD9tjk2K5QPUZimnN) |
| push epoch 1 | [`5oVrFqq5P3…`](https://testnet.suivision.xyz/txblock/5oVrFqq5P3RdoNM8755ozKouUTcxskVog5XRCted94oA) · [`C9lq623Y29…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/C9lq623Y293-2D06a418VY6Xnr7vBH1ZmbV8_aeUPqg) |
| MemoryIndex A | [`0x30db5d1e…129c`](https://testnet.suivision.xyz/object/0x30db5d1ea434f92ef619e2b02bbd6e27c2de39857fe7e6d8ab54ad4971bf129c) · flush [`9BrHUBexM1…`](https://testnet.suivision.xyz/txblock/9BrHUBexM183ugwkZEJw1eUUmEeXHNY874SC1XCTPbPL), [`4jcTda47rv…`](https://testnet.suivision.xyz/txblock/4jcTda47rvAJMThJK5YVaQWYGrp7pMr4yoviJADKa1zF) |

Run 2 objects and transactions:

| Step | Link |
| --- | --- |
| Space | [`0xc544e5c6…6628`](https://testnet.suivision.xyz/object/0xc544e5c6d6864e394968128d7d3889299d9e9f62de6b9ec9802a8d5971476628) |
| Allowlist | [`0x5a2edd35…3829`](https://testnet.suivision.xyz/object/0x5a2edd35c90e352e3460cf86f046d838410f2a964a2132d3d35dc4d73f613829) |
| Cap | [`0x8effc341…060f`](https://testnet.suivision.xyz/object/0x8effc341f11bf451ec406d822cbc4409b9174f9b1ad3e579e249f57ee664060f) |
| create | [`4S5PVpt4Cz…`](https://testnet.suivision.xyz/txblock/4S5PVpt4CzXnjF1TQLSPwrdQijK4N9f5wLyZUUXXASf8) |
| add B | [`Fc7ZTrq8sR…`](https://testnet.suivision.xyz/txblock/Fc7ZTrq8sRb3L6vQwTZkNUGcpqDUnwtJT5NLfsgZ7wLV) |
| push epoch 0 (2 texts) | [`Rmqs3Ncpff…`](https://testnet.suivision.xyz/txblock/Rmqs3NcpffCZGiXjZyWzYGmq1b6k6wWU9vTqo6q2cgW) · [`SHLEbceaAk…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/SHLEbceaAkSapP5PHuCAu1lACv_515-tx4I0bu2VcwA), [`VuTomRfOIx…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/VuTomRfOIxIun5Vz9lHzrdoNHAIW0kTy1bfRlttx1Fg) |
| push epoch 0 (--from-memory) | [`8KRaEcFiTS…`](https://testnet.suivision.xyz/txblock/8KRaEcFiTSrvnFx3jKtYbSN2yM5jL5NX97kAAosRjXzJ) · [`qadkYkyR3q…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/qadkYkyR3qHTA4GIiFWR6Z4F16j6yZ7lJ_6xYoi7Ol4) |
| remove B (epoch 0 → 1) | [`Cq3jGaReYV…`](https://testnet.suivision.xyz/txblock/Cq3jGaReYVkEtgQqb9rc4cJDSPmf9BKb8HbugLy9KM2v) |
| push epoch 1 | [`AF5XPmLWJY…`](https://testnet.suivision.xyz/txblock/AF5XPmLWJYn1DYsrMPEHx2Va1Pbhp1BudPFoi4jVPJz5) · [`HLTyTiS4ay…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/HLTyTiS4ayzlF0w07woFPEHvGnFYUxGHkyogfAultJQ) |
| MemoryIndex A | [`0x21aea546…aa39`](https://testnet.suivision.xyz/object/0x21aea546453738e42ea33bc3dfbe151c0aad2f78353d26dfcb2814509cfbaa39) · flush [`5xscUChcaR…`](https://testnet.suivision.xyz/txblock/5xscUChcaRsEKDCZGkhfs6SGF6fDT7xxfdY7Xyez6nr6), [`7KFDmA1TW4…`](https://testnet.suivision.xyz/txblock/7KFDmA1TW4pcQ93D2MKp1RFHGVQ9aXUYK3GnE91qz93N) |

## Space package

Published by the owner address `0xa0ea…8c06` in
[`Bwx3yVdYci…`](https://testnet.suivision.xyz/txblock/Bwx3yVdYci6XQXMXecpt3eYHCyvZr46maSQuyZfSbddM): package
[`0xa40e74c6…00d1`](https://testnet.suivision.xyz/object/0xa40e74c6ae866f18e86e9dcdfc9d29af436597264991fc81b4a289519f3500d1), UpgradeCap
[`0x27a9d878…1b96`](https://testnet.suivision.xyz/object/0x27a9d878f827ca3693cb5fdc963ce7d87897ffc742de8b0183f8a6040f6f1b96). Its linkage
table points to the allowlist package `0xe10f…7f1b` version 1.
