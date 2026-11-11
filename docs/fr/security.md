# Sécurité et chiffrement

Le secret de l'utilisateur ne quitte pas le processus, et le texte en clair ne quitte pas la machine.

## Clés

`MNEMOS_SECRET` est une phrase secrète, étirée avant usage. Argon2id (RFC 9106, version 0x13) en tire une clé maîtresse de 32 octets avec ces paramètres :

| Paramètre | Valeur |
| --- | --- |
| Mémoire | 64 Mio (`m = 65536` Kio) |
| Passes | 3 (`t = 3`) |
| Voies | 1 (`p = 1`) |
| Sel | la chaîne fixe `mnemos-kdf-v2/argon2id` |
| Sortie | 32 octets |

Le sel est fixe exprès : une restauration sur une nouvelle machine n'a que la phrase secrète et l'identifiant de l'objet, et la clé publique de signature doit être connue avant que le MemoryIndex existe. La protection vient du coût de chaque essai, donc choisissez une phrase longue (quatre mots aléatoires ou plus). Argon2id tourne une fois par processus et le résultat reste en mémoire.

HKDF-SHA256 (RFC 5869) dérive ensuite trois valeurs de 32 octets depuis la clé maîtresse :

| Sel | Info | Usage |
| --- | --- | --- |
| `mnemos-aead-v2` | `xchacha20-poly1305` | Chiffrement des blobs |
| `mnemos-sign-v2` | `ed25519-seed` | Signatures des instantanés |
| `mnemos-sui-v2` | `ed25519-seed` | Clé de gaz Sui, seulement si `MNEMOS_SUI_PRIVATE_KEY` est vide |

Les backends Python et Rust dérivent les mêmes octets ; `tests/test_kdf.py` et les tests de `native/src/chain.rs` fixent les mêmes vecteurs.

La clé publique des instantanés est enregistrée dans le `MemoryIndex` à sa création. Les ajouts suivants doivent être vérifiés avec cette clé. Quelqu'un qui vole seulement la clé de gaz Sui ne peut pas ajouter un instantané accepté par le contrat. Quelqu'un qui vole seulement le secret peut déchiffrer les blobs qu'il télécharge et produire des signatures, mais ne peut pas ajouter sans contrôler aussi l'objet.

### Mémoires créées avant 0.2.2

Avant 0.2.2, les trois clés venaient d'un seul HKDF sur la phrase secrète (sels `mnemos-aead-v1`, `mnemos-sign-v1`, `mnemos-sui-v1`). C'est rapide à attaquer hors ligne, car la clé publique et le chiffré sont publics. Mnemos lit toujours ces index : `restore` voit que la clé publique sur la chaîne correspond à l'ancienne dérivation et déchiffre avec l'ancienne clé, et `history --verify` n'a besoin d'aucun secret. Un tel index est en lecture seule : aucun nouvel instantané n'est signé avec les anciennes clés. Pour continuer à écrire, lancez `hermes mnemos init` pour créer un nouveau MemoryIndex, qui reçoit la clé Argon2id. Les nouveaux faits vont dans le nouvel index ; l'ancien reste lisible et vérifiable pour l'historique antérieur. Aucun réglage ne permet de choisir l'ancienne dérivation.

## Garde de chaîne

Chaque transaction signée par Mnemos (init, flush, point de contrôle et l'outil `mnemos_flush` appelable par le modèle) demande d'abord au `sui_rpc` configuré son identifiant de chaîne (`LedgerService.GetServiceInfo`) et refuse sauf s'il s'agit du condensat de genèse de Sui testnet `69WiPg3DAQiwdxfncX6wYQ2siKwAe6L9BZthQea3JNMD` (`4c78adac` en forme courte). Le contrôle échoue fermé : une erreur, un délai dépassé, un identifiant absent ou toute autre chaîne arrête l'écriture avant toute signature ou tout envoi. Les deux backends le font, une fois avant l'envoi vers Walrus et de nouveau juste avant de signer. Il n'y a ni contournement ni mode mainnet, donc aucune écriture hors testnet à approuver.

## Blob

```text
"MNMS" || version 1 || nonce de 24 octets || XChaCha20-Poly1305(texte chiffré || tag)
```

La donnée associée est la chaîne fixe `mnemos-blob-v1`. Le texte en clair est du JSON canonique :

```json
{"entries":[{"id":"...","kind":"fact","session_id":"...","text":"...","ts":0}],"v":1}
```

Walrus stocke ces octets et renvoie un identifiant de blob. L'identifiant n'est pas secret. Les octets ne se lisent pas sans le secret.

## Ce qui est public

Les objets Sui sont publics. Quiconque connaît l'identifiant de l'objet peut lire les identifiants de blobs, les sessions, les hash, les hash précédents, les signatures et la clé publique. Il ne peut pas lire les faits sans `MNEMOS_SECRET`.

## Ce que le prefetch envoie au modèle

Le prefetch injecte le texte en clair correspondant, depuis le cache local, dans l'invite Hermes. C'est le rôle de la mémoire. Ce texte n'est pas envoyé à Walrus ni à Sui. Les appels d'outils que Hermes reflète via `on_memory_write` ou `sync_turn` peuvent contenir du texte de l'espace de travail. Le secret protège ce texte au repos hors de la machine. Il ne le cache pas au modèle sur cette machine.

## Notes pour le catalogue

Le téléchargement de la wheel native est désactivé par défaut. Avec `MNEMOS_NATIVE_AUTO=1`, il ne vient que de la release GitHub officielle `mnemos-native-v0.2.3` de ce dépôt, et la wheel doit correspondre à un sha256 épinglé dans `mnemos/native_wheel.py` ; aucune URL de remplacement, aucun checksum lu sur le réseau. La wheel est extraite sous `$HERMES_HOME/mnemos/native/mnemos-native-v0.2.3/` et ce dossier est ajouté à la fin de `sys.path`, il ne masque donc aucun autre module. Un `mnemos_core` antérieur à 0.2.3 (sans la dérivation Argon2id ni les lectures sur le nœud complet) est ignoré et le backend Python est utilisé. Chaque wheel est construite et attestée par le workflow GitHub Actions public du dépôt à partir du tag `mnemos-native-v0.2.3` ; le commit suivant le tag ajoute seulement la table des checksums dans `mnemos/native_wheel.py`, qui ne fait pas partie de la wheel.

Les secrets sont déclarés dans `plugin.yaml` et lus seulement via `get_secret`. Le plugin ne lit pas `auth.json`, les fichiers d'un autre profil, ni un shell enfant en mode YOLO. Le diagnostic et le faucet ont des délais. `on_session_end` cesse d'attendre après 20 secondes.
