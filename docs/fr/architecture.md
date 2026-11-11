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
   cache SQLite    Walrus PUT/GET    écriture gRPC Sui
   $HERMES_HOME/   texte chiffré     lecture GraphQL
   mnemos/         identifiant blob  MemoryIndex
```

## Pièces

- `plugin.yaml`, `__init__.py`, `cli.py`, `config_schema.py` sont les surfaces Hermes.
- `mnemos/provider.py` implémente `MemoryProvider`. Chaque `register()` construit une nouvelle instance. Les chemins viennent de l'argument `hermes_home`, pas d'un répertoire figé.
- `mnemos/engine.py` utilise le module natif quand `import mnemos_core` réussit.
- `mnemos/py_engine.py` est le repli : mêmes octets d'instantané, HTTP Walrus, et gRPC Sui (simulation puis exécution). Les points de contrôle sont validés dans ce mode.
- `mnemos/history.py` lit la liste des instantanés du MemoryIndex et vérifie chaque version (sha256 du blob, lien vers le hash précédent, signature Ed25519) pour `hermes mnemos history --verify`.
- `native/` est la crate PyO3. Elle possède SQLite, XChaCha20-Poly1305, Ed25519, le HTTP Walrus, la preuve de travail du faucet et les transactions Sui (`sui-sdk-types`, `sui-crypto`, `sui-transaction-builder`, `sui-rpc`).
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
