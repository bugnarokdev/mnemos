# Espaces partagés avec Seal : BETA (testnet preview)

> **BETA (testnet preview).** Ce n'est pas un logiciel de production. Cette voie n'existe que sur la branche `preview/seal`. Elle tourne
> sur Sui testnet, Walrus testnet et les serveurs de clés Seal testnet de Mysten. Elle ne
> fait partie d'aucune release, les formats peuvent changer et le testnet peut être
> remis à zéro. Ne partagez rien que vous ne pouvez pas perdre ou voir fuiter.

La mémoire personnelle reste exactement la même : `MNEMOS_SECRET`, Argon2id, blobs
XChaCha20-Poly1305, votre `MemoryIndex`, Walrus. La voie de partage est un chemin séparé
avec deux implémentations : le cœur natif, compilé seulement avec la feature cargo
`seal`, et une implémentation Python sur les bindings Python de sui-seal-client v0.2.0
(toutes deux BETA, voir [Deux implémentations](#deux-implémentations-beta)). Elle n'écrit
jamais dans le MemoryIndex, le stockage local ni `config.json`.

## Ce qu'elle fait

Un **espace partagé** permet à plusieurs profils Hermes de lire les mêmes entrées de
mémoire, sans mot de passe commun :

1. A crée un espace. Une seule transaction crée une **allowlist** Seal (A détient son
   `Cap` et en est le premier membre) et un objet **Space** qui lui est lié.
2. A ajoute l'adresse Sui de B à l'allowlist.
3. A (ou tout membre) publie des entrées. Chaque entrée est chiffrée avec
   [Seal](https://github.com/MystenLabs/seal) via
   [`sui-seal-client`](https://github.com/bugnarokdev/sui-seal-client) v0.2.0 (BETA), stockée sur
   Walrus, et référencée dans le Space (id du blob, id de clé, epoch, auteur, sha256 du
   blob).
4. B récupère : les serveurs de clés Seal vérifient `allowlist::seal_approve` on-chain et
   ne donnent les clés qu'aux membres. C, qui n'est pas membre, est refusé.
5. A retire B. L'epoch de l'allowlist augmente de un, et toute nouvelle entrée est
   chiffrée sous le nouvel epoch.

## Les éléments on-chain

| Élément | Id testnet |
| --- | --- |
| Package allowlist `seal_allowlist::allowlist` (de sui-seal-client, propriétaire `0xa0ea…8c06`) | `0xe10fbae08333748f310adc926904980c6ced589a75a774bff4dfc462567d7f1b` |
| Package Space `mnemos_share::space` (`move/SharedSpace`) | `0xa40e74c6ae866f18e86e9dcdfc9d29af436597264991fc81b4a289519f3500d1` |
| UpgradeCap du package Space (détenu par l'adresse propriétaire `0xa0ea…8c06`) | `0x27a9d878f827ca3693cb5fdc963ce7d87897ffc742de8b0183f8a6040f6f1b96` |
| Transaction de publication du package Space | `Bwx3yVdYci6XQXMXecpt3eYHCyvZr46maSQuyZfSbddM` |
| Serveurs de clés Seal (par défaut) | `mysten-testnet-1`, `mysten-testnet-2`, seuil 2 sur 2 |
| Serveurs de clés Seal (`--committee`, BETA) | Comité testnet de Mysten `0xb012378c9f3799fb5b1a7083da74a4069e3c3f1c93de0b27212a5799ce1e1e98`, 3 sur 5 derrière `https://seal-aggregator-testnet.mystenlabs.com` |

Le cœur natif fige `sui-seal-client` par commit sur le tag `v0.2.0`
(`da579f64223ff3f7a83897272937cfc4d2d55153`), et `scripts/build_seal_py.sh` compile les
bindings Python depuis ce même commit. `move/SharedSpace` dépend toujours de
l'allowlist à `f4ffa2f`, le commit à partir duquel le package Space a été compilé et
publié ; les sources Move de l'allowlist sont identiques octet pour octet dans `v0.2.0`.
Le package publié est lié à `0xe10f…7f1b` version 1.

Le Space n'accepte une référence que si :

- l'expéditeur est un **membre actuel** de l'allowlist (sinon `ENotMember`) ;
- l'id de clé commence par l'id de l'allowlist (`EBadKeyId`) et porte l'**epoch
  actuel** (`EStaleEpoch`) : un epoch ancien ou futur est refusé ;
- le sha256 fait 32 octets (`EBadDigest`).

Format de l'id de clé : `[id de l'allowlist : 32 octets][epoch : u64 little-endian][nonce : 16 octets aléatoires]`.

## La révocation : ce que le retrait fait et ne fait pas

Une clé Seal ne se reprend pas. La clé d'un id ouvre tout ce qui a été chiffré pour cet
id, pour toujours. Donc :

- **Après son retrait, B ne peut plus obtenir aucune clé**, ni pour les anciennes
  entrées ni pour les nouvelles.
- **Les nouvelles entrées sont hors de portée de B** : elles utilisent le nouvel epoch,
  pour lequel B n'a jamais eu de clé. Le Space refuse toute entrée sous un ancien epoch.
- **Ce que B a déjà lu, B le garde.** La CLI met en cache ce qu'un membre a déchiffré
  dans `<HERMES_HOME>/mnemos/shared/<space>.json`, et les clés obtenues par B avant son
  retrait déchiffrent toujours ces anciennes entrées. Le test de bout en bout coop le
  vérifie explicitement.
- Les membres restants lisent toujours les anciennes et les nouvelles entrées.

Le retrait protège l'avenir, pas le passé. Une entrée qui doit rester secrète pour un
membre retiré n'aurait pas dû lui être partagée.

## Serveurs de clés (BETA)

Chaque espace choisit les serveurs de clés vers lesquels ses entrées sont chiffrées. Le
choix se fait au `create` (ou au `join`) et est enregistré avec l'espace dans
`<HERMES_HOME>/mnemos/share.json` (`spaces.<nom>.seal`) ; `push` l'utilise sans option.

| Option | Serveurs de clés | Seuil |
| --- | --- | --- |
| aucune (par défaut) | `mysten-testnet-1`, `mysten-testnet-2` | 2 sur 2, comme dans la première preview |
| `--committee` | Comité testnet de Mysten, joint via l'agrégateur de Mysten | 1 (le comité lui-même est en 3 sur 5) |
| `--threshold N` | les serveurs par défaut | N |
| `--key-server ID [--key-server ID…] [--threshold N]` | les objets serveurs de clés listés | N (2 par défaut) |

Les lecteurs n'ont besoin d'aucune option : `pull` lit les serveurs de clés et le seuil
dans l'en-tête chiffré de chaque entrée, puis interroge ces serveurs. Il ne contacte que
des serveurs de confiance : les serveurs et le comité testnet de Mysten, plus ceux listés
dans les réglages du profil. Une entrée qui nomme un autre serveur, ou un jeu de serveurs
pondérés, est marquée `error` sans aucune requête.

Les espaces créés avant ce changement n'ont pas de réglage `seal` et gardent le 2 sur 2
par défaut ; leurs entrées se déchiffrent toujours (le test coop de bout en bout relit un
espace écrit par la version précédente du client).

Les réglages pour tout le profil vont dans `share.json` sous `config` :
`key_server_profile` (`testnet` ou `committee`), `key_servers`, `threshold`, et
`aggregators` (`{"<id du serveur comité>": "https://…"}`) pour d'autres comités que celui
de Mysten. Les réglages sont validés avant toute transaction ; un seuil impossible est
refusé avec `share settings: …`.

**Mauvais propriétaire.** Si les serveurs de clés répondent que l'approbation a utilisé
un objet appartenant à une autre adresse (`Error::is_wrong_owner` dans sui-seal-client, `WrongOwnerError` dans ses bindings
Python),
`pull` marque les entrées `wrong_owner`, ne réessaie pas et sort avec le code 4. Les
espaces à allowlist n'utilisent que l'objet allowlist partagé, donc ce cas ne devrait pas
arriver ; il signalerait un mauvais id d'espace ou d'allowlist.

## Deux implémentations (BETA)

La voie existe deux fois, avec les mêmes commandes, les mêmes appels on-chain, le même
`share.json` et le même format d'entrée chiffrée :

| Voie | Construite depuis | Choisie quand |
| --- | --- | --- |
| native (Rust) | `native/src/share.rs`, feature cargo `seal` | par défaut si le cœur a la feature `seal` ; forcée avec `MNEMOS_BACKEND=rust` |
| Python | `mnemos/py_share.py` sur les bindings Python de sui-seal-client v0.2.0 | `MNEMOS_BACKEND=python`, ou automatiquement si le cœur n'a pas `seal` |

Les membres d'un même espace peuvent utiliser des voies différentes : une entrée écrite
par l'une est lue par l'autre, et un espace créé par l'une s'administre avec l'autre (le
test de bout en bout coop vérifie chaque sens). Les deux voies affichent le même JSON
pour le même état.

Compiler l'une ou les deux :

```sh
MNEMOS_FEATURES=seal scripts/build_core.sh   # voie native
scripts/build_seal_py.sh                       # voie Python (Rust et maturin requis)
```

`scripts/build_seal_py.sh` clone sui-seal-client, se place sur le commit figé de `v0.2.0`
`da579f64223ff3f7a83897272937cfc4d2d55153` (et le vérifie), compile `python/` avec
`maturin build --release --locked` et installe le wheel dans `MNEMOS_PYTHON`. Sans les
bindings, `MNEMOS_BACKEND=python` fait refuser `share` avec un message qui nomme le
script, avant de lire la moindre clé ou de toucher au profil.

## Commandes

Les commandes sont sous `hermes mnemos-sui share`. Le même code tourne hors de Hermes avec
`python -m mnemos.share --home <HERMES_HOME> …`. Chaque commande affiche du JSON.

| Commande | Qui | Effet |
| --- | --- | --- |
| `share whoami` | tous | Affiche l'adresse du signataire |
| `share create [--name N] [--committee \| --key-server ID… ] [--threshold N]` | créateur | Allowlist + Space, le créateur est membre et détient le Cap ; serveurs de clés comme ci-dessus |
| `share join --space <id> [--name N] [options serveurs]` | membre | Enregistre localement l'espace de quelqu'un d'autre (options utiles seulement pour publier) |
| `share add <adresse>` | créateur | Ajoute un membre (epoch inchangé) |
| `share remove <adresse>` | créateur | Retire un membre, epoch + 1 |
| `share push --text T [--text T…]` | membre | Chiffre, envoie, publie |
| `share push --from-memory REQUÊTE [--limit N]` | membre | Partage les entrées personnelles qui correspondent (lecture seule) |
| `share pull` | membre | Obtient les clés, déchiffre, met en cache ; code de sortie 3 si une entrée est refusée, 4 en cas de `wrong_owner` |
| `share status` | tous | Epoch, nombre d'entrées par epoch |

**Signataire.** `MNEMOS_SHARE_KEY_FILE` (ou `--key-file`) désigne un fichier de clé Sui
(suiprivkey, base64 de keystore ou seed hex de 32 octets). Le fichier est lu, transmis à
la voie de partage et jamais affiché. Sans lui, le signataire est l'adresse Sui Mnemos du profil
(dérivée de `MNEMOS_SECRET`). Les clés de session et les clés obtenues restent en mémoire.

**Gas.** `create`, `add`, `remove` et `push` sont des transactions Sui payées par le
signataire. `pull` et `status` ne coûtent rien.

**Sécurité.** Chaque écriture vérifie d'abord que le RPC est bien Sui testnet, comme le
chemin personnel. Une transaction qui échoue on-chain est remontée comme une erreur.

## Limites de cette preview

- **Deux voies, un format.** La voie Python a besoin des bindings compilés par
  `scripts/build_seal_py.sh` (toolchain Rust et maturin) ; ils ne sont pas sur PyPI. Sans
  les bindings et sans cœur `seal`, `share` refuse avec un message clair. La mémoire
  personnelle marche avec les deux backends, comme avant.
- Les entrées sont des éléments de texte, pas des snapshots complets, et ne sont pas
  fusionnées dans la mémoire personnelle : elles vivent dans l'espace et dans le cache
  de l'espace.
- Le Space garde les références dans un vecteur à l'intérieur d'un seul objet (limite de
  taille d'objet Sui, de l'ordre de quelques milliers d'entrées).
- Seul le créateur détient le Cap ; il n'y a pas de commande de transfert d'admin.
- Les serveurs de clés testnet ouverts et le comité de Mysten sont gratuits, limités en
  débit et sans garantie de disponibilité. Il n'y a pas de bascule automatique entre
  jeux de serveurs : un espace garde les serveurs choisis à sa création ; un seuil plus
  bas (par exemple 1 sur 2) tolère un serveur en panne, au prix de faire confiance à un
  seul serveur.
- Les serveurs pondérés et les clés d'API (serveurs à permission), disponibles dans
  sui-seal-client v0.2.0, ne sont pas exposés ici.
- Les crates `seal-sdk` et `crypto` sont des dépendances git figées par commit : cette
  feature ne peut pas encore être publiée sur crates.io.

## Feuille de route (indicative)

Cette feuille de route est seulement indicative : aucune date, aucun engagement, et
l'ordre peut changer.

1. **v0.1 beta :** partage coopératif par allowlist sur testnet.
2. **Serveurs de clés (cette branche, BETA, sur sui-seal-client v0.2.0) :** comité
   testnet de Mysten via l'agrégateur, choix des serveurs et du seuil, lecture guidée par
   l'en-tête. Ensuite : bascule entre jeux de serveurs, serveurs à permission.
3. **D'autres règles d'accès :** l'abonnement et l'accès à durée limitée existent dans
   sui-seal-client v0.2.0 ; les brancher sur les espaces vient ensuite.
4. **Partage depuis le backend Python (cette branche, BETA) :** fait, via les bindings
   Python de sui-seal-client v0.2.0, interopérable avec la voie native. Ensuite : des
   bindings installables (wheels) au lieu d'une compilation locale.
5. **Publication sur crates.io** quand Mysten publiera `seal-sdk` et `crypto`.
6. **Revue de sécurité** de la voie et des packages Move.
7. **Puis mainnet**, seulement après la revue.

## Tests

- `cargo test --release --features seal` : les 8 tests existants et 8 pour la voie native
  (format de l'id de clé, validation de la config, profils de serveurs, réglages
  refusés, jeux de serveurs des en-têtes, classement des erreurs dont le mauvais
  propriétaire, lecture du Space, format des entrées).
- `move/SharedSpace` : 7 tests Move (`sui move test`).
- `tests/test_share.py` : 57 vérifications hors ligne (choix de la voie, refus sans les
  bindings, isolation de la config, cache, branchement CLI, options de serveurs et
  réglages par espace ; sur chaque voie : réglages refusés avant toute transaction,
  dérivation du signataire, fichier de clé jamais affiché ; parité : les deux voies
  acceptent et refusent les mêmes 34 réglages avec le même message et dérivent les
  mêmes signataires ; la voie Python face aux vecteurs unitaires natifs, les octets
  exacts d'une entrée, et un chiffrement/déchiffrement hors ligne avec des serveurs de
  clés en mémoire).
- `scripts/e2e_coop.sh` : le scénario testnet ci-dessus, un espace sur le comité, un
  espace en 1 sur 2, un espace créé par la voie Python avec des entrées qui passent
  d'une voie à l'autre dans les deux sens (retrait et epoch par la voie Python, entrées
  Python dans les espaces comité et 1 sur 2), une relecture de l'espace d'un run
  précédent sur les deux voies, avec le MemoryIndex
  personnel de A créé, restauré et vérifié autour. Voir le
  [rapport de tests de la preview](test-report-preview-seal.md).
