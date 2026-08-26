"""
Module Overview
---------------
Purpose: Platform connector implementations and connector registry abstractions.
Documentation Standard: module/class/public-method docstrings.
"""
__all__ = ['bale', 'telegram', 'connector_registry']


def __getattr__(name: str):
    """Resolve connector exports lazily to keep plugin composition acyclic."""
    if name == 'connector_registry':
        from wootify.connectors.registry import connector_registry

        return connector_registry
    if name == 'bale':
        from wootify.connectors.bale_connector import bale

        return bale
    if name == 'telegram':
        from wootify.connectors.telegram_connector import telegram

        return telegram
    raise AttributeError(name)
