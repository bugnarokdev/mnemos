# Rapport de tests

Trace des tests exécutés avant le tag `mnemos-native-v0.2.3`. Chaque écriture sur la chaîne est publique : les liens permettent de vérifier la transaction, l'objet MemoryIndex et le blob chiffré. Les blobs sont chiffrés ; rien ici ne permet de les déchiffrer. Le testnet peut être réinitialisé, les liens peuvent donc expirer.

| | |
| --- | --- |
| Date | 12 novembre 2026, de 00 h 20 à 00 h 40, heure de Paris (UTC+1) |
| Commit testé | [`82fba1327b29`](https://github.com/bugnarokdev/mnemos/commit/82fba1327b29ed6f1441bccea4e97c070818359c) (tag `mnemos-native-v0.2.3`) |
| Réseau | Sui testnet (identifiant de chaîne `69WiPg3DAQiwdxfncX6wYQ2siKwAe6L9BZthQea3JNMD`), Walrus testnet |
| Outils | Linux x86_64, rustc 1.99.1, cargo 1.99.1, Python 3.12.14, pytest 9.2.0, grpcio 1.85.1, protobuf 6.33.6, argon2-cffi 25.1.0, mnemos_core 0.2.3 (compilé depuis le commit testé) |

## Tests unitaires

| Suite | Résultat |
| --- | --- |
| `cargo test --release` (cœur natif) | 8 réussis : vecteurs de clés Argon2id et legacy, normalisation des adresses, conversion protobuf vers JSON, règles du repli de lecture (introuvable et échec de chaîne définitifs, mode `grpc`, fraîcheur invérifiable), valeurs par défaut |
| `pytest` avec le cœur Rust exigé (`MNEMOS_REQUIRE_RUST=1`) | 49 réussis |
| `pytest` avec `MNEMOS_BACKEND=python` | 48 réussis, 1 ignoré (la comparaison de hachage propre à Rust) |

Les suites pytest sont `test_chain` (6), `test_chain_guard` (7), `test_cross` (4), `test_history` (12), `test_kdf` (7) et `test_reads` (13). `test_reads` simule le nœud complet et GraphQL : nœud d'abord, repli à jour, GraphQL en retard refusé (19 107 checkpoints), limite configurable, GraphQL sur la mauvaise chaîne, fraîcheur invérifiable, mode `grpc` seul, introuvable définitif, échec de la garde de chaîne non relancé, mauvais type, mauvais package, identifiant d'objet différent, chaîne vérifiée une seule fois.

## De bout en bout sur le testnet

Scénario : deux faits sont écrits, chacun synchronisé en un instantané (envoi Walrus, puis `append` signé sur Sui) ; le stockage local est supprimé ; `restore` le reconstruit depuis Sui et Walrus ; `history --verify` vérifie chaque version (sha256 du blob, lien vers le hachage précédent, signature Ed25519) sans le secret. Package : [`0xc1222936…1f4a`](https://testnet.suivision.xyz/package/0xc1222936acba3d4b12e87341b43c689d932c04a026805ed1b3758f32a37f1f4a).

### Cœur Rust

MemoryIndex [`0x772bef4a…48bd`](https://testnet.suivision.xyz/object/0x772bef4afb843eec39dc880b3c0def75c7c363d3dc3942d8ac546931bf5d48bd), créé dans [`EsrNZ2H9n1…`](https://testnet.suivision.xyz/txblock/EsrNZ2H9n1LBDFNquubJRsjMTDxhGxockYcaShJyZgaQ).

| Version | Hachage du contenu | Blob Walrus | Transaction Sui |
| --- | --- | --- | --- |
| 1 | `fdb11b68cb3386a4` | [`k7xbplKaLg…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/k7xbplKaLg_6X9VHmiHx6IzMmY1HQ8q6IfxfytE3V-M) | [`45Zx4EPBN2…`](https://testnet.suivision.xyz/txblock/45Zx4EPBN2tgXwvzF9BuEyTNyKd7yn5tc2TzHS2yTbZX) |
| 2 | `577cd51205e7f5a1` | [`IWdN2tdMe_…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/IWdN2tdMe_iMto_WsNzvfu-s2ca3DIdgA38abPxpCXg) | [`hjYsfFs7BF…`](https://testnet.suivision.xyz/txblock/hjYsfFs7BFuBBLz2sFkwACWrECbHWSLa1dEX4DGdSvw) |

Résultat : stockage local supprimé ; restore a renvoyé 2 entrées, 2 instantanés, `kdf: argon2id` ; `history --verify` : `OK: 2 versions verified`. L'index a été lu sur le nœud complet.

### Backend Python

MemoryIndex [`0x5daf1aef…a0dc`](https://testnet.suivision.xyz/object/0x5daf1aefe8217a1505832792a627c3279d51e874996ebc68a46a2602eaeca0dc), créé dans [`3GMejNZzdS…`](https://testnet.suivision.xyz/txblock/3GMejNZzdS2Xzp2qwhNBJHy6wJjbZAm7XcQ2v2XYvTVN).

| Version | Hachage du contenu | Blob Walrus | Transaction Sui |
| --- | --- | --- | --- |
| 1 | `3699abdb7070d969` | [`z95OaPSMO6…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/z95OaPSMO6qNtCEuYPOvAS_an8cQIlI15eYO74aadlw) | [`DJxyqM4J9R…`](https://testnet.suivision.xyz/txblock/DJxyqM4J9RhGX6Cewn1yAwdriKjAKG59eSBLtcH9jjGh) |
| 2 | `22c4415235437e31` | [`YBc2QYHKJo…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/YBc2QYHKJoTBbZq2vRZNH3G5PMnKfP9ePjBvGxmAUao) | [`AUpNzs8b6S…`](https://testnet.suivision.xyz/txblock/AUpNzs8b6Sdc9BzEERnkgMwyCT3mGzsyQ9aXg1BJ1JEH) |

Résultat : index créé par ce passage, identifiants pris dans les effets de la transaction ; stockage local supprimé ; restore a renvoyé 2 entrées, 2 instantanés, `kdf: argon2id` ; `history --verify` : `OK: 2 versions verified`, lu sur le nœud complet.

### Entre backends

Chaque backend restaure puis prolonge l'index de l'autre, et l'autre backend vérifie le résultat. Les formats de texte chiffré, de hachage et de signature sont identiques, la chaîne reste donc valide.

- Rust restaure l'index Python [`0x5daf1aef…a0dc`](https://testnet.suivision.xyz/object/0x5daf1aefe8217a1505832792a627c3279d51e874996ebc68a46a2602eaeca0dc) (2 entrées) et ajoute la version 3: [`tK-fUg1r2w…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/tK-fUg1r2w2jIAkId_vNmYZN6Q-hDDxcyyFudzLnfeQ), [`GwG6gTeXwk…`](https://testnet.suivision.xyz/txblock/GwG6gTeXwkDaee58LjNFdJyY7ZPxsKDSWwWWXUFpJ4gL).
- Python restaure l'index Rust [`0x772bef4a…48bd`](https://testnet.suivision.xyz/object/0x772bef4afb843eec39dc880b3c0def75c7c363d3dc3942d8ac546931bf5d48bd) (2 entrées) et ajoute la version 3: [`46DO0SCDR0…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/46DO0SCDR0uAz0VWBzYihPr9MgJmUzeNdfEATmtbsX0), [`GC8hRBVa6q…`](https://testnet.suivision.xyz/txblock/GC8hRBVa6qcyRbPxjfj6n6a5ZcRWzCHdqCpSfTmqYNkj).

Après chaque écriture croisée, le premier backend restaure 3 entrées et `history --verify` indique `OK: 3 versions verified`.

## Garde de chaîne

Chaque backend a été pointé vers un `sui_rpc` hors testnet ou injoignable, avec une demande d'`init` puis de `flush`. Chaque tentative a été refusée avant tout envoi ou signature.

| `sui_rpc` | Backend | `init` et `flush` |
| --- | --- | --- |
| mainnet `https://fullnode.mainnet.sui.io:443` | Python, Rust | `refusing to sign: ... is on chain mainnet (4btiuiMPvEENsttpZC7CZ53DruC3MAgfznDbASZ7DR6S), Mnemos only writes to Sui testnet` |
| devnet `https://fullnode.devnet.sui.io:443` | Python, Rust | `refusing to sign: ... is on chain unknown (9ovasb4imJNPrhAjLcXjanDMwcybDczZR8W5jnWxpVhK), Mnemos only writes to Sui testnet` |
| injoignable `https://127.0.0.1:9` | Python, Rust | `refusing to sign: could not read the chain id of https://127.0.0.1:9` (Python: `_InactiveRpcError`, Rust: `tcp connect error`) |

## Panne GraphQL et repli de lecture

Pendant cette session, le GraphQL public du testnet a cessé d'avancer au checkpoint 405395971 (depuis le 11 novembre, 22 h 24 heure de Paris) alors que le nœud complet continuait. Les requêtes par propriétaire et de solde sur GraphQL renvoyaient `Request is outside consistent range`. C'est la raison pour laquelle la 0.2.3 lit sur le nœud complet ; voir [lectures et disponibilité](architecture.md#lectures-et-disponibilité).

Constaté avec le commit testé :

- Toutes les lectures de bout en bout ci-dessus (index, objets possédés, solde, récupération du package) ont été servies par le nœud complet pendant que GraphQL était en retard ; le `doctor` des deux backends indiquait l'index `readable via grpc`.
- Le contrôle de fraîcheur a refusé GraphQL : `GraphQL is 34416 checkpoints behind the fullnode (limit 100); refusing a stale read` (00 h 39). `doctor` le signale comme un contrôle `graphql_fresh` en échec, en précisant que les lectures passent par le nœud complet.
- Avec un `sui_rpc` injoignable, la lecture d'un MemoryIndex a été refusée en mode `auto` comme en mode `grpc` (`refusing to sign: could not read the chain id of https://127.0.0.1:9`). GraphQL n'a pas été utilisé seul.

## Release

Les wheels de [`mnemos-native-v0.2.3`](https://github.com/bugnarokdev/mnemos/releases/tag/mnemos-native-v0.2.3) ont été construites par le workflow GitHub Actions public à partir du commit testé. Chacune correspond au `SHA256SUMS` de la release et à la table épinglée dans `mnemos/native_wheel.py`, et `gh attestation verify --repo bugnarokdev/mnemos` confirme qu'elle provient de `refs/tags/mnemos-native-v0.2.3` au commit `82fba1327b29`.

| Wheel | sha256 |
| --- | --- |
| `macosx_10_12_x86_64` | `2b0ec16f5d02df7759fdb080cc8c826f4bfc88d0f5c7410daf6ee3375e0b26fe` |
| `macosx_11_0_arm64` | `ff28f82f1b010ec459e3720591ebfc8f9a85dffa84d75aa882ae0fb0f9cb9038` |
| `manylinux_2_34_aarch64` | `3bd1b5245781f31cb3391712bc54ee1ea19ad3032ce7a701391d16d85ab07c78` |
| `manylinux_2_34_x86_64` | `d7bd9ea2f9cd55bbde33822b4b39670bcfa40ad929232bae6d6455b24844ea01` |
| `win_amd64` | `97f1190fb50d76b13ed19dd2beae89c96b6607a549ebf37db921bd0ebd7207e7` |

## Reproduire

Lancez `pytest`, `cargo test --release` dans `native/`, puis `hermes mnemos init`, quelques `remember`, `hermes mnemos flush`, `hermes mnemos restore` sur un profil neuf et `hermes mnemos history --verify`. Pour la partie entre backends, refaites les mêmes étapes une fois avec `MNEMOS_BACKEND=python` et une fois avec le cœur Rust, avec le même secret.
