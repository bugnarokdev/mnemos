# Testnet Sui et Walrus

## Points de terminaison

| Service | Défaut |
| --- | --- |
| gRPC Sui | `https://fullnode.testnet.sui.io:443` |
| GraphQL Sui | `https://graphql.testnet.sui.io/graphql` |
| Faucet | `https://faucet.testnet.sui.io` |
| Publisher Walrus | `https://publisher.walrus-testnet.walrus.space` |
| Agrégateur Walrus | `https://aggregator.walrus-testnet.walrus.space` |

Remplacez-les dans `mnemos/config.json`. Les écritures passent par gRPC (`sui-rpc` du SDK Rust Mysten). Les lectures de l'index passent par GraphQL parce que le contenu de l'objet revient en JSON. Les nœuds publics ne servent plus le JSON-RPC.

Le stockage Walrus est `PUT /v1/blobs?epochs=N` avec le texte chiffré brut. La lecture est `GET /v1/blobs/<identifiant>`. La réponse du publisher est `newlyCreated.blobObject.blobId` ou `alreadyCertified.blobId`.

## Paquet Move

`move/MemoryIndex` définit `mnemos::memory_index::MemoryIndex`. L'objet appartient à celui qui le publie. Il contient :

- un compteur `version`
- `head`, le dernier hash de contenu, ou 32 octets nuls
- `pubkey`, la clé de vérification des instantanés
- `snapshots`, les enregistrements ordonnés (identifiant de blob, session, hash, signature)
- `sessions`, les identifiants de session distincts déjà vus

`create` transfère l'objet à l'expéditeur de la transaction. `append` est le seul mutateur. L'objet contient au plus 32 instantanés.

Compilez avec la CLI Sui (la dépendance du framework est la révision `framework/testnet`) :

```bash
cd move/MemoryIndex
sui move build --dump-bytecode-as-base64
```

`hermes mnemos-sui init --bytecode` publie ce JSON et appelle `create`.

## Faucet

Le faucet de testnet actuel (`/v3/challenge` puis `/v3/gas`) est une preuve de travail, pas un captcha de navigateur. Les deux backends résolvent l'Argon2d du défi et l'envoient. `init` affiche l'adresse Sui qu'il approvisionne.

Une personne doit intervenir quand :

- le faucet renvoie une limite de débit ou une erreur de type captcha (le point v2 répond maintenant que la compatibilité CI est désactivée ; un futur v3 peut ajouter un contrôle Turnstile),
- la preuve de travail ne finit pas dans la fenêtre du défi.

Dans ce cas, approvisionnez l'adresse affichée depuis le faucet navigateur <https://faucet.sui.io> (cette page peut demander un captcha), gardez le même `MNEMOS_SECRET` et relancez `init`. Si l'adresse a déjà du gaz, `init` saute le faucet.

Le SUI et le WAL de testnet n'ont pas de valeur. Le publisher public paie le stockage Walrus sur le testnet. Ne pointez pas cette version vers le mainnet en attendant un publisher gratuit : Walrus n'a pas de publisher mainnet public sans authentification.

## Gaz

La publication demande un budget de 0,1 SUI. Chaque ajout demande 0,03 SUI. Un versement de faucet de 1 SUI couvre la publication, la création et plusieurs instantanés. Quand le solde est déjà d'au moins 0,2 SUI, `init` n'appelle pas le faucet.
