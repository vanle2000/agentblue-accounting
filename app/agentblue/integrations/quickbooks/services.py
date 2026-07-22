"""QuickBooks API service wrappers.

Provides high-level service methods for common QuickBooks Online operations.
Only CompanyInfo is fully implemented; others expose interfaces only.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agentblue.integrations.quickbooks.api_client import QuickBooksApiClient  # noqa: TC001


@runtime_checkable
class QuickBooksServiceProtocol(Protocol):
    """Interface for QuickBooks service operations."""

    async def get_company_info(self) -> dict[str, Any]:
        """Retrieve company information for the connected realm."""
        ...


class CompanyInfoService:
    """Service for QuickBooks Company Info operations."""

    def __init__(self, client: QuickBooksApiClient) -> None:
        self._client = client

    async def get_company_info(self) -> dict[str, Any]:
        """Retrieve company information for the connected realm.

        Returns the CompanyInfo object from the QuickBooks API.
        """
        realm_id = self._client._realm_id
        result = await self._client.get(f"/v3/company/{realm_id}/companyinfo/{realm_id}")
        info: dict[str, Any] = result.get("CompanyInfo", {})
        return info


class ChartOfAccountsService:
    """Service for QuickBooks Chart of Accounts operations.

    Interface only — implementation deferred to transaction sync stage.
    """

    def __init__(self, client: QuickBooksApiClient) -> None:
        self._client = client

    async def list_accounts(self, **kwargs: Any) -> list[dict[str, Any]]:
        """List all accounts. Implementation deferred."""
        raise NotImplementedError("Chart of Accounts sync is deferred to a later stage.")


class VendorsService:
    """Service for QuickBooks Vendors operations.

    Interface only — implementation deferred.
    """

    def __init__(self, client: QuickBooksApiClient) -> None:
        self._client = client

    async def list_vendors(self, **kwargs: Any) -> list[dict[str, Any]]:
        """List all vendors. Implementation deferred."""
        raise NotImplementedError("Vendor sync is deferred to a later stage.")


class CustomersService:
    """Service for QuickBooks Customers operations.

    Interface only — implementation deferred.
    """

    def __init__(self, client: QuickBooksApiClient) -> None:
        self._client = client

    async def list_customers(self, **kwargs: Any) -> list[dict[str, Any]]:
        """List all customers. Implementation deferred."""
        raise NotImplementedError("Customer sync is deferred to a later stage.")


class TransactionsService:
    """Service for QuickBooks Transactions operations.

    Placeholder only — implementation deferred to transaction sync stage.
    """

    def __init__(self, client: QuickBooksApiClient) -> None:
        self._client = client

    async def list_transactions(self, **kwargs: Any) -> list[dict[str, Any]]:
        """List transactions. Implementation deferred."""
        raise NotImplementedError("Transaction sync is deferred to a later stage.")
