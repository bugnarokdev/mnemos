# Rapport de tests : espaces partagés avec Seal, BETA (testnet preview)

> **BETA (testnet preview).** Ces résultats portent sur une branche de preview, sur Sui
> testnet, Walrus testnet et les serveurs de clés Seal testnet de Mysten. Ils ne disent
> rien du mainnet.

| | |
| --- | --- |
| Quand | 12 novembre 2026, de 13 h 36 à 13 h 47, heure de Paris (UTC+1) |
| Commit testé | `0005597` sur `preview/seal` (base `main` `f3dff2d`) |
| Passages | la suite complète deux fois de suite (passage 1 à 13 h 36, passage 2 à 13 h 42), tous deux au vert |
| Réseau | Sui testnet (chain id `4c78adac`), Walrus testnet, Seal `mysten-testnet-1` et `mysten-testnet-2` (seuil 2) |
| Outils | Linux x86_64, rustc 1.99.1, Python 3.12.14, pytest 9.2.0, sui CLI 1.84.0, mnemos_core 0.2.3 compilé depuis le commit testé avec `--features seal` |
| Dépendances | sui-seal-client `f4ffa2f` (git), package allowlist `0xe10f…7f1b`, package Space `0xa40e…00d1` |

## Tests unitaires (identiques aux deux passages)

| Suite | Résultat |
| --- | --- |
| `cargo test --release` (features par défaut) | 8 réussis (les 8 existants, inchangés) |
| `cargo test --release --features seal` | 12 réussis : les 8 existants + 4 pour la voie de partage |
| `cargo clippy --release --all-targets -D warnings` | propre, avec et sans `seal` |
| `sui move test` dans `move/SharedSpace` | 7 réussis |
| `pytest`, `MNEMOS_REQUIRE_RUST=1` | 58 réussis : les 49 existants + 9 dans `test_share.py` |
| `pytest`, `MNEMOS_BACKEND=python` | 54 réussis, 4 ignorés : les 48 réussis et 1 ignoré existants, + 6 réussis et 3 ignorés (tests de partage natifs uniquement) |

## Mémoire personnelle de bout en bout (index neufs à chaque passage)

Même scénario que le rapport 0.2.3 : deux faits envoyés, stockage local supprimé,
restauration, `history --verify` ; puis chaque backend prolonge l'index de l'autre ; puis
le garde-fou de chaîne (mainnet, devnet, RPC injoignable) sur `init` et `flush`, pour les
deux backends. Tout est passé aux deux passages : restauration de 2 entrées,
`OK: 2 versions verified`, écritures croisées `OK: 3 versions verified`, et les 12
tentatives du garde-fou refusées avant toute signature.

Passage 1 :

| Backend | MemoryIndex | Flushs | Entrées restaurées |
| --- | --- | --- | --- |
| rust | [`0xad799252…43ad`](https://testnet.suivision.xyz/object/0xad799252ddb5e600bc09c5b0654fe7ae84c4a1066ac8b38ecbec99994beb43ad) | [`KUQXx4ft6x…`](https://testnet.suivision.xyz/txblock/KUQXx4ft6xPZdkQw3MJYDTBokpoLwwQ1fBGbabcdd99), [`BLPcZUkuXJ…`](https://testnet.suivision.xyz/txblock/BLPcZUkuXJffM6xu1uLieQU6ab2BJLqhXogNuJnNGFNh) | 2 |
| python | [`0x9f2e4108…0b32`](https://testnet.suivision.xyz/object/0x9f2e410848929e8cbb9f4985bfed6d5dd24d790d85103ac2aa696efaadc00b32) | [`D41d5VNnVq…`](https://testnet.suivision.xyz/txblock/D41d5VNnVqujfdRzHkBkF89KEzfJ6EtASob4RbfZgxag), [`2SwTFfb9zo…`](https://testnet.suivision.xyz/txblock/2SwTFfb9zo9T9JMHr8ovN1CzE6fuv537xXMTS7yY7PPk) | 2 |
| cross rust on python | | [`4Jpb5cbYb9…`](https://testnet.suivision.xyz/txblock/4Jpb5cbYb9ZGcBA7dbA798X6Y2b2CTDBSEbhgjPgRNxF) | 3 |
| cross python on rust | | [`3T8ypt8xxz…`](https://testnet.suivision.xyz/txblock/3T8ypt8xxzqGWMpZ7PzTG5c7CAPca1gAhiF5DuWNDXfd) | 3 |

Passage 2 :

| Backend | MemoryIndex | Flushs | Entrées restaurées |
| --- | --- | --- | --- |
| rust | [`0xab495139…7057`](https://testnet.suivision.xyz/object/0xab495139313ceba1eb84eb5b7a902111d5a9905ef423b32b820c78d7dbfd7057) | [`56Y9kQB7f8…`](https://testnet.suivision.xyz/txblock/56Y9kQB7f8rFzsb7yZmE1sdRqRS2p3CP4UXFCcwsETwq), [`H1tVyYt94d…`](https://testnet.suivision.xyz/txblock/H1tVyYt94dcaFV6WhEyeCEg62PiSQKS4nv66Xo1pv8cy) | 2 |
| python | [`0x1481e1b8…28b3`](https://testnet.suivision.xyz/object/0x1481e1b8658755f4d38a49da4897e14b94d3697f999c3eaa88d74c341fbe28b3) | [`3BEaCbkiwn…`](https://testnet.suivision.xyz/txblock/3BEaCbkiwnxGFAYpZU86JiKs93WKq5oCkfTPAux5NucB), [`FzpeLbTTTr…`](https://testnet.suivision.xyz/txblock/FzpeLbTTTrXYYBooHLs7HUrh6t4ZqEGzBvYDASZogGA4) | 2 |
| cross rust on python | | [`HGZT4JF6Pc…`](https://testnet.suivision.xyz/txblock/HGZT4JF6PcrGL4NPPo6FZWp27LnasH4jUcHTJ9rUF8B9) | 3 |
| cross python on rust | | [`HzU1RdFNzF…`](https://testnet.suivision.xyz/txblock/HzU1RdFNzFZwvLJa2jdkjqGq9qsnCQ3PqHsqnHnk84vc) | 3 |

## Coop de bout en bout, BETA (testnet preview) : `scripts/e2e_coop.sh`

34 assertions, toutes réussies aux deux passages. Signataires : A `0xf857…322a` (paie le
gas), B `0x0fd9…c263`, C `0x2802…38ee`.

1. Le MemoryIndex personnel de A est créé neuf et reçoit deux faits.
2. A crée l'espace (epoch 0) et ajoute B.
3. A publie 2 textes et 1 entrée tirée de sa mémoire personnelle (`--from-memory`). Le
   stockage personnel de A reste identique à l'octet près.
4. B récupère : ok 3, no_access 0, no_cached_key 0, error 0 ; textes exacts, dans l'ordre, tous signés A.
5. C récupère : ok 0, no_access 3, no_cached_key 0, error 0 ; aucun texte en clair (code de sortie 3).
6. A retire B : epoch 0 → 1. A publie une nouvelle entrée ; son id de clé porte l'epoch 1.
7. B dans un nouveau processus : ok 0, no_access 4, no_cached_key 0, error 0. Le cache
   local de B contient toujours les 3 textes lus avant ; rien pour la nouvelle entrée.
8. **Comportement documenté :** la session longue de B, avec seulement les clés obtenues
   avant le retrait : ok 3, no_access 0, no_cached_key 1, error 0. Les 3 anciennes
   entrées se déchiffrent, pas celle de l'epoch 1. En redemandant aux serveurs de clés :
   ok 3, no_access 1, no_cached_key 0, error 0 (la clé de l'epoch 1 est refusée).
9. A récupère : ok 4, no_access 0, no_cached_key 0, error 0. C est toujours refusé pour les 4.
10. Mémoire personnelle de A : `mnemos.sqlite` et `config.json` inchangés par le partage ;
    stockage local effacé ; la restauration rend les 2 faits exacts (Argon2id), sans
    aucune entrée partagée ; `history --verify` : `OK: 2 versions verified`.

Objets et transactions du passage 1 :

| Étape | Lien |
| --- | --- |
| Space | [`0xbc10f7fb…c09e`](https://testnet.suivision.xyz/object/0xbc10f7fb6a46c21467fd9ea1c0cfb3de9f5a4c64622e125471167410f9abc09e) |
| Allowlist | [`0xd6c5ec9f…2f37`](https://testnet.suivision.xyz/object/0xd6c5ec9fba06ec42cf6cfd5f3ae68a90a68c5a4c1fab21fa320bcb94a5fa2f37) |
| Cap | [`0x9333a1f9…ea86`](https://testnet.suivision.xyz/object/0x9333a1f9ea0981eb393de64e2dfc917ebba30b82e915b2d7321b320e2629ea86) |
| create | [`57smWQ9sZF…`](https://testnet.suivision.xyz/txblock/57smWQ9sZFyM1bwuUH7ErCdjkZAnmpYvhYfbUgbdAako) |
| add B | [`HEm5eeFvTy…`](https://testnet.suivision.xyz/txblock/HEm5eeFvTytLS8m3QmrDokhpa2TpD8rSqEME2rhKSGAF) |
| push epoch 0 (2 textes) | [`EJe6MFAD9g…`](https://testnet.suivision.xyz/txblock/EJe6MFAD9gUz7pFgtQ75DNwiwdf131S7GFPtSGLgxhqp) · [`MR_NNFvqfI…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/MR_NNFvqfIc7lfKIOlywb_nxuY6Ocafsob-5Wx2r5MM), [`_Cw8wiI-kH…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/_Cw8wiI-kHRha0Yebyw997G8yTjcit1lL4qOLFA413g) |
| push epoch 0 (--from-memory) | [`8YZg5ss9f3…`](https://testnet.suivision.xyz/txblock/8YZg5ss9f3abt2JDSDknkQdMKH5VApBosTkKDiNt3nMv) · [`NfcqNIkg92…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/NfcqNIkg92wgAiiklXojwKcSCW2blkQaw-nkwFMsfI8) |
| remove B (epoch 0 → 1) | [`5Rwn1i1VMs…`](https://testnet.suivision.xyz/txblock/5Rwn1i1VMsAk9A41v2RUBzDYcu1KD9tjk2K5QPUZimnN) |
| push epoch 1 | [`5oVrFqq5P3…`](https://testnet.suivision.xyz/txblock/5oVrFqq5P3RdoNM8755ozKouUTcxskVog5XRCted94oA) · [`C9lq623Y29…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/C9lq623Y293-2D06a418VY6Xnr7vBH1ZmbV8_aeUPqg) |
| MemoryIndex A | [`0x30db5d1e…129c`](https://testnet.suivision.xyz/object/0x30db5d1ea434f92ef619e2b02bbd6e27c2de39857fe7e6d8ab54ad4971bf129c) · flush [`9BrHUBexM1…`](https://testnet.suivision.xyz/txblock/9BrHUBexM183ugwkZEJw1eUUmEeXHNY874SC1XCTPbPL), [`4jcTda47rv…`](https://testnet.suivision.xyz/txblock/4jcTda47rvAJMThJK5YVaQWYGrp7pMr4yoviJADKa1zF) |

Objets et transactions du passage 2 :

| Étape | Lien |
| --- | --- |
| Space | [`0xc544e5c6…6628`](https://testnet.suivision.xyz/object/0xc544e5c6d6864e394968128d7d3889299d9e9f62de6b9ec9802a8d5971476628) |
| Allowlist | [`0x5a2edd35…3829`](https://testnet.suivision.xyz/object/0x5a2edd35c90e352e3460cf86f046d838410f2a964a2132d3d35dc4d73f613829) |
| Cap | [`0x8effc341…060f`](https://testnet.suivision.xyz/object/0x8effc341f11bf451ec406d822cbc4409b9174f9b1ad3e579e249f57ee664060f) |
| create | [`4S5PVpt4Cz…`](https://testnet.suivision.xyz/txblock/4S5PVpt4CzXnjF1TQLSPwrdQijK4N9f5wLyZUUXXASf8) |
| add B | [`Fc7ZTrq8sR…`](https://testnet.suivision.xyz/txblock/Fc7ZTrq8sRb3L6vQwTZkNUGcpqDUnwtJT5NLfsgZ7wLV) |
| push epoch 0 (2 textes) | [`Rmqs3Ncpff…`](https://testnet.suivision.xyz/txblock/Rmqs3NcpffCZGiXjZyWzYGmq1b6k6wWU9vTqo6q2cgW) · [`SHLEbceaAk…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/SHLEbceaAkSapP5PHuCAu1lACv_515-tx4I0bu2VcwA), [`VuTomRfOIx…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/VuTomRfOIxIun5Vz9lHzrdoNHAIW0kTy1bfRlttx1Fg) |
| push epoch 0 (--from-memory) | [`8KRaEcFiTS…`](https://testnet.suivision.xyz/txblock/8KRaEcFiTSrvnFx3jKtYbSN2yM5jL5NX97kAAosRjXzJ) · [`qadkYkyR3q…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/qadkYkyR3qHTA4GIiFWR6Z4F16j6yZ7lJ_6xYoi7Ol4) |
| remove B (epoch 0 → 1) | [`Cq3jGaReYV…`](https://testnet.suivision.xyz/txblock/Cq3jGaReYVkEtgQqb9rc4cJDSPmf9BKb8HbugLy9KM2v) |
| push epoch 1 | [`AF5XPmLWJY…`](https://testnet.suivision.xyz/txblock/AF5XPmLWJYn1DYsrMPEHx2Va1Pbhp1BudPFoi4jVPJz5) · [`HLTyTiS4ay…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/HLTyTiS4ayzlF0w07woFPEHvGnFYUxGHkyogfAultJQ) |
| MemoryIndex A | [`0x21aea546…aa39`](https://testnet.suivision.xyz/object/0x21aea546453738e42ea33bc3dfbe151c0aad2f78353d26dfcb2814509cfbaa39) · flush [`5xscUChcaR…`](https://testnet.suivision.xyz/txblock/5xscUChcaRsEKDCZGkhfs6SGF6fDT7xxfdY7Xyez6nr6), [`7KFDmA1TW4…`](https://testnet.suivision.xyz/txblock/7KFDmA1TW4pcQ93D2MKp1RFHGVQ9aXUYK3GnE91qz93N) |

## Package Space

Publié par l'adresse propriétaire `0xa0ea…8c06` dans
[`Bwx3yVdYci…`](https://testnet.suivision.xyz/txblock/Bwx3yVdYci6XQXMXecpt3eYHCyvZr46maSQuyZfSbddM) : package
[`0xa40e74c6…00d1`](https://testnet.suivision.xyz/object/0xa40e74c6ae866f18e86e9dcdfc9d29af436597264991fc81b4a289519f3500d1), UpgradeCap
[`0x27a9d878…1b96`](https://testnet.suivision.xyz/object/0x27a9d878f827ca3693cb5fdc963ce7d87897ffc742de8b0183f8a6040f6f1b96). Sa table de
liens pointe vers le package allowlist `0xe10f…7f1b` version 1.
