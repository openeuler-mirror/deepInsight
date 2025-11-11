from .base import Base

# Import papers so their tables are registered on Base.metadata
from . import academic  # noqa: F401
from . import knowledge  # noqa: F401

__all__ = ["Base", "academic", "knowledge"]