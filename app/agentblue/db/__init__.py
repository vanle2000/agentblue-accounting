"""Database package. Re-exports the session dependency."""

from agentblue.db.session import get_db

__all__ = ["get_db"]
