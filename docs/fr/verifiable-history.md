# Historique vérifiable

Le hash de contenu de chaque instantané engage le hash de contenu de l'instantané précédent. `MemoryIndex` garde ce hash comme tête et contrôle le lien avant d'accepter un ajout.

## Hash de contenu

```text
préimage = "MNEMOS1\0"
         || prev_hash          (32 octets)
         || sha256(blob)       (32 octets)
         || version            (u64 petit-boutiste)
         || u32le(len) || session_id
         || u32le(len) || blob_id
content_hash = SHA-256(préimage)
```

La signature est Ed25519 sur `mnemos-snapshot-v1 || content_hash`, avec la clé d'instantané dérivée de `MNEMOS_SECRET`. L'objet est créé avec cette clé publique. `append` avorte sauf si :

- le hash précédent égale la tête actuelle (32 octets nuls à la genèse),
- la version vaut exactement `version_de_tête + 1`,
- `ed25519_verify` accepte la signature de ce message.

Le contrat ne télécharge pas le blob Walrus. La vérification complète, y compris « cet identifiant de blob est bien ce texte chiffré », se fait dans le client pendant la restauration : recalculer le hash à partir des octets téléchargés et comparer. N'importe qui disposant des blobs et de l'objet peut le faire sans le secret. Lire les faits exige toujours le secret.

## Vérifier soi-même

```bash
hermes mnemos history                       # liste chaque version
hermes mnemos history --verify              # vérifie chaque version
hermes mnemos history --verify --object-id 0x...  --json
```

`--object-id` vaut par défaut `index_object_id` de la configuration. Aucun secret n'est nécessaire. Pour chaque snapshot, `--verify` :

1. vérifie que la version vaut la précédente plus 1 (1 à la genèse),
2. vérifie que `prev_hash` égale le hash de contenu du snapshot précédent (32 octets nuls à la genèse),
3. télécharge le blob depuis l'agrégateur Walrus et recalcule `content_hash` à partir de son sha256,
4. vérifie la signature Ed25519 avec la clé publique stockée dans l'objet.

Elle affiche une ligne par version, `OK` ou `BROKEN` avec la raison, puis un verdict. La tête de l'index doit égaler le dernier hash de contenu. Tout échec sort avec le code 1. Un blob remplacé, un hash modifié, un lien cassé ou une signature forgée est signalé à la version où il se produit.

```text
MemoryIndex 0x40028382ba48edaff90240696fb0441854046d9bee10195d296d2046a72668e3  pubkey c5f2488e608132c3
v1    OK     03053bacf1305209  blob ymHOBfSei9ECmnM_CoA-4Q2oLtTX3tYgVgvN4Hngxiw
OK: 1 version verified
```

## Suite

Partager une capacité de lecture via Seal, et une interface graphique qui parcourt la chaîne, ne sont pas dans cette version. La forme de l'objet et le lien de hash sont les pièces dont ces fonctions ont besoin, et elles sont appliquées maintenant.
