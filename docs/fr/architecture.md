# Architecture

```text
+---------------- Processus Hermes --------------+
|  MemoryProvider (mnemos/provider.py)           |
|    prefetch / queue_prefetch  --> SQLite seul  |
|    sync_turn / on_memory_write --> file        |
|    on_pre_compress (API v2) --> flush, ou erreur|
|    outils : remember, recall, status, flush    |
+------------------------+-----------------------+
                         |
                         v
              +--------------------+
              | mnemos_core (Rust) |  préféré
              | ou repli Python    |
              +---------+----------+
                        |
        +---------------+----------------+
        |               |                |
        v               v                v
   cache SQLite    Walrus PUT/GET    gRPC Sui
   $HERMES_HOME/   texte chiffré     (repli GraphQL)
   mnemos/         identifiant blob  MemoryIndex
```

## Pièces

- `plugin.yaml`, `__init__.py`, `cli.py`, `config_schema.py` sont les surfaces Hermes.
- `mnemos/provider.py` implémente `MemoryProvider`. Chaque `register()` construit une nouvelle instance. Les chemins viennent de l'argument `hermes_home`, pas d'un répertoire figé.
- `mnemos/engine.py` utilise le module natif quand `import mnemos_core` réussit et que le module annonce `core_version()` 0.2.3 ou plus (clés Argon2id et lectures sur le nœud complet) ; un module plus ancien est ignoré.
- `mnemos/py_engine.py` est le repli : mêmes octets d'instantané, HTTP Walrus, et gRPC Sui (simulation puis exécution). Les points de contrôle sont validés dans ce mode.
- `mnemos/chain.py` contient les octets d'instantané et la dérivation des clés (Argon2id, puis HKDF par usage), ainsi que l'ancienne dérivation en lecture seule. `mnemos/sui_client.py` vérifie l'identifiant de chaîne du RPC avant chaque signature. `mnemos/reads.py` lit l'état de la chaîne sur le nœud complet, avec un repli GraphQL encadré (voir plus bas).
- `mnemos/history.py` lit la liste des instantanés du MemoryIndex et vérifie chaque version (sha256 du blob, lien vers le hash précédent, signature Ed25519) pour `hermes mnemos-sui history --verify`.
- `native/` est la crate PyO3. Elle possède SQLite, XChaCha20-Poly1305, Ed25519, le HTTP Walrus, la preuve de travail du faucet et les transactions Sui (`sui-sdk-types`, `sui-crypto`, `sui-transaction-builder`, `sui-rpc`), avec la même dérivation Argon2id (crate `argon2`) et le même contrôle de chaîne testnet.
- `move/MemoryIndex` est l'index sur la chaîne.

## Schéma local

`$HERMES_HOME/mnemos/mnemos.sqlite` contient `entries` (texte en clair, cette machine seulement), `snapshots` (texte chiffré, identifiant de blob, hash, signature, bit de synchro) et `meta`.

## Chemin d'un tour

`prefetch` renvoie une chaîne déjà calculée pour cette requête, ou la calcule depuis SQLite en cas d'absence de cache. Dans les deux cas, cela reste sur cette machine. `queue_prefetch` remplit le cache sur un fil de contexte Hermes après le tour.

`sync_turn` insère une copie bornée du tour dans `entries` et démarre une synchronisation de fond. Le tour n'attend ni Walrus ni Sui. `on_session_end` attend au plus 20 secondes. Le backend Python et l'extension Rust valident tous les deux cette synchronisation.

`on_pre_compress` est synchrone. Il renvoie `checkpoint: <identifiant de blob>` seulement après le stockage du blob et l'acceptation de l'ajout à l'index. Un échec lève une erreur. Hermes, quand `compression.checkpoint_required` est vrai, conserve la transcription non compactée.

Les contextes sous-agent et cron sautent les écritures automatiques. Ils passent quand même par `on_pre_compress`, parce que c'est le contrat en échec fermé.

## Un instantané par synchronisation

Les entrées en attente sont canonisées en JSON, scellées, envoyées, hachées, signées et ajoutées comme un seul instantané. Une nouvelle tentative réutilise le même texte chiffré et le même identifiant de blob si l'envoi précédent a réussi mais pas la mise à jour de la chaîne.

## Lectures et disponibilité

### Pourquoi

Chaque opération Mnemos qui touche Sui lit d'abord l'état de la chaîne : `flush` lit la tête de l'index et le solde de gas, `restore` et `history` lisent le MemoryIndex, `init` lit les objets créés, et un profil restauré retrouve `package_id` à partir du type de l'index. Jusqu'à 0.2.2, toutes ces lectures passaient par l'indexeur GraphQL de Sui, alors que les écritures allaient au nœud complet. L'indexeur était donc un second point de défaillance unique, et discret : un indexeur qui n'avance plus continue de répondre, mais avec des données anciennes.

C'est arrivé le 11 novembre 2026. À partir de 22 h 24 (heure de Paris), le GraphQL public du testnet est resté bloqué au checkpoint 405395971 pendant que le nœud complet continuait d'avancer ; deux heures plus tard il avait plus de 33 000 checkpoints de retard, et les requêtes par propriétaire et de solde échouaient avec `Request is outside consistent range`. Le nœud complet fonctionnait normalement, mais toutes les lectures de Mnemos échouaient : impossible d'écrire ou de restaurer.

### Comment

Les lectures vont désormais au même nœud complet gRPC qui exécute les transactions (`sui_rpc`) : `LedgerService.GetObject` pour le MemoryIndex et la récupération du package, `StateService.ListOwnedObjects` pour les objets possédés, `StateService.GetBalance` pour le gas. Le nœud complet est la source que l'indexeur recopie, il ne peut donc pas être en retard sur lui, et une écriture en dépend déjà. Avant la première lecture, le nœud doit annoncer l'identifiant de chaîne de Sui testnet, la même vérification que celle qui protège la signature (mise en cache cinq minutes par URL).

GraphQL n'est plus qu'un repli, et seulement s'il est prouvé à jour :

1. Lecture sur le nœud complet. Un objet trouvé est utilisé tel quel. Un objet que le nœud déclare introuvable l'est définitivement ; GraphQL n'est jamais consulté pour le contredire. Un échec de vérification de chaîne est définitif aussi.
2. Si le nœud a passé la vérification de chaîne mais qu'une lecture échoue (délai dépassé, erreur de transport ou du serveur) et que `read_source` vaut `auto`, Mnemos vérifie GraphQL avant de lui faire confiance : il lit la hauteur de checkpoint du nœud et le dernier checkpoint de GraphQL, GraphQL doit annoncer l'identifiant de chaîne de Sui testnet, et l'écart doit être d'au plus `graphql_max_lag` checkpoints (100 par défaut, environ une minute sur le testnet).
3. Si l'une de ces conditions échoue, la lecture est refusée avec les deux raisons, par exemple `GraphQL is 33313 checkpoints behind the fullnode (limit 100); refusing a stale read`. Mnemos n'agit jamais sur une donnée qu'il ne peut pas dater. Un nœud complet totalement injoignable échoue à la vérification de chaîne, et la lecture est refusée à ce stade : le contrôle de fraîcheur a de toute façon besoin de la hauteur de checkpoint du nœud, et GraphQL seul n'est jamais cru.

Quelle que soit la source, le résultat est vérifié avant usage : l'identifiant renvoyé doit être celui demandé, le type doit être `<package>::memory_index::MemoryIndex` et, pour une écriture, le package doit correspondre à `package_id`. `init` prend le package et l'identifiant du nouveau MemoryIndex dans les effets de la transaction au lieu de chercher parmi les objets du propriétaire, donc une adresse qui possède plusieurs index ne peut pas choisir le mauvais.

Les deux backends suivent le même ordre et les mêmes vérifications. `hermes mnemos-sui doctor` indique la source de la vérification d'index (`readable via grpc`) et un contrôle séparé `graphql_fresh` avec l'écart courant. Les blobs Walrus sont toujours lus depuis l'agrégateur, et leur sha256 est comparé à l'index signé.

Réglages : `read_source` (`auto` par défaut ; `grpc` désactive le repli GraphQL) et `graphql_max_lag` (`100` par défaut). Voir [configuration](configuration.md#fichier).
