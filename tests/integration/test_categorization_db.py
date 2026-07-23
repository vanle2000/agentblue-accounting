"""PostgreSQL integration tests for categorization (Stage 7).

Tests real database constraints, locking, and persistence.
Requires Docker Compose PostgreSQL on localhost:5433.

Run with: pytest -m integration tests/integration/test_categorization_db.py
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.categorization.models import (
    CategorizationDecision,
    CategorizationRecommendation,
    QuickBooksCategorizationApplication,
    TransactionCategorization,
)
from agentblue.db.session import get_session_factory

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
]


@pytest.fixture
async def db_session() -> AsyncSession:
    factory = get_session_factory()
    async with factory() as session:
        yield session
        await session.rollback()


def _unique_realm() -> str:
    return f"realm-{uuid.uuid4().hex[:8]}"


async def _create_cat(
    session: AsyncSession, realm: str, qb_id: str = "QB1"
) -> TransactionCategorization:
    cat = TransactionCategorization(
        realm_id=realm,
        transaction_id=str(uuid.uuid4()),
        transaction_quickbooks_id=qb_id,
        status="APPROVED",
    )
    session.add(cat)
    await session.flush()
    return cat


# ---------------------------------------------------------------------------
# A. Idempotency uniqueness
# ---------------------------------------------------------------------------


class TestIdempotency:
    async def test_duplicate_idempotency_key_rejected(self, db_session: AsyncSession) -> None:
        realm = _unique_realm()
        key = f"key-{uuid.uuid4().hex[:8]}"

        cat1 = await _create_cat(db_session, realm, "QB1")
        cat2 = await _create_cat(db_session, realm, "QB2")

        app1 = QuickBooksCategorizationApplication(
            categorization_id=cat1.id,
            realm_id=realm,
            transaction_id=cat1.transaction_id,
            transaction_quickbooks_id="QB1",
            transaction_type="Purchase",
            selected_account_quickbooks_id="ACCT1",
            idempotency_key=key,
            approved_by="reviewer",
            approved_at=datetime.now(UTC),
        )
        db_session.add(app1)
        await db_session.flush()

        app2 = QuickBooksCategorizationApplication(
            categorization_id=cat2.id,
            realm_id=realm,
            transaction_id=cat2.transaction_id,
            transaction_quickbooks_id="QB2",
            transaction_type="Purchase",
            selected_account_quickbooks_id="ACCT2",
            idempotency_key=key,
            approved_by="reviewer",
            approved_at=datetime.now(UTC),
        )
        db_session.add(app2)
        with pytest.raises(Exception, match="uq_application_idempotency_key|UniqueViolation"):
            await db_session.flush()


# ---------------------------------------------------------------------------
# B. Current categorization uniqueness
# ---------------------------------------------------------------------------


class TestCategorizationUniqueness:
    async def test_duplicate_transaction_rejected(self, db_session: AsyncSession) -> None:
        realm = _unique_realm()
        txn_id = str(uuid.uuid4())

        cat1 = TransactionCategorization(
            realm_id=realm,
            transaction_id=txn_id,
            transaction_quickbooks_id="QB1",
            status="PENDING",
        )
        db_session.add(cat1)
        await db_session.flush()

        cat2 = TransactionCategorization(
            realm_id=realm,
            transaction_id=txn_id,
            transaction_quickbooks_id="QB1",
            status="PENDING",
        )
        db_session.add(cat2)
        with pytest.raises(Exception, match="uq_categorization_transaction|UniqueViolation"):
            await db_session.flush()

    async def test_same_txn_different_realm_allowed(self, db_session: AsyncSession) -> None:
        txn_id = str(uuid.uuid4())
        for realm in [_unique_realm(), _unique_realm()]:
            cat = TransactionCategorization(
                realm_id=realm,
                transaction_id=txn_id,
                transaction_quickbooks_id="QB1",
                status="PENDING",
            )
            db_session.add(cat)
        await db_session.flush()


# ---------------------------------------------------------------------------
# C. Recommendation uniqueness
# ---------------------------------------------------------------------------


class TestRecommendationUniqueness:
    async def test_duplicate_account_rejected(self, db_session: AsyncSession) -> None:
        realm = _unique_realm()
        cat = await _create_cat(db_session, realm)

        rec1 = CategorizationRecommendation(
            categorization_id=cat.id,
            realm_id=realm,
            account_quickbooks_id="ACCT1",
            rank=1,
            score=Decimal("0.95"),
            confidence_band="HIGH",
            recommendation_source="USER_RULE",
            explanation={},
            feature_snapshot={},
        )
        db_session.add(rec1)
        await db_session.flush()

        rec2 = CategorizationRecommendation(
            categorization_id=cat.id,
            realm_id=realm,
            account_quickbooks_id="ACCT1",
            rank=2,
            score=Decimal("0.80"),
            confidence_band="MEDIUM",
            recommendation_source="KEYWORD_RULE",
            explanation={},
            feature_snapshot={},
        )
        db_session.add(rec2)
        with pytest.raises(Exception, match="uq_rec_categorization_account|UniqueViolation"):
            await db_session.flush()


# ---------------------------------------------------------------------------
# D. Numeric precision
# ---------------------------------------------------------------------------


class TestNumericPrecision:
    async def test_score_precision(self, db_session: AsyncSession) -> None:
        realm = _unique_realm()
        scores = [Decimal("0.000"), Decimal("0.970"), Decimal("1.000")]
        for score in scores:
            cat = TransactionCategorization(
                realm_id=realm,
                transaction_id=str(uuid.uuid4()),
                transaction_quickbooks_id=f"QB-{score}",
                status="RECOMMENDED",
                confidence_score=score,
            )
            db_session.add(cat)
        await db_session.flush()

        stmt = select(TransactionCategorization).where(TransactionCategorization.realm_id == realm)
        result = await db_session.execute(stmt)
        stored = sorted([c.confidence_score for c in result.scalars().all()])
        assert stored == sorted(scores)


# ---------------------------------------------------------------------------
# E. JSONB persistence
# ---------------------------------------------------------------------------


class TestJsonBPersistence:
    async def test_explanation_roundtrip(self, db_session: AsyncSession) -> None:
        realm = _unique_realm()
        explanation = {
            "summary": "Matched vendor rule",
            "reason_codes": ["USER_RULE_MATCH"],
            "score_components": {"user_rule": "0.55"},
        }
        cat = await _create_cat(db_session, realm)

        rec = CategorizationRecommendation(
            categorization_id=cat.id,
            realm_id=realm,
            account_quickbooks_id="ACCT1",
            rank=1,
            score=Decimal("0.95"),
            confidence_band="HIGH",
            recommendation_source="USER_RULE",
            explanation=explanation,
            feature_snapshot={"type": "Purchase"},
        )
        db_session.add(rec)
        await db_session.flush()

        stmt = select(CategorizationRecommendation).where(
            CategorizationRecommendation.realm_id == realm
        )
        result = await db_session.execute(stmt)
        loaded = result.scalar_one()
        assert loaded.explanation["summary"] == "Matched vendor rule"
        assert loaded.feature_snapshot["type"] == "Purchase"


# ---------------------------------------------------------------------------
# F. Decision append-only
# ---------------------------------------------------------------------------


class TestDecisionAppendOnly:
    async def test_decisions_accumulate(self, db_session: AsyncSession) -> None:
        realm = _unique_realm()
        cat = await _create_cat(db_session, realm)

        for decision in ["DEFER", "APPROVE"]:
            dec = CategorizationDecision(
                categorization_id=cat.id,
                realm_id=realm,
                decision=decision,
                reviewer="accountant",
                engine_version="1.0.0",
                recommendation_snapshot={},
            )
            db_session.add(dec)
        await db_session.flush()

        stmt = select(CategorizationDecision).where(
            CategorizationDecision.categorization_id == cat.id
        )
        result = await db_session.execute(stmt)
        assert len(list(result.scalars().all())) == 2


# ---------------------------------------------------------------------------
# G. Realm isolation
# ---------------------------------------------------------------------------


class TestRealmIsolation:
    async def test_cross_realm_not_visible(self, db_session: AsyncSession) -> None:
        realm_a = _unique_realm()
        realm_b = _unique_realm()
        cat = await _create_cat(db_session, realm_a)

        stmt = select(TransactionCategorization).where(
            TransactionCategorization.realm_id == realm_b,
            TransactionCategorization.id == cat.id,
        )
        result = await db_session.execute(stmt)
        assert result.scalar_one_or_none() is None
