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
        from wootify.plugins.bale.connector import bale

        return bale
    if name == 'telegram':
        from wootify.plugins.telegram.connector import telegram

        return telegram
    raise AttributeError(name)
