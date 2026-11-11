# Restauration et migration

La restauration a besoin de deux éléments qui ne sont pas dans le répertoire effacé :

1. Le même `MNEMOS_SECRET` (et le même `MNEMOS_SUI_PRIVATE_KEY` seulement si vous en avez défini un ; la restauration elle-même n'a besoin que du secret).
2. L'identifiant de l'objet `MemoryIndex`.

```bash
export HERMES_HOME=/chemin/vers/le/nouveau/profil
# installez le plugin et MNEMOS_SECRET comme sur l'ancienne machine
hermes mnemos restore --object-id 0xVOTRE_INDEX
```

Vous pouvez omettre `--object-id` quand `index_object_id` est déjà dans `mnemos/config.json`.

La restauration :

1. Lit l'objet via le GraphQL Sui.
2. Vérifie que la clé publique sur la chaîne correspond à la clé dérivée de ce secret.
3. Télécharge chaque blob depuis l'agrégateur Walrus, dans l'ordre des versions.
4. Recalcule le hash de contenu à partir du hash précédent, des octets du blob, de la version, de la session et de l'identifiant de blob.
5. Compare ce hash à l'objet, vérifie la signature Ed25519, et vérifie que chaque hash précédent égale le hash de contenu précédent.
6. Déchiffre. Si le secret est faux, le déchiffrement échoue et la base locale n'est pas modifiée.
7. Remplace les entrées locales par les faits déchiffrés.

Le nouveau profil peut ensuite `prefetch` et `mnemos_recall` sans autre appel réseau.

## Déplacer un profil

Copiez `MNEMOS_SECRET` vous-même dans le magasin de secrets du nouveau profil. Ne le commitez pas. Seul `index_object_id` est nécessaire : `hermes mnemos restore --object-id` l'enregistre, et le `package_id` est lu dans le type Move de l'objet puis enregistré aussi, donc un `flush` ultérieur fonctionne sans `init`. `hermes mnemos restore` reconstruit `mnemos.sqlite`. L'ancien fichier SQLite n'est pas nécessaire.

`backup_paths()` renvoie une liste vide parce que l'état hors chaîne est sous `HERMES_HOME`, que Hermes archive déjà. La chaîne est la sauvegarde qui survit à la suppression de ce répertoire.

## Limites

Un index contient au plus 32 instantanés. Quand il est plein, l'ajout avorte. Enchaîner un nouvel objet qui pointe vers l'ancienne tête n'est pas dans cette version. Les opérateurs peuvent effacer le Walrus de testnet et le Sui de testnet. Après un effacement, l'identifiant d'objet ne restaure plus rien.
