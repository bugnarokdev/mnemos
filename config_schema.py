"""Dashboard config panel for Mnemos. Loaded by path, not as part of plugin startup.

Hermes rebuilds this plain record into its memory-provider panel schema, so this
file does not import Hermes internals.
"""

CONFIG_SCHEMA = {
    "name": "mnemos",
    "label": "Mnemos",
    "storage": "flat_json",
    "docs_url": "https://github.com/bugnarokdev/mnemos/blob/main/docs/en/configuration.md",
    "fields": (
        {
            "key": "mnemos_secret",
            "label": "Secret",
            "kind": "secret",
            "env_key": "MNEMOS_SECRET",
            "description": "Passphrase. Derives encryption and snapshot signing keys.",
            "inline": True,
        },
        {
            "key": "sui_private_key",
            "label": "Sui private key",
            "kind": "secret",
            "env_key": "MNEMOS_SUI_PRIVATE_KEY",
            "description": "Optional. suiprivkey bech32 or hex seed. Derived from the secret when empty.",
        },
        {
            "key": "index_object_id",
            "label": "MemoryIndex object",
            "kind": "text",
            "description": "Owned Sui object id that stores ordered blob ids and the hash-chain head.",
            "placeholder": "0x...",
            "inline": True,
            "group": "chain",
        },
        {
            "key": "package_id",
            "label": "Package id",
            "kind": "text",
            "description": "Published mnemos::memory_index package id.",
            "group": "chain",
        },
        {
            "key": "sui_rpc",
            "label": "Sui gRPC endpoint",
            "kind": "text",
            "default": "https://fullnode.testnet.sui.io:443",
            "description": "Testnet fullnode by default. Point this at a gRPC endpoint; dRPC is a later mainnet option.",
            "group": "network",
        },
        {
            "key": "graphql_url",
            "label": "Sui GraphQL",
            "kind": "text",
            "default": "https://graphql.testnet.sui.io/graphql",
            "description": "Fallback reader. Used only when the fullnode refuses a read and GraphQL is within graphql_max_lag checkpoints of it.",
            "group": "network",
        },
        {
            "key": "read_source",
            "label": "Sui reads",
            "kind": "text",
            "default": "auto",
            "description": "auto: fullnode gRPC, then GraphQL only if it is fresh. grpc: fullnode only.",
            "group": "network",
        },
        {
            "key": "graphql_max_lag",
            "label": "GraphQL max lag (checkpoints)",
            "kind": "text",
            "default": "100",
            "description": "A GraphQL fallback read is refused when the indexer is further behind the fullnode than this.",
            "group": "network",
        },
        {
            "key": "faucet_url",
            "label": "Faucet",
            "kind": "text",
            "default": "https://faucet.testnet.sui.io",
            "group": "network",
        },
        {
            "key": "walrus_publisher",
            "label": "Walrus publisher",
            "kind": "text",
            "default": "https://publisher.walrus-testnet.walrus.space",
            "group": "network",
        },
        {
            "key": "walrus_aggregator",
            "label": "Walrus aggregator",
            "kind": "text",
            "default": "https://aggregator.walrus-testnet.walrus.space",
            "group": "network",
        },
        {
            "key": "epochs",
            "label": "Walrus epochs",
            "kind": "number",
            "default": "5",
            "description": "How many Walrus testnet epochs to store each blob.",
            "group": "network",
        },
        {
            "key": "network",
            "label": "Network",
            "kind": "text",
            "default": "testnet",
            "group": "network",
        },
    ),
}
