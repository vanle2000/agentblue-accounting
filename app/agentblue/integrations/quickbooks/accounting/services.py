"""Account validation, candidate, hierarchy, and usage services.

Read-only services for accounting context queries.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.integrations.quickbooks.accounting.domain import (
    CandidateFilter,
    HierarchyNode,
    ProposedUsage,
    TransactionAccountRef,
    UsageEvaluation,
    ValidationResult,
    ValidationStatus,
)
from agentblue.integrations.quickbooks.accounting.repository import AccountingRepository

# Known account classification-to-usage mappings
_USAGE_RULES: dict[ProposedUsage, set[str]] = {
    ProposedUsage.EXPENSE: {"Expense"},
    ProposedUsage.INCOME: {"Revenue"},
    ProposedUsage.ASSET: {"Asset"},
    ProposedUsage.LIABILITY: {"Liability"},
    ProposedUsage.EQUITY: {"Equity"},
    ProposedUsage.BANK: {"Asset"},
    ProposedUsage.ACCOUNTS_PAYABLE: {"Liability"},
    ProposedUsage.ACCOUNTS_RECEIVABLE: {"Asset"},
}


class AccountValidationService:
    """Validates account references against the synced Chart of Accounts."""

    def __init__(self, session: AsyncSession) -> None:
        self._repo = AccountingRepository(session)

    async def validate_account_reference(
        self,
        realm_id: str,
        quickbooks_account_id: str,
        *,
        require_active: bool = True,
        allowed_account_types: list[str] | None = None,
        allowed_classifications: list[str] | None = None,
    ) -> ValidationResult:
        """Validate an account reference."""
        account = await self._repo.get_account_by_quickbooks_id(realm_id, quickbooks_account_id)

        if account is None:
            return ValidationResult(
                valid=False,
                reason_code=ValidationStatus.NOT_FOUND,
                message=f"Account {quickbooks_account_id} not found.",
                account_quickbooks_id=quickbooks_account_id,
            )

        if account.source_deleted:
            return ValidationResult(
                valid=False,
                reason_code=ValidationStatus.SOURCE_DELETED,
                message=f"Account {quickbooks_account_id} is source-deleted.",
                account_quickbooks_id=quickbooks_account_id,
                source_deleted=True,
                account_type=account.account_type or "",
                classification=account.classification or "",
            )

        if require_active and not account.active:
            return ValidationResult(
                valid=False,
                reason_code=ValidationStatus.INACTIVE,
                message=f"Account {quickbooks_account_id} is inactive.",
                account_quickbooks_id=quickbooks_account_id,
                active=False,
                account_type=account.account_type or "",
                classification=account.classification or "",
            )

        if (
            allowed_account_types
            and account.account_type
            and account.account_type not in allowed_account_types
        ):
            return ValidationResult(
                valid=False,
                reason_code=ValidationStatus.TYPE_NOT_ALLOWED,
                message=(
                    f"Account type '{account.account_type}' not in "
                    f"allowed types: {allowed_account_types}."
                ),
                account_quickbooks_id=quickbooks_account_id,
                active=account.active,
                account_type=account.account_type or "",
                classification=account.classification or "",
            )

        if (
            allowed_classifications
            and account.classification
            and account.classification not in allowed_classifications
        ):
            return ValidationResult(
                valid=False,
                reason_code=ValidationStatus.CLASSIFICATION_NOT_ALLOWED,
                message=(
                    f"Classification '{account.classification}' not in "
                    f"allowed: {allowed_classifications}."
                ),
                account_quickbooks_id=quickbooks_account_id,
                active=account.active,
                account_type=account.account_type or "",
                classification=account.classification or "",
            )

        return ValidationResult(
            valid=True,
            reason_code=ValidationStatus.VALID,
            account_quickbooks_id=quickbooks_account_id,
            active=account.active,
            account_type=account.account_type or "",
            account_subtype=account.account_subtype or "",
            classification=account.classification or "",
        )


class AccountCandidateService:
    """Returns account candidates for future categorization."""

    def __init__(self, session: AsyncSession) -> None:
        self._repo = AccountingRepository(session)

    async def get_candidates(self, filters: CandidateFilter) -> list[dict[str, object]]:
        """Get account candidates matching the given filters."""
        accounts = await self._repo.get_accounts_by_realm(
            filters.realm_id,
            active_only=filters.active_only,
            include_deleted=False,
            account_type=filters.account_type,
            classification=filters.classification,
            name_search=filters.name_search,
            max_results=filters.max_results,
        )

        results: list[dict[str, object]] = []
        for acct in accounts:
            if not filters.include_subaccounts and acct.subaccount:
                continue
            if (
                filters.parent_quickbooks_id
                and acct.parent_quickbooks_id != filters.parent_quickbooks_id
            ):
                continue
            results.append(
                {
                    "quickbooks_id": acct.quickbooks_id,
                    "name": acct.name,
                    "fully_qualified_name": acct.fully_qualified_name or "",
                    "classification": acct.classification or "",
                    "account_type": acct.account_type or "",
                    "account_subtype": acct.account_subtype or "",
                    "active": acct.active,
                    "subaccount": acct.subaccount,
                    "parent_quickbooks_id": acct.parent_quickbooks_id or "",
                    "account_number": acct.account_number or "",
                }
            )
        return results


class AccountHierarchyService:
    """Account hierarchy traversal and resolution."""

    def __init__(self, session: AsyncSession) -> None:
        self._repo = AccountingRepository(session)

    async def get_hierarchy(
        self, realm_id: str, quickbooks_id: str, *, max_depth: int = 20
    ) -> HierarchyNode | None:
        """Get the hierarchy tree rooted at the given account."""
        account = await self._repo.get_account_by_quickbooks_id(realm_id, quickbooks_id)
        if account is None:
            return None

        return await self._build_tree(
            realm_id, account.quickbooks_id, depth=0, max_depth=max_depth
        )

    async def get_ancestors(
        self, realm_id: str, quickbooks_id: str, *, max_depth: int = 20
    ) -> list[dict[str, str]]:
        """Get the ancestor chain from root to the given account."""
        ancestors: list[dict[str, str]] = []
        current_id = quickbooks_id
        visited: set[str] = set()

        for _ in range(max_depth):
            if current_id in visited:
                break
            visited.add(current_id)

            account = await self._repo.get_account_by_quickbooks_id(realm_id, current_id)
            if account is None:
                break

            ancestors.append(
                {
                    "quickbooks_id": account.quickbooks_id,
                    "name": account.name,
                    "account_type": account.account_type or "",
                    "classification": account.classification or "",
                }
            )
            if not account.parent_quickbooks_id:
                break
            current_id = account.parent_quickbooks_id

        ancestors.reverse()
        return ancestors

    async def _build_tree(
        self,
        realm_id: str,
        quickbooks_id: str,
        depth: int,
        max_depth: int,
        visited: set[str] | None = None,
    ) -> HierarchyNode | None:
        """Recursively build hierarchy tree with cycle detection."""
        if visited is None:
            visited = set()
        if depth > max_depth or quickbooks_id in visited:
            return None

        visited.add(quickbooks_id)

        account = await self._repo.get_account_by_quickbooks_id(realm_id, quickbooks_id)
        if account is None:
            return None

        children = await self._repo.get_children(realm_id, quickbooks_id)
        child_nodes: list[HierarchyNode] = []
        for child in children:
            node = await self._build_tree(
                realm_id, child.quickbooks_id, depth + 1, max_depth, visited
            )
            if node:
                child_nodes.append(node)

        return HierarchyNode(
            quickbooks_id=account.quickbooks_id,
            name=account.name,
            fully_qualified_name=account.fully_qualified_name or "",
            account_type=account.account_type or "",
            classification=account.classification or "",
            active=account.active,
            depth=depth,
            parent_quickbooks_id=account.parent_quickbooks_id or "",
            children=child_nodes,
        )


class AccountUsageService:
    """Evaluates account suitability for proposed accounting usage."""

    async def evaluate(self, account: dict[str, object], proposed_usage: str) -> UsageEvaluation:
        """Evaluate whether an account is appropriate for a proposed usage."""
        try:
            usage = ProposedUsage(proposed_usage)
        except ValueError:
            return UsageEvaluation(
                allowed=False,
                confidence="low",
                warnings=[f"Unknown proposed usage: {proposed_usage}"],
            )

        classification = str(account.get("classification", ""))
        active = bool(account.get("active", True))
        source_deleted = bool(account.get("source_deleted", False))

        warnings: list[str] = []
        reason_codes: list[str] = []

        if source_deleted:
            return UsageEvaluation(
                allowed=False,
                confidence="high",
                reason_codes=["SOURCE_DELETED"],
                warnings=["Account is source-deleted."],
            )

        if not active:
            warnings.append("Account is inactive.")

        allowed_classifications = _USAGE_RULES.get(usage, set())
        if classification not in allowed_classifications:
            return UsageEvaluation(
                allowed=False,
                confidence="medium",
                reason_codes=["CLASSIFICATION_MISMATCH"],
                warnings=[f"Classification '{classification}' not typical for {proposed_usage}."],
            )

        return UsageEvaluation(
            allowed=True,
            confidence="high",
            reason_codes=reason_codes,
            warnings=warnings,
        )


class TransactionAccountResolver:
    """Resolves transaction account references against the Chart of Accounts."""

    def __init__(self, session: AsyncSession) -> None:
        self._repo = AccountingRepository(session)

    async def resolve(
        self,
        realm_id: str,
        quickbooks_account_id: str,
        reference_role: str = "LINE_ACCOUNT",
    ) -> TransactionAccountRef:
        """Resolve a single account reference."""
        if not quickbooks_account_id:
            return TransactionAccountRef(
                quickbooks_account_id="",
                resolved=False,
                reason_code="EMPTY_REFERENCE",
                reference_role=reference_role,
            )

        account = await self._repo.get_account_by_quickbooks_id(realm_id, quickbooks_account_id)

        if account is None:
            return TransactionAccountRef(
                quickbooks_account_id=quickbooks_account_id,
                resolved=False,
                reason_code="NOT_FOUND",
                reference_role=reference_role,
            )

        if account.realm_id != realm_id:
            return TransactionAccountRef(
                quickbooks_account_id=quickbooks_account_id,
                resolved=False,
                reason_code="REALM_MISMATCH",
                reference_role=reference_role,
            )

        return TransactionAccountRef(
            quickbooks_account_id=quickbooks_account_id,
            account_id=account.id,
            account_name=account.name,
            classification=account.classification or "",
            account_type=account.account_type or "",
            active=account.active,
            source_deleted=account.source_deleted,
            resolved=True,
            reason_code="INACTIVE" if not account.active else "OK",
            reference_role=reference_role,
        )
