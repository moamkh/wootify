"""Compatibility facade for the relocated SQLAlchemy models.

New code should import persistence entities from
``wootify.infrastructure.persistence.models``.
"""

from wootify.infrastructure.persistence.models import *  # noqa: F401,F403
