"""Accounting context FastAPI endpoints.

Transport-layer only. Business logic in services.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.db.session import get_db
from agentblue.integrations.quickbooks.accounting.domain import (
    CandidateFilter,
)
from agentblue.integrations.quickbooks.accounting.repository import AccountingRepository
from agentblue.integrations.quickbooks.accounting.service import AccountSyncService
from agentblue.integrations.quickbooks.accounting.services import (
    AccountCandidateService,
    AccountHierarchyService,
    AccountUsageService,
    AccountValidationService,
    TransactionAccountResolver,
)
from agentblue.integrations.quickbooks.config import get_quickbooks_settings
from agentblue.integrations.quickbooks.repository import InMemoryTokenRepository

logger = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/api/v1/integrations/quickbooks/accounts",
    tags=["quickbooks-accounts"],
)


# --- Response models ---


class AccountSummary(BaseModel):
    quickbooks_id: str
    name: str
    fully_qualified_name: str = ""
    classification: str = ""
    account_type: str = ""
    account_subtype: str = ""
    active: bool = True
    subaccount: bool = False
    parent_quickbooks_id: str = ""
    account_number: str = ""
    current_balance: str = "0"


class PaginatedAccounts(BaseModel):
    items: list[AccountSummary]
    total: int
    limit: int
    offset: int


class SyncResponse(BaseModel):
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    marked_deleted: int = 0


class ValidationResultResponse(BaseModel):
    valid: bool
    reason_code: str
    message: str = ""
    account_quickbooks_id: str = ""
    active: bool = True
    account_type: str = ""
    classification: str = ""


class HierarchyNodeResponse(BaseModel):
    quickbooks_id: str
    name: str
    fully_qualified_name: str = ""
    account_type: str = ""
    classification: str = ""
    active: bool = True
    depth: int = 0
    children: list[HierarchyNodeResponse] = []


class CandidateResponse(BaseModel):
    quickbooks_id: str
    name: str
    fully_qualified_name: str = ""
    classification: str = ""
    account_type: str = ""
    active: bool = True
    subaccount: bool = False


class AccountRefResponse(BaseModel):
    quickbooks_account_id: str
    account_id: str = ""
    account_name: str = ""
    classification: str = ""
    account_type: str = ""
    active: bool = True
    source_deleted: bool = False
    resolved: bool = False
    reason_code: str = ""


class UsageEvalResponse(BaseModel):
    allowed: bool
    confidence: str = "high"
    reason_codes: list[str] = []
    warnings: list[str] = []


class ValidateRequest(BaseModel):
    realm_id: str
    quickbooks_account_id: str
    require_active: bool = True
    allowed_account_types: list[str] = []
    allowed_classifications: list[str] = []


class UsageRequest(BaseModel):
    realm_id: str
    quickbooks_account_id: str
    proposed_usage: str


def _to_summary(acct: Any) -> AccountSummary:
    return AccountSummary(
        quickbooks_id=acct.quickbooks_id,
        name=acct.name,
        fully_qualified_name=acct.fully_qualified_name or "",
        classification=acct.classification or "",
        account_type=acct.account_type or "",
        account_subtype=acct.account_subtype or "",
        active=acct.active,
        subaccount=acct.subaccount,
        parent_quickbooks_id=acct.parent_quickbooks_id or "",
        account_number=acct.account_number or "",
        current_balance=str(acct.current_balance),
    )


def _hierarchy_to_response(node: Any) -> HierarchyNodeResponse:
    return HierarchyNodeResponse(
        quickbooks_id=node.quickbooks_id,
        name=node.name,
        fully_qualified_name=node.fully_qualified_name,
        account_type=node.account_type,
        classification=node.classification,
        active=node.active,
        depth=node.depth,
        children=[_hierarchy_to_response(c) for c in node.children],
    )


# --- Endpoints ---


@router.post("/sync/backfill", response_model=SyncResponse)
async def sync_backfill(
    realm_id: str = Query(),
    db: AsyncSession = Depends(get_db),
) -> SyncResponse:
    """Backfill all accounts (active and inactive)."""
    settings = get_quickbooks_settings()
    repository = InMemoryTokenRepository()

    from agentblue.integrations.quickbooks.api_client import QuickBooksApiClient

    async with QuickBooksApiClient(settings, repository, realm_id) as client:
        service = AccountSyncService(client, db)
        counts = await service.backfill(realm_id)

    return SyncResponse(**counts)


@router.post("/sync/incremental", response_model=SyncResponse)
async def sync_incremental(
    realm_id: str = Query(),
    db: AsyncSession = Depends(get_db),
) -> SyncResponse:
    """Incremental CDC sync for accounts."""
    settings = get_quickbooks_settings()
    repository = InMemoryTokenRepository()

    from agentblue.integrations.quickbooks.api_client import QuickBooksApiClient

    async with QuickBooksApiClient(settings, repository, realm_id) as client:
        service = AccountSyncService(client, db)
        counts = await service.sync_incremental(realm_id)

    return SyncResponse(**counts)


@router.get("", response_model=PaginatedAccounts)
async def list_accounts(
    realm_id: str = Query(),
    active_only: bool = Query(default=True),
    account_type: str = Query(default=""),
    classification: str = Query(default=""),
    name_search: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> PaginatedAccounts:
    """List accounts with optional filters."""
    repo = AccountingRepository(db)
    accounts = await repo.get_accounts_by_realm(
        realm_id,
        active_only=active_only,
        account_type=account_type,
        classification=classification,
        name_search=name_search,
        max_results=limit + offset,
    )

    page = accounts[offset : offset + limit]
    return PaginatedAccounts(
        items=[_to_summary(a) for a in page],
        total=len(accounts),
        limit=limit,
        offset=offset,
    )


@router.get("/{quickbooks_account_id}", response_model=AccountSummary)
async def get_account(
    realm_id: str = Query(),
    quickbooks_account_id: str = "",
    db: AsyncSession = Depends(get_db),
) -> AccountSummary:
    """Get a single account by QuickBooks ID."""
    repo = AccountingRepository(db)
    account = await repo.get_account_by_quickbooks_id(realm_id, quickbooks_account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found.")
    return _to_summary(account)


@router.get(
    "/{quickbooks_account_id}/hierarchy",
    response_model=HierarchyNodeResponse,
)
async def get_hierarchy(
    realm_id: str = Query(),
    quickbooks_account_id: str = "",
    db: AsyncSession = Depends(get_db),
) -> HierarchyNodeResponse:
    """Get account hierarchy rooted at the given account."""
    service = AccountHierarchyService(db)
    node = await service.get_hierarchy(realm_id, quickbooks_account_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Account not found.")
    return _hierarchy_to_response(node)


@router.post("/validate", response_model=ValidationResultResponse)
async def validate_account(
    body: ValidateRequest,
    db: AsyncSession = Depends(get_db),
) -> ValidationResultResponse:
    """Validate an account reference."""
    service = AccountValidationService(db)
    result = await service.validate_account_reference(
        body.realm_id,
        body.quickbooks_account_id,
        require_active=body.require_active,
        allowed_account_types=body.allowed_account_types or None,
        allowed_classifications=body.allowed_classifications or None,
    )
    return ValidationResultResponse(
        valid=result.valid,
        reason_code=result.reason_code.value,
        message=result.message,
        account_quickbooks_id=result.account_quickbooks_id,
        active=result.active,
        account_type=result.account_type,
        classification=result.classification,
    )


@router.get("/candidates", response_model=list[CandidateResponse])
async def get_candidates(
    realm_id: str = Query(),
    active_only: bool = Query(default=True),
    account_type: str = Query(default=""),
    classification: str = Query(default=""),
    name_search: str = Query(default=""),
    include_subaccounts: bool = Query(default=True),
    max_results: int = Query(default=100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
) -> list[CandidateResponse]:
    """Get account candidates for future categorization."""
    service = AccountCandidateService(db)
    filters = CandidateFilter(
        realm_id=realm_id,
        active_only=active_only,
        account_type=account_type,
        classification=classification,
        name_search=name_search,
        include_subaccounts=include_subaccounts,
        max_results=max_results,
    )
    candidates = await service.get_candidates(filters)
    return [
        CandidateResponse(
            quickbooks_id=str(c["quickbooks_id"]),
            name=str(c["name"]),
            fully_qualified_name=str(c.get("fully_qualified_name", "")),
            classification=str(c.get("classification", "")),
            account_type=str(c.get("account_type", "")),
            active=bool(c.get("active", True)),
            subaccount=bool(c.get("subaccount", False)),
        )
        for c in candidates
    ]


@router.post("/resolve-ref", response_model=AccountRefResponse)
async def resolve_account_ref(
    body: UsageRequest,
    db: AsyncSession = Depends(get_db),
) -> AccountRefResponse:
    """Resolve a transaction account reference."""
    service = TransactionAccountResolver(db)
    result = await service.resolve(body.realm_id, body.quickbooks_account_id, "HEADER_ACCOUNT")
    return AccountRefResponse(
        quickbooks_account_id=result.quickbooks_account_id,
        account_id=result.account_id,
        account_name=result.account_name,
        classification=result.classification,
        account_type=result.account_type,
        active=result.active,
        source_deleted=result.source_deleted,
        resolved=result.resolved,
        reason_code=result.reason_code,
    )


@router.post("/evaluate-usage", response_model=UsageEvalResponse)
async def evaluate_usage(
    body: UsageRequest,
    db: AsyncSession = Depends(get_db),
) -> UsageEvalResponse:
    """Evaluate account suitability for a proposed usage."""
    repo = AccountingRepository(db)
    account = await repo.get_account_by_quickbooks_id(body.realm_id, body.quickbooks_account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found.")

    service = AccountUsageService()
    result = await service.evaluate(
        {
            "classification": account.classification or "",
            "active": account.active,
            "source_deleted": account.source_deleted,
        },
        body.proposed_usage,
    )
    return UsageEvalResponse(
        allowed=result.allowed,
        confidence=result.confidence,
        reason_codes=result.reason_codes,
        warnings=result.warnings,
    )
