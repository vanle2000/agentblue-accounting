"""Categorization FastAPI router."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.categorization.exceptions import (
    CategorizationNotFoundError,
    InvalidCategorizationStateError,
    InvalidTargetAccountError,
    ReviewConflictError,
)
from agentblue.categorization.models import CategorizationRule
from agentblue.categorization.repository import CategorizationRepository
from agentblue.categorization.review import ReviewService
from agentblue.categorization.schemas import (
    CategorizationDetail,
    CategorizationRunRequest,
    CategorizationRunResponse,
    CategorizationSummary,
    ReviewQueueResponse,
    ReviewRequest,
    ReviewResponse,
    RuleCreateRequest,
    RuleResponse,
)
from agentblue.categorization.services import CategorizationService
from agentblue.db.session import get_db

logger = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/api/v1/categorization",
    tags=["categorization"],
)


@router.post("/runs", response_model=CategorizationRunResponse)
async def create_run(
    body: CategorizationRunRequest,
    db: AsyncSession = Depends(get_db),
) -> CategorizationRunResponse:
    """Run categorization on a set of transactions."""
    service = CategorizationService(db)
    # In production, transactions would be fetched from Stage 5
    # For now, accept empty list and return the run structure
    result = await service.run_categorization(
        body.realm_id,
        [],
        recategorize=body.recategorize,
    )
    return CategorizationRunResponse(**result)


@router.get("/runs/{run_id}")
async def get_run(
    realm_id: str = Query(),
    run_id: str = "",
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    repo = CategorizationRepository(db)
    run = await repo.get_run(realm_id, run_id)
    if run is None:
        raise HTTPException(404, "Run not found.")
    return {
        "run_id": run.id,
        "status": run.status,
        "transaction_count": run.transaction_count,
        "recommended_count": run.recommended_count,
        "needs_review_count": run.needs_review_count,
        "failed_count": run.failed_count,
    }


@router.get("/categorizations", response_model=list[CategorizationSummary])
async def list_categorizations(
    realm_id: str = Query(),
    status: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[CategorizationSummary]:
    repo = CategorizationRepository(db)
    queue = await repo.get_review_queue(realm_id, limit=limit)
    return [
        CategorizationSummary(
            id=c.id,
            transaction_quickbooks_id=c.transaction_quickbooks_id,
            status=c.status,
            recommended_account_quickbooks_id=c.recommended_account_quickbooks_id or "",
            confidence_score=str(c.confidence_score),
            confidence_band=c.confidence_band,
            recommendation_source=c.recommendation_source,
            requires_review=c.requires_review,
        )
        for c in queue
    ]


@router.get("/categorizations/{cat_id}", response_model=CategorizationDetail)
async def get_categorization(
    realm_id: str = Query(),
    cat_id: str = "",
    db: AsyncSession = Depends(get_db),
) -> CategorizationDetail:
    repo = CategorizationRepository(db)
    cat = await repo.get_categorization(realm_id, cat_id)
    if cat is None:
        raise HTTPException(404, "Categorization not found.")
    return CategorizationDetail(
        id=cat.id,
        transaction_quickbooks_id=cat.transaction_quickbooks_id,
        status=cat.status,
        recommended_account_quickbooks_id=cat.recommended_account_quickbooks_id or "",
        confidence_score=str(cat.confidence_score),
        confidence_band=cat.confidence_band,
        recommendation_source=cat.recommendation_source,
        requires_review=cat.requires_review,
        explanation_summary=cat.explanation_summary or "",
        engine_version=cat.engine_version,
    )


@router.post("/categorizations/{cat_id}/review", response_model=ReviewResponse)
async def review_categorization(
    realm_id: str = Query(),
    cat_id: str = "",
    body: ReviewRequest = Body(...),
    db: AsyncSession = Depends(get_db),
) -> ReviewResponse:
    service = ReviewService(db)
    try:
        result = await service.review(
            realm_id,
            cat_id,
            decision=body.decision,
            reviewer=body.reviewer,
            selected_account_quickbooks_id=body.selected_account_quickbooks_id,
            review_note=body.review_note,
        )
        return ReviewResponse(**result)
    except CategorizationNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (InvalidCategorizationStateError, ReviewConflictError) as exc:
        raise HTTPException(409, str(exc)) from exc
    except InvalidTargetAccountError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/rules", response_model=RuleResponse)
async def create_rule(
    body: RuleCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> RuleResponse:
    repo = CategorizationRepository(db)
    rule = CategorizationRule(
        realm_id=body.realm_id,
        name=body.name,
        rule_type=body.rule_type,
        conditions=body.conditions,
        target_account_quickbooks_id=body.target_account_quickbooks_id,
        precedence=body.precedence,
        description=body.description or None,
    )
    created = await repo.create_rule(rule)
    return RuleResponse(
        id=created.id,
        name=created.name,
        rule_type=created.rule_type,
        rule_status=created.rule_status,
        precedence=created.precedence,
        target_account_quickbooks_id=created.target_account_quickbooks_id,
    )


@router.get("/rules", response_model=list[RuleResponse])
async def list_rules(
    realm_id: str = Query(),
    db: AsyncSession = Depends(get_db),
) -> list[RuleResponse]:
    repo = CategorizationRepository(db)
    rules = await repo.get_active_rules(realm_id)
    return [
        RuleResponse(
            id=r.id,
            name=r.name,
            rule_type=r.rule_type,
            rule_status=r.rule_status,
            precedence=r.precedence,
            target_account_quickbooks_id=r.target_account_quickbooks_id,
            match_count=r.match_count,
        )
        for r in rules
    ]


@router.get("/review-queue", response_model=ReviewQueueResponse)
async def review_queue(
    realm_id: str = Query(),
    limit: int = Query(default=50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ReviewQueueResponse:
    repo = CategorizationRepository(db)
    items = await repo.get_review_queue(realm_id, limit=limit)
    return ReviewQueueResponse(
        items=[
            CategorizationSummary(
                id=c.id,
                transaction_quickbooks_id=c.transaction_quickbooks_id,
                status=c.status,
                recommended_account_quickbooks_id=(c.recommended_account_quickbooks_id or ""),
                confidence_score=str(c.confidence_score),
                confidence_band=c.confidence_band,
                recommendation_source=c.recommendation_source,
                requires_review=c.requires_review,
            )
            for c in items
        ],
        total=len(items),
        limit=limit,
    )
