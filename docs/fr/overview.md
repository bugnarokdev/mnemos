# Vue d'ensemble

Mnemos est un fournisseur de mémoire pour Hermes. Hermes conserve déjà `MEMORY.md` et `USER.md`. Mnemos est le fournisseur externe choisi avec `memory.provider: mnemos`. Il ajoute un dépôt que l'agent peut reconstruire sur une autre machine.

Ce que l'agent peut faire :

- `mnemos_remember` enregistre un fait dans le cache local et dans la file d'attente.
- `mnemos_recall` interroge ce cache. Il n'appelle pas le réseau.
- `mnemos_flush` chiffre la file, envoie un blob à Walrus et ajoute une entrée à l'objet `MemoryIndex` sur Sui.
- `mnemos_status` et `hermes mnemos doctor` indiquent l'état du dépôt local, le backend actif, et pourquoi il a été choisi.

Hermes appelle aussi le fournisseur de lui-même. `prefetch` injecte le rappel mis en cache avant un tour. `sync_turn` et `on_memory_write` mettent le travail en file sans bloquer le tour. `on_pre_compress` (API de point de contrôle v2) archive la transcription avant le compactage et lève une erreur si cette archive n'est pas validée. `on_session_end` tente une synchronisation, puis abandonne au bout de 20 secondes pour qu'une exécution sans opérateur ne reste pas bloquée.

La restauration est `hermes mnemos restore`. Hermes n'offre pas d'emplacement de plugin nommé `hermes memory restore`. `hermes memory status` est la commande intégrée et affiche la configuration Mnemos. Le diagnostic et la restauration sont sur `hermes mnemos`.

`hermes mnemos history --verify` parcourt chaque snapshot du MemoryIndex et le vérifie contre Walrus, sans le secret. Voir [historique vérifiable](verifiable-history.md).

Cette version vise le testnet Sui et le testnet Walrus. Le mainnet, le partage via Seal et un navigateur d'historique graphique viennent ensuite.
