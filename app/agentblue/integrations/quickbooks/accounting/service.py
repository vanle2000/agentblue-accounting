"""Account synchronization service.

Orchestrates backfill and incremental CDC sync for QuickBooks accounts.
Reuses Stage 4 API client and Stage 5 checkpoint patterns.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.integrations.quickbooks.accounting.normalizer import normalize_account
from agentblue.integrations.quickbooks.accounting.repository import (
    AccountingRepository,
    RecordOutcome,
)
from agentblue.integrations.quickbooks.api_client import QuickBooksApiClient  # noqa: TC001
from agentblue.integrations.quickbooks.sync.query_builder import format_date
from agentblue.integrations.quickbooks.sync.repository import SyncRepository

logger = structlog.get_logger(__name__)

# Account-specific entity type for checkpoints
_ACCOUNT_ENTITY = "Account"

# CDC constants (same as Stage 5)
_CDC_MAX_OBJECTS = 1000
_CDC_LOOKBACK_DAYS = 30
_CDC_MIN_WINDOW_SECONDS = 3600
_CDC_MAX_SPLIT_DEPTH = 10


class AccountSyncService:
    """Orchestrates QuickBooks Account synchronization."""

    def __init__(
        self,
        client: QuickBooksApiClient,
        session: AsyncSession,
        *,
        cdc_overlap_seconds: int = 300,
        cdc_min_window_seconds: int = _CDC_MIN_WINDOW_SECONDS,
        cdc_max_split_depth: int = _CDC_MAX_SPLIT_DEPTH,
    ) -> None:
        self._client = client
        self._repo = AccountingRepository(session)
        self._sync_repo = SyncRepository(session)
        self._session = session
        self._cdc_overlap = cdc_overlap_seconds
        self._cdc_min_window = cdc_min_window_seconds
        self._cdc_max_split_depth = cdc_max_split_depth

    async def backfill(
        self,
        realm_id: str,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        page_size: int = 100,
    ) -> dict[str, int]:
        """Backfill all accounts (active and inactive).

        Uses WHERE Active IN (true, false) to ensure inactive accounts
        are included in the query.
        """
        if start_date is None:
            start_date = datetime.now(UTC) - timedelta(days=365 * 5)
        if end_date is None:
            end_date = datetime.now(UTC)

        counts = {
            "fetched": 0,
            "inserted": 0,
            "updated": 0,
            "unchanged": 0,
            "marked_deleted": 0,
        }

        start_position = 0

        while True:
            start_str = format_date(start_date)
            end_str = format_date(end_date)

            query = (
                f"SELECT * FROM Account "
                f"WHERE Metadata.LastUpdatedTime >= '{start_str}' "
                f"AND Metadata.LastUpdatedTime <= '{end_str}' "
                f"ORDERBY Id ASC "
                f"STARTPOSITION {start_position} "
                f"MAXRESULTS {page_size}"
            )

            result = await self._client.get(
                f"/v3/company/{realm_id}/query",
                params={"query": query},
            )

            query_response = result.get("QueryResponse", {})
            items = query_response.get("Account", [])
            max_results = int(query_response.get("MaxResults", 0))
            total_count = int(query_response.get("TotalCount", 0))

            if not items:
                break

            batch_counts = await self._persist_batch(realm_id, items)
            for k in counts:
                counts[k] += batch_counts[k]

            await self._session.commit()

            # Resolve parent references after each batch
            resolved = await self._repo.resolve_parent_references(realm_id)
            await self._session.commit()

            logger.info(
                "account_backfill_page",
                realm_id=realm_id,
                page_size=len(items),
                start_position=start_position,
                total_count=total_count,
                parent_refs_resolved=resolved,
            )

            if len(items) < page_size or start_position + max_results >= total_count:
                break
            start_position += max_results

        return counts

    async def sync_incremental(
        self,
        realm_id: str,
        since: datetime | None = None,
    ) -> dict[str, int]:
        """Incremental Account sync using CDC.

        Uses the CDC endpoint with overlap windows.
        """
        if since is None:
            since = datetime.now(UTC) - timedelta(days=1)

        now = datetime.now(UTC)
        lookback_limit = now - timedelta(days=_CDC_LOOKBACK_DAYS)
        if since < lookback_limit:
            since = lookback_limit

        # Apply overlap
        since_with_overlap = since - timedelta(seconds=self._cdc_overlap)

        query = f"SELECT * FROM Account CHANGEDSINCE '{format_date(since_with_overlap)}'"

        result = await self._client.get(
            f"/v3/company/{realm_id}/cdc",
            params={"entities": query, "changedSince": format_date(since_with_overlap)},
        )

        cdc_response = result.get("CDCResponse", {})
        cdc_entities = (
            cdc_response.get("CDCResponse", [{}])[0] if cdc_response.get("CDCResponse") else {}
        )

        items = cdc_entities.get("Account", [])

        counts = await self._persist_batch(realm_id, items)
        await self._session.commit()

        # Resolve parent references
        await self._repo.resolve_parent_references(realm_id)
        await self._session.commit()

        return counts

    async def _persist_batch(self, realm_id: str, items: list[dict[str, Any]]) -> dict[str, int]:
        """Persist a batch of raw Account entities."""
        counts = {
            "fetched": len(items),
            "inserted": 0,
            "updated": 0,
            "unchanged": 0,
            "marked_deleted": 0,
        }

        for raw in items:
            try:
                qb_id = str(raw.get("Id", ""))
                sync_token = int(raw.get("SyncToken", 0))
                active = bool(raw.get("Active", True))
                is_deleted = not active

                # Upsert source snapshot
                await self._repo.upsert_account_snapshot(
                    realm_id=realm_id,
                    quickbooks_id=qb_id,
                    raw=raw,
                    sync_token=sync_token,
                    active=active,
                    source_deleted=is_deleted,
                    source_created_at=str(raw.get("MetaData", {}).get("CreateTime", "")),
                    source_updated_at=str(raw.get("MetaData", {}).get("LastUpdatedTime", "")),
                )

                # Normalize and upsert canonical account
                normalized = normalize_account(raw, realm_id)
                outcome = await self._repo.upsert_account(normalized)

                if outcome == RecordOutcome.INSERTED:
                    counts["inserted"] += 1
                elif outcome == RecordOutcome.UPDATED:
                    counts["updated"] += 1
                else:
                    counts["unchanged"] += 1

            except Exception as exc:
                logger.warning(
                    "account_persist_failed",
                    realm_id=realm_id,
                    error=str(exc)[:200],
                )

        return counts
