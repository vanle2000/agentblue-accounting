"""Categorization Pydantic schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CategorizationRunRequest(BaseModel):
    realm_id: str
    transaction_ids: list[str] = []
    recategorize: bool = False
    limit: int = Field(default=100, ge=1, le=500)


class CategorizationRunResponse(BaseModel):
    run_id: str
    total: int = 0
    recommended: int = 0
    needs_review: int = 0
    failed: int = 0


class CategorizationSummary(BaseModel):
    id: str
    transaction_quickbooks_id: str
    status: str
    recommended_account_quickbooks_id: str = ""
    confidence_score: str = "0"
    confidence_band: str = "NONE"
    recommendation_source: str = ""
    requires_review: bool = True


class CategorizationDetail(CategorizationSummary):
    explanation_summary: str = ""
    engine_version: str = ""
    candidates: list[dict[str, Any]] = []


class ReviewRequest(BaseModel):
    decision: str
    reviewer: str
    selected_account_quickbooks_id: str = ""
    review_note: str = ""


class ReviewResponse(BaseModel):
    status: str
    account: str = ""


class RuleCreateRequest(BaseModel):
    realm_id: str
    name: str
    rule_type: str
    conditions: dict[str, Any]
    target_account_quickbooks_id: str
    precedence: int = 100
    description: str = ""


class RuleResponse(BaseModel):
    id: str
    name: str
    rule_type: str
    rule_status: str
    precedence: int
    target_account_quickbooks_id: str
    match_count: int = 0


class ReviewQueueResponse(BaseModel):
    items: list[CategorizationSummary]
    total: int
    limit: int
