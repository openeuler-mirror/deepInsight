from __future__ import annotations

from enum import Enum

class OwnerType(str, Enum):
    """Common owner types for knowledge base binding."""
    CONFERENCE = "conference"
    # Future owners can be added here, e.g. WORKSPACE = "workspace", USER = "user"