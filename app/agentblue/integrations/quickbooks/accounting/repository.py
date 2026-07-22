"""Accounting context repository.

Idempotent persistence for accounts, snapshots, and references.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.db.models.quickbooks_accounting import (
    QuickBooksAccount,
    QuickBooksAccountSourceSnapshot,
    QuickBooksTransactionAccountRef,
)
from agentblue.integrations.quickbooks.accounting.domain import NormalizedAccount  # noqa: TC001

logger = structlog.get_logger(__name__)


def _hash_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RecordOutcome:
    INSERTED = "inserted"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    MARKED_DELETED = "marked_deleted"


class AccountingRepository:
    """Repository for accounting context persistence."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- Source snapshots ---

    async def upsert_account_snapshot(
        self,
        realm_id: str,
        quickbooks_id: str,
        raw: dict[str, Any],
        sync_token: int,
        active: bool,
        source_deleted: bool = False,
        source_created_at: str = "",
        source_updated_at: str = "",
    ) -> str:
        """Upsert account source snapshot. Returns outcome."""
        payload_hash = _hash_payload(raw)
        now = _utcnow()

        stmt = select(QuickBooksAccountSourceSnapshot).where(
            QuickBooksAccountSourceSnapshot.realm_id == realm_id,
            QuickBooksAccountSourceSnapshot.quickbooks_id == quickbooks_id,
        )
        result = await self._session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            if (
                existing.raw_payload_hash == payload_hash
                and existing.active == active
                and existing.source_deleted == source_deleted
            ):
                existing.source_last_seen_at = now
                return RecordOutcome.UNCHANGED
            existing.sync_token = sync_token
            existing.raw_payload = raw
            existing.raw_payload_hash = payload_hash
            existing.active = active
            existing.source_deleted = source_deleted
            existing.source_created_at = source_created_at
            existing.source_updated_at = source_updated_at
            existing.source_last_seen_at = now
            return RecordOutcome.UPDATED

        snapshot = QuickBooksAccountSourceSnapshot(
            realm_id=realm_id,
            quickbooks_id=quickbooks_id,
            sync_token=sync_token,
            raw_payload=raw,
            raw_payload_hash=payload_hash,
            active=active,
            source_deleted=source_deleted,
            source_created_at=source_created_at,
            source_updated_at=source_updated_at,
            source_last_seen_at=now,
        )
        self._session.add(snapshot)
        return RecordOutcome.INSERTED

    # --- Canonical accounts ---

    async def upsert_account(
        self,
        account: NormalizedAccount,
        *,
        source_deleted: bool = False,
    ) -> str:
        """Upsert canonical account. Returns outcome.

        source_deleted is an explicit parameter — NOT derived from
        account.active. An inactive account (Active=false) is NOT deleted.
        """
        now = _utcnow()
        payload_hash = _hash_payload(account.raw_payload)

        stmt = select(QuickBooksAccount).where(
            QuickBooksAccount.realm_id == account.realm_id,
            QuickBooksAccount.quickbooks_id == account.quickbooks_id,
        )
        result = await self._session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            changed = (
                existing.sync_token != account.sync_token
                or existing.name != account.name
                or existing.active != account.active
                or existing.account_type != account.account_type
                or existing.classification != account.classification
                or existing.source_deleted != source_deleted
                or existing.parent_quickbooks_id != account.parent_quickbooks_id
            )
            if not changed:
                existing.source_last_seen_at = now
                return RecordOutcome.UNCHANGED

            existing.sync_token = account.sync_token
            existing.name = account.name
            existing.fully_qualified_name = account.fully_qualified_name
            existing.description = account.description
            existing.classification = account.classification
            existing.account_type = account.account_type
            existing.account_subtype = account.account_subtype
            existing.active = account.active
            existing.subaccount = account.subaccount
            existing.parent_quickbooks_id = account.parent_quickbooks_id
            existing.account_number = account.account_number
            existing.currency_code = account.currency_code
            existing.current_balance = account.current_balance
            existing.current_balance_with_subaccounts = account.current_balance_with_subaccounts
            existing.taxable = account.taxable
            existing.source_created_at = account.source_created_at
            existing.source_updated_at = account.source_updated_at
            existing.source_last_seen_at = now
            existing.raw_payload_hash = payload_hash
            existing.source_deleted = source_deleted
            return RecordOutcome.UPDATED

        db_account = QuickBooksAccount(
            realm_id=account.realm_id,
            quickbooks_id=account.quickbooks_id,
            sync_token=account.sync_token,
            name=account.name,
            fully_qualified_name=account.fully_qualified_name,
            description=account.description,
            classification=account.classification,
            account_type=account.account_type,
            account_subtype=account.account_subtype,
            active=account.active,
            subaccount=account.subaccount,
            parent_quickbooks_id=account.parent_quickbooks_id,
            account_number=account.account_number,
            currency_code=account.currency_code,
            current_balance=account.current_balance,
            current_balance_with_subaccounts=account.current_balance_with_subaccounts,
            taxable=account.taxable,
            source_created_at=account.source_created_at,
            source_updated_at=account.source_updated_at,
            source_last_seen_at=now,
            raw_payload_hash=payload_hash,
            source_deleted=source_deleted,
        )
        self._session.add(db_account)
        return RecordOutcome.INSERTED

    # --- Account lookup ---

    async def get_account_by_quickbooks_id(
        self, realm_id: str, quickbooks_id: str
    ) -> QuickBooksAccount | None:
        stmt = select(QuickBooksAccount).where(
            QuickBooksAccount.realm_id == realm_id,
            QuickBooksAccount.quickbooks_id == quickbooks_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_accounts_by_realm(
        self,
        realm_id: str,
        *,
        active_only: bool = False,
        include_deleted: bool = False,
        account_type: str = "",
        classification: str = "",
        name_search: str = "",
        max_results: int = 100,
    ) -> list[QuickBooksAccount]:
        """Get accounts for a realm with optional filters."""
        stmt = select(QuickBooksAccount).where(QuickBooksAccount.realm_id == realm_id)
        if active_only:
            stmt = stmt.where(QuickBooksAccount.active.is_(True))
        if not include_deleted:
            stmt = stmt.where(QuickBooksAccount.source_deleted.is_(False))
        if account_type:
            stmt = stmt.where(QuickBooksAccount.account_type == account_type)
        if classification:
            stmt = stmt.where(QuickBooksAccount.classification == classification)
        if name_search:
            stmt = stmt.where(QuickBooksAccount.name.ilike(f"%{name_search}%"))
        stmt = stmt.order_by(
            QuickBooksAccount.classification,
            QuickBooksAccount.account_type,
            QuickBooksAccount.fully_qualified_name,
            QuickBooksAccount.quickbooks_id,
        ).limit(max_results)

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_children(
        self, realm_id: str, parent_quickbooks_id: str
    ) -> list[QuickBooksAccount]:
        """Get direct children of an account."""
        stmt = select(QuickBooksAccount).where(
            QuickBooksAccount.realm_id == realm_id,
            QuickBooksAccount.parent_quickbooks_id == parent_quickbooks_id,
            QuickBooksAccount.source_deleted.is_(False),
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_root_accounts(self, realm_id: str) -> list[QuickBooksAccount]:
        """Get top-level accounts (no parent)."""
        stmt = (
            select(QuickBooksAccount)
            .where(
                QuickBooksAccount.realm_id == realm_id,
                QuickBooksAccount.parent_quickbooks_id == "",
                QuickBooksAccount.source_deleted.is_(False),
            )
            .order_by(
                QuickBooksAccount.classification,
                QuickBooksAccount.fully_qualified_name,
            )
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # --- Hierarchy ---

    async def resolve_parent_references(self, realm_id: str) -> int:
        """Resolve parent_quickbooks_id -> parent_account_id for all accounts.

        Returns count of resolved references.
        """
        stmt = select(QuickBooksAccount).where(
            QuickBooksAccount.realm_id == realm_id,
            QuickBooksAccount.parent_quickbooks_id != "",
            QuickBooksAccount.parent_account_id.is_(None),
            QuickBooksAccount.source_deleted.is_(False),
        )
        result = await self._session.execute(stmt)
        children = list(result.scalars().all())

        resolved = 0
        for child in children:
            parent = await self.get_account_by_quickbooks_id(realm_id, child.parent_quickbooks_id)
            if parent:
                child.parent_account_id = parent.id
                resolved += 1

        return resolved

    # --- Transaction account references ---

    async def upsert_transaction_account_ref(
        self,
        transaction_id: str,
        realm_id: str,
        quickbooks_account_id: str,
        reference_role: str,
        source_line_id: str = "",
    ) -> str:
        """Upsert a transaction account reference."""
        if not quickbooks_account_id:
            return RecordOutcome.UNCHANGED

        stmt = select(QuickBooksTransactionAccountRef).where(
            QuickBooksTransactionAccountRef.transaction_id == transaction_id,
            QuickBooksTransactionAccountRef.quickbooks_account_id == quickbooks_account_id,
            QuickBooksTransactionAccountRef.reference_role == reference_role,
        )
        result = await self._session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            return RecordOutcome.UNCHANGED

        # Resolve account_id if possible
        account = await self.get_account_by_quickbooks_id(realm_id, quickbooks_account_id)

        ref = QuickBooksTransactionAccountRef(
            transaction_id=transaction_id,
            realm_id=realm_id,
            quickbooks_account_id=quickbooks_account_id,
            account_id=account.id if account else None,
            reference_role=reference_role,
            source_line_id=source_line_id,
        )
        self._session.add(ref)
        return RecordOutcome.INSERTED
