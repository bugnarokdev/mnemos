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

## Suite

Partager une capacité de lecture via Seal, et une petite interface qui parcourt la chaîne, ne sont pas dans cette version. La forme de l'objet et le lien de hash sont les pièces dont ces fonctions ont besoin, et elles sont appliquées maintenant.
