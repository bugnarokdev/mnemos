# Installation

Prérequis :

- Hermes Agent 0.23.1 ou plus récent (`requires_hermes: ">=0.23.1"`). Ce plancher correspond à l'API de point de contrôle v2 livrée dans cette version.
- Python 3.11 ou plus récent. Un checkout Hermes actuel utilise 3.14.
- Pour les écritures sur la chaîne : une chaîne d'outils Rust, `maturin`, et la CLI `sui` pour compiler le paquet Move.
- L'accès réseau aux points de terminaison de testnet listés dans le README.

## Disposition du plugin

La racine du dépôt est le plugin.

```bash
ln -sfn /chemin/vers/mnemos "$HERMES_HOME/plugins/mnemos-sui"
```

`$HERMES_HOME/config.yaml` :

```yaml
memory:
  provider: mnemos-sui
```

`$HERMES_HOME/.env` :

```bash
MNEMOS_SECRET=choisissez-une-longue-phrase
```

Hermes lit ce secret avec `agent.secret_scope.get_secret`. Mnemos n'ouvre pas `.env` lui-même.

Vérifier l'installation :

```bash
hermes plugins validate /chemin/vers/mnemos --install-deps
hermes memory status
```

`validate` contrôle le manifeste, charge `register()` et installe les dépendances Python (`cryptography`, `grpcio`, `protobuf`, `argon2-cffi`). Elles suffisent pour écrire sur Walrus et Sui. Rust n'est pas obligatoire.

## Cœur natif

Par défaut Mnemos utilise le backend Python et ne télécharge rien. Le téléchargement natif est optionnel : avec `MNEMOS_NATIVE_AUTO=1` et sans `mnemos_core` importable, la première ouverture télécharge la wheel correspondante depuis la release officielle `mnemos-native-v0.2.3` sur `https://github.com/bugnarokdev/mnemos`, la compare au sha256 épinglé dans `mnemos/native_wheel.py`, l'extrait sous `$HERMES_HOME/mnemos/native/mnemos-native-v0.2.3/`, ajoute ce dossier à la fin de `sys.path`, puis la charge. Un `mnemos_core` antérieur à 0.2.3 est ignoré. L'URL est fixe et aucun fichier de checksums n'est lu sur le réseau. Une release absente, un checksum faux, ou `MNEMOS_BACKEND=python` laisse le backend Python. `hermes mnemos-sui doctor` indique le backend actif et pourquoi.

GitHub Actions (`.github/workflows/native-wheels.yml`) construit les wheels et les atteste avec `actions/attest-build-provenance`. Aucun binaire n'est commité.

`scripts/build_core.sh` construit encore l'extension localement si vous le voulez :

```bash
export MNEMOS_PYTHON=/chemin/vers/le/python/utilisé/par/hermes
export MATURIN=maturin
./scripts/build_core.sh
```

`MNEMOS_PYTHON` doit être l'interpréteur qui importe réellement les plugins. Sur un checkout source, c'est l'environnement virtuel choisi par Hermes, pas un autre Python du système.

Une installation depuis le catalogue ne compile pas Rust. Le backend Python produit le même chiffrement, le même hash et la même signature, puis envoie la transaction Sui en gRPC. Les points de contrôle réussissent dans ce mode.

## Première chaîne

```bash
cd move/MemoryIndex
sui move build --dump-bytecode-as-base64 > /tmp/mnemos-bytecode.json
hermes mnemos-sui init --bytecode /tmp/mnemos-bytecode.json
```

`init` dérive une clé Sui de `MNEMOS_SECRET` sauf si `MNEMOS_SUI_PRIVATE_KEY` est défini, demande du gaz au faucet de testnet, publie `MemoryIndex` et crée l'objet possédé. Il écrit `package_id` et `index_object_id` dans `$HERMES_HOME/mnemos/config.json`.

## Démonstration

```bash
export HERMES_BIN=hermes
export SUI_BIN=sui
export MNEMOS_PYTHON=/chemin/vers/le/python/hermes
export HERMES_SRC=/chemin/vers/hermes-agent   # seulement pour un checkout source
./scripts/demo.sh
```

Le script utilise deux profils sous `$HOME/.hermes/profiles/` (`mnemos-demo-a` et `mnemos-demo-b`) afin qu'ils partagent l'installation des dépendances Hermes, puis les supprime.
