# Dépannage

## Le diagnostic indique `backend: python`

C'est un mode qui fonctionne. `reason` dit pourquoi le module natif n'est pas utilisé (téléchargement natif optionnel et désactivé par défaut, pas de wheel, ou `MNEMOS_BACKEND=python`). Les écritures vont quand même vers Sui et Walrus. Pour préférer Rust, mettez `MNEMOS_NATIVE_AUTO=1` (release officielle seulement, sha256 épinglé) ou lancez `scripts/build_core.sh` avec `MNEMOS_PYTHON` égal à l'interpréteur d'Hermes.

## `hermes` ne charge pas ses dépendances avec un nouveau `HERMES_HOME`

L'état des dépendances est dans le répertoire Hermes qui a installé le checkout (`<home>/installs/...`). Un répertoire tout neuf ne le voit pas. Utilisez un profil sous ce répertoire (`<home>/profiles/<nom>`), comme le fait `scripts/demo.sh`.

## Échec du faucet

L'erreur contient l'adresse Sui. Approvisionnez-la depuis <https://faucet.sui.io> si la preuve de travail ou la limite de débit a bloqué `init`. Gardez le même secret pour que l'adresse dérivée ne change pas. Puis relancez `init`.

## `MoveAbort` code 7 sur `append`

Le code 7 est `E_VERSION` : le client a construit un instantané contre une tête périmée. La synchronisation relit GraphQL jusqu'à atteindre au moins la version enregistrée localement. Si l'erreur reste, attendez quelques secondes et relancez `hermes mnemos flush`. L'entrée reste locale tant qu'un ajout n'a pas réussi.

## `MoveAbort` code 6

L'index a déjà 32 instantanés. Cette version ne fait pas tourner l'objet. La restauration de l'ancien index fonctionne encore jusqu'à l'effacement du testnet.

## Prefetch vide

Le fait est chiffré au repos hors de la machine, mais le rappel lit le cache local. Après une restauration, le cache doit contenir le fait. Lancez `hermes mnemos restore` avant d'attendre un prefetch. Les requêtes ignorent les jetons de moins de 4 caractères, donc une requête `ok` ne rappelle rien, volontairement.

## `hermes plugins validate` signale des binaires

`native/target/` contient des artefacts de compilation. Ils sont ignorés par git et ne doivent pas être commités. Validez un arbre propre. Un avertissement qui nomme `libmnemos_core.so` signifie qu'un répertoire de build est encore dans le chemin du plugin.

## Le diagnostic dit que l'index manque

`index_object_id` est vide, ou GraphQL ne voit pas encore l'objet. `init` attend l'objet après l'inclusion de la transaction dans un checkpoint. Si vous avez collé un identifiant, vérifiez que le réseau est `testnet` et que les URL RPC et GraphQL correspondent à ce réseau.

## `refusing to sign: ... Mnemos only writes to Sui testnet`

Le `sui_rpc` de `mnemos/config.json` n'a pas annoncé l'identifiant de chaîne de Sui testnet, ou n'a pas répondu à temps. Rien n'a été signé ni envoyé. Remettez `sui_rpc` sur `https://fullnode.testnet.sui.io:443` (ou un autre nœud testnet) et relancez. Les entrées restent dans la file locale jusqu'à un flush réussi.

## `MemoryIndex uses the pre-0.2.2 key derivation and is read-only`

L'index a été créé avant Argon2id. La restauration et `history --verify` fonctionnent toujours, pas les écritures. Lancez `hermes mnemos init` pour créer un nouvel index. Voir [restauration](restore.md#index-créés-avant-022).

## Le point de contrôle a levé une erreur et le compactage s'est arrêté

C'est le comportement de l'API v2 quand `compression.checkpoint_required` est vrai. Réparez Walrus ou Sui, puis réessayez. La transcription n'est pas compactée. Avec le réglage désactivé, Hermes journalise l'erreur et compacte quand même.
