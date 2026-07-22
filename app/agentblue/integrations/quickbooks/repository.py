"""QuickBooks OAuth token persistence interface.

Defines the contract for token storage. Production implementations must
use encrypted storage. An in-memory implementation is provided for testing.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentblue.integrations.quickbooks.exceptions import QuickBooksTokenStorageError
from agentblue.integrations.quickbooks.models import TokenResponse  # noqa: TC001


@runtime_checkable
class TokenRepository(Protocol):
    """Interface for OAuth token persistence.

    Implementations must handle thread safety and encryption independently.
    The optional version field supports safe concurrent refresh updates.
    """

    async def save(self, token: TokenResponse) -> None:
        """Persist a new token. Raises QuickBooksTokenStorageError on failure."""
        ...

    async def get_by_realm(self, realm_id: str) -> TokenResponse | None:
        """Retrieve the current token for a realm, or None if not found."""
        ...

    async def update(self, token: TokenResponse) -> None:
        """Update an existing token. Raises QuickBooksTokenStorageError if not found."""
        ...

    async def delete(self, realm_id: str) -> bool:
        """Delete the token for a realm. Returns True if deleted, False if not found."""
        ...


class InMemoryTokenRepository:
    """In-memory token repository for unit testing.

    Not suitable for production — tokens are stored in process memory
    without encryption. Production persistence must use encrypted storage.
    """

    def __init__(self) -> None:
        self._store: dict[str, TokenResponse] = {}

    async def save(self, token: TokenResponse) -> None:
        """Store a token keyed by realm_id."""
        if not token.realm_id:
            raise QuickBooksTokenStorageError("Cannot save token without realm_id.")
        self._store[token.realm_id] = token

    async def get_by_realm(self, realm_id: str) -> TokenResponse | None:
        """Retrieve a token by realm_id."""
        return self._store.get(realm_id)

    async def update(self, token: TokenResponse) -> None:
        """Update an existing token. Raises if the realm has no stored token."""
        if token.realm_id not in self._store:
            raise QuickBooksTokenStorageError(
                f"No token found for realm {token.realm_id!r} to update."
            )
        self._store[token.realm_id] = token

    async def delete(self, realm_id: str) -> bool:
        """Delete a token by realm_id."""
        if realm_id in self._store:
            del self._store[realm_id]
            return True
        return False
