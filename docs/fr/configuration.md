# Configuration

Les réglages non secrets sont dans `$HERMES_HOME/mnemos/config.json`. Les secrets sont dans le magasin du profil et lus avec `get_secret`.

## Secrets

| Variable | Obligatoire | Rôle |
| --- | --- | --- |
| `MNEMOS_SECRET` | oui | Phrase secrète. HKDF-SHA256 dérive la clé XChaCha20-Poly1305, la graine Ed25519 des instantanés et, si aucune clé Sui n'est définie, la clé de gaz. |
| `MNEMOS_SUI_PRIVATE_KEY` | non | Clé de gaz optionnelle. L'extension Rust accepte le bech32 `suiprivkey`, le base64 du trousseau Sui, ou une graine hexadécimale de 32 octets. Le backend Python accepte une graine hexadécimale de 32 octets. Si vide, les deux dérivent la clé de gaz de `MNEMOS_SECRET`. |
| `MNEMOS_BACKEND` | non | `python` force l'écrivain gRPC Python. Vide préfère `mnemos_core` quand il peut être chargé. |
| `MNEMOS_NATIVE_AUTO` | non | Désactivé par défaut. `1` active le téléchargement de la wheel native depuis la release officielle `mnemos-native-v0.2.1` sur `https://github.com/bugnarokdev/mnemos`. L'URL n'est pas modifiable et la wheel doit correspondre à un sha256 épinglé. |

`hermes memory setup` demande ces deux secrets et `index_object_id`.

## Fichier

Les valeurs par défaut sont les points de terminaison publics du testnet.

| Clé | Défaut |
| --- | --- |
| `network` | `testnet` |
| `sui_rpc` | `https://fullnode.testnet.sui.io:443` |
| `graphql_url` | `https://graphql.testnet.sui.io/graphql` |
| `faucet_url` | `https://faucet.testnet.sui.io` |
| `walrus_publisher` | `https://publisher.walrus-testnet.walrus.space` |
| `walrus_aggregator` | `https://aggregator.walrus-testnet.walrus.space` |
| `epochs` | `5` |
| `package_id` | vide jusqu'à `hermes mnemos init` |
| `index_object_id` | vide jusqu'à init, ou un objet existant pour la restauration |

`sui_rpc` est le nœud complet gRPC. Le JSON-RPC des nœuds publics est retiré. Un déploiement mainnet ultérieur peut pointer `sui_rpc` vers un autre point gRPC, y compris une URL dRPC, sans changer le code.

`epochs` est le nombre d'époques Walrus pendant lesquelles le publisher conserve chaque blob. Les époques de testnet sont courtes et le réseau peut être effacé. Ne traitez pas ces blobs comme durables.

Le panneau du tableau de bord est `config_schema.py`. Il déclare `CONFIG_SCHEMA` comme des données simples ; Hermes en reconstruit ses types de panneau, donc le fichier n'importe pas d'internes Hermes.

## Commandes

| Commande | Rôle |
| --- | --- |
| `hermes memory status` | État intégré. Affiche la section Mnemos via `get_status_config`. |
| `hermes mnemos status` | Nombre d'entrées, tête de chaîne, backend (`rust` ou `python`). |
| `hermes mnemos doctor` | Secret, dépôt, Walrus, GraphQL et objet index. Code de sortie 1 si le dépôt local est inutilisable. |
| `hermes mnemos init --bytecode FICHIER` | Faucet, publication, création de l'objet possédé. |
| `hermes mnemos flush` | Valide la file d'attente. |
| `hermes mnemos restore --object-id 0x...` | Reconstruit le cache local. |

Les commandes `hermes mnemos` n'apparaissent que lorsque `memory.provider` vaut `mnemos`.
