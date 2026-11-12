# Espaces partagés avec Seal : BETA (testnet preview)

> **BETA (testnet preview).** Ce n'est pas un logiciel de production. Cette voie n'existe que sur la branche `preview/seal`. Elle tourne
> sur Sui testnet, Walrus testnet et les serveurs de clés Seal testnet de Mysten. Elle ne
> fait partie d'aucune release, les formats peuvent changer et le testnet peut être
> remis à zéro. Ne partagez rien que vous ne pouvez pas perdre ou voir fuiter.

La mémoire personnelle reste exactement la même : `MNEMOS_SECRET`, Argon2id, blobs
XChaCha20-Poly1305, votre `MemoryIndex`, Walrus. La voie de partage est un chemin séparé,
compilé seulement avec la feature cargo `seal`. Elle n'écrit jamais dans le MemoryIndex,
le stockage local ni `config.json`.

## Ce qu'elle fait

Un **espace partagé** permet à plusieurs profils Hermes de lire les mêmes entrées de
mémoire, sans mot de passe commun :

1. A crée un espace. Une seule transaction crée une **allowlist** Seal (A détient son
   `Cap` et en est le premier membre) et un objet **Space** qui lui est lié.
2. A ajoute l'adresse Sui de B à l'allowlist.
3. A (ou tout membre) publie des entrées. Chaque entrée est chiffrée avec
   [Seal](https://github.com/MystenLabs/seal) via
   [`sui-seal-client`](https://github.com/bugnarokdev/sui-seal-client), stockée sur
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
| Serveurs de clés Seal | `mysten-testnet-1`, `mysten-testnet-2`, seuil 2 sur 2 |

`move/SharedSpace` dépend de l'allowlist par commit git (`sui-seal-client` `f4ffa2f`), et
le package publié est lié à `0xe10f…7f1b` version 1.

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

## Commandes

Compiler le cœur natif avec la feature :

```sh
MNEMOS_FEATURES=seal scripts/build_core.sh
```

Les commandes sont sous `hermes mnemos-sui share`. Le même code tourne hors de Hermes avec
`python -m mnemos.share --home <HERMES_HOME> …`. Chaque commande affiche du JSON.

| Commande | Qui | Effet |
| --- | --- | --- |
| `share whoami` | tous | Affiche l'adresse du signataire |
| `share create [--name N]` | créateur | Allowlist + Space, le créateur est membre et détient le Cap |
| `share join --space <id> [--name N]` | membre | Enregistre localement l'espace de quelqu'un d'autre |
| `share add <adresse>` | créateur | Ajoute un membre (epoch inchangé) |
| `share remove <adresse>` | créateur | Retire un membre, epoch + 1 |
| `share push --text T [--text T…]` | membre | Chiffre, envoie, publie |
| `share push --from-memory REQUÊTE [--limit N]` | membre | Partage les entrées personnelles qui correspondent (lecture seule) |
| `share pull` | membre | Obtient les clés, déchiffre, met en cache ; code de sortie 3 si une entrée est refusée |
| `share status` | tous | Epoch, nombre d'entrées par epoch |

**Signataire.** `MNEMOS_SHARE_KEY_FILE` (ou `--key-file`) désigne un fichier de clé Sui
(suiprivkey, base64 de keystore ou seed hex de 32 octets). Le fichier est lu, transmis au
cœur natif et jamais affiché. Sans lui, le signataire est l'adresse Sui Mnemos du profil
(dérivée de `MNEMOS_SECRET`). Les clés de session et les clés obtenues restent en mémoire.

**Gas.** `create`, `add`, `remove` et `push` sont des transactions Sui payées par le
signataire. `pull` et `status` ne coûtent rien.

**Sécurité.** Chaque écriture vérifie d'abord que le RPC est bien Sui testnet, comme le
chemin personnel. Une transaction qui échoue on-chain est remontée comme une erreur.

## Limites de cette preview

- **Rust uniquement.** Le backend Python n'a pas d'implémentation de Seal ; avec
  `MNEMOS_BACKEND=python` ou un cœur compilé sans `seal`, `share` refuse avec un message
  clair. La mémoire personnelle marche avec les deux backends, comme avant.
- Les entrées sont des éléments de texte, pas des snapshots complets, et ne sont pas
  fusionnées dans la mémoire personnelle : elles vivent dans l'espace et dans le cache
  de l'espace.
- Le Space garde les références dans un vecteur à l'intérieur d'un seul objet (limite de
  taille d'objet Sui, de l'ordre de quelques milliers d'entrées).
- Seul le créateur détient le Cap ; il n'y a pas de commande de transfert d'admin.
- Les serveurs de clés testnet ouverts de Mysten sont gratuits, limités en débit et sans
  garantie de disponibilité. Les serveurs en comité (agrégateur) ne sont pas encore pris
  en charge.
- Les crates `seal-sdk` et `crypto` sont des dépendances git figées par commit : cette
  feature ne peut pas encore être publiée sur crates.io.

## Feuille de route (indicative)

Cette feuille de route est seulement indicative : aucune date, aucun engagement, et
l'ordre peut changer.

1. **v0.1 beta (cette branche) :** partage coopératif par allowlist sur testnet.
2. **Serveurs de clés :** serveurs en comité (agrégateur), choix des serveurs et du seuil,
   avec bascule si l'un tombe.
3. **D'autres règles d'accès :** abonnement, accès à durée limitée.
4. **Bindings Python** pour sui-seal-client, pour que le backend Python puisse aussi
   partager.
5. **Publication sur crates.io** quand Mysten publiera `seal-sdk` et `crypto`.
6. **Revue de sécurité** de la voie et des packages Move.
7. **Puis mainnet**, seulement après la revue.

## Tests

- `cargo test --release --features seal` : les 8 tests existants et 4 pour cette voie
  (format de l'id de clé, validation de la config, lecture du Space, format des entrées).
- `move/SharedSpace` : 7 tests Move (`sui move test`).
- `tests/test_share.py` : 9 tests hors ligne (refus sur le backend Python, isolation de
  la config, cache, branchement CLI, dérivation du signataire, fichier de clé jamais
  affiché).
- `scripts/e2e_coop.sh` : le scénario testnet ci-dessus, avec le MemoryIndex personnel de
  A créé, restauré et vérifié autour. Voir le
  [rapport de tests de la preview](test-report-preview-seal.md).
