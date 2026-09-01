"""Shared SQLAlchemy declarative base."""
import uuid
from sqlalchemy.orm import declarative_base

Base = declarative_base()

def _uuid() -> str:
    return str(uuid.uuid4())
