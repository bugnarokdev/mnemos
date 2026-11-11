"""Mnemos memory provider plugin.

``register`` hands Hermes a new ``MnemosProvider`` via ``ctx.register_memory_provider``.
"""


def register(ctx) -> None:
    from .mnemos.provider import MnemosProvider

    ctx.register_memory_provider(MnemosProvider())
