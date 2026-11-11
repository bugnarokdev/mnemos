"""Mnemos memory engine (Python fallback and native-core facade)."""

__all__ = ["open_engine"]


def open_engine(db_path: str, secret: str, config: dict):
    from .engine import open_engine as _open

    return _open(db_path, secret, config)
