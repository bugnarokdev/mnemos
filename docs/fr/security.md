# Sécurité et chiffrement

Le secret de l'utilisateur ne quitte pas le processus, et le texte en clair ne quitte pas la machine.

## Clés

`MNEMOS_SECRET` est le matériel de clé. HKDF-SHA256 (RFC 5869) dérive trois valeurs de 32 octets :

| Sel | Info | Usage |
| --- | --- | --- |
| `mnemos-aead-v1` | `xchacha20-poly1305` | Chiffrement des blobs |
| `mnemos-sign-v1` | `ed25519-seed` | Signatures des instantanés |
| `mnemos-sui-v1` | `ed25519-seed` | Clé de gaz Sui, seulement si `MNEMOS_SUI_PRIVATE_KEY` est vide |

La clé publique des instantanés est enregistrée dans `MemoryIndex` à la création. Les ajouts suivants doivent se vérifier avec cette clé. Quelqu'un qui ne vole que la clé de gaz Sui ne peut pas ajouter un instantané que le contrat accepte. Quelqu'un qui ne vole que le secret peut déchiffrer les blobs qu'il télécharge et produire des signatures, mais ne peut pas ajouter tant qu'il ne contrôle pas aussi l'objet.

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

Le téléchargement de la wheel native est désactivé par défaut. Avec `MNEMOS_NATIVE_AUTO=1`, il ne vient que de la release GitHub officielle `mnemos-native-v0.2.1` de ce dépôt, et la wheel doit correspondre à un sha256 épinglé dans `mnemos/native_wheel.py` ; aucune URL de remplacement, aucun checksum lu sur le réseau. Chaque wheel est construite et attestée par le workflow GitHub Actions public du dépôt.

Les secrets sont déclarés dans `plugin.yaml` et lus seulement via `get_secret`. Le plugin ne lit pas `auth.json`, les fichiers d'un autre profil, ni un shell enfant en mode YOLO. Le diagnostic et le faucet ont des délais. `on_session_end` cesse d'attendre après 20 secondes.
