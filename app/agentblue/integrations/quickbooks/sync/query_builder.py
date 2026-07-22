"""Safe QuickBooks query construction.

Builds QuickBooks Query API strings from validated domain inputs.
All entity names come from the registry; dates are formatted internally.
No arbitrary user-supplied query fragments are concatenated.
"""

from __future__ import annotations

import re
from datetime import datetime

import structlog

from agentblue.integrations.quickbooks.exceptions import (
    QuickBooksQueryConstructionError,
)
from agentblue.integrations.quickbooks.sync.domain import EntityType  # noqa: TC001
from agentblue.integrations.quickbooks.sync.registry import get_registry_entry

logger = structlog.get_logger(__name__)

# Pattern for valid date format
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def format_date(dt: datetime) -> str:
    """Format a datetime as a QuickBooks-safe date string (YYYY-MM-DD)."""
    return dt.strftime("%Y-%m-%d")


def build_backfill_query(
    entity_type: EntityType,
    start_date: str,
    end_date: str | None = None,
    start_position: int = 0,
    page_size: int = 100,
) -> str:
    """Build a safe QuickBooks query for initial backfill.

    Entity names come from the registry. Dates are validated. Position and
    page size are integers (never user-supplied strings).

    Raises:
        QuickBooksQueryConstructionError: On invalid inputs.
    """
    if not _DATE_PATTERN.match(start_date):
        raise QuickBooksQueryConstructionError(
            f"Invalid start_date format: {start_date!r}. Expected YYYY-MM-DD."
        )
    if end_date and not _DATE_PATTERN.match(end_date):
        raise QuickBooksQueryConstructionError(
            f"Invalid end_date format: {end_date!r}. Expected YYYY-MM-DD."
        )
    if page_size < 1:
        raise QuickBooksQueryConstructionError(f"page_size must be positive, got {page_size}.")

    entry = get_registry_entry(entity_type)
    qb_name = entry.quickbooks_entity_name

    where_parts: list[str] = []
    if entry.where_clause:
        where_parts.append(entry.where_clause.format(start=start_date, end=end_date or start_date))

    query_parts = [f"SELECT * FROM {qb_name}"]
    if where_parts:
        query_parts.append("WHERE " + " AND ".join(where_parts))
    query_parts.append("ORDERBY TxnDate ASC")
    query_parts.append(f"STARTPOSITION {start_position}")
    query_parts.append(f"MAXRESULTS {page_size}")

    return " ".join(query_parts)


def build_cdc_query(
    entity_types: list[EntityType],
    changed_since: str,
) -> str:
    """Build a QuickBooks CDC query.

    The CDC endpoint uses a single query string with entity names
    separated by commas.

    Raises:
        QuickBooksQueryConstructionError: On invalid inputs.
    """
    if not _DATE_PATTERN.match(changed_since):
        raise QuickBooksQueryConstructionError(
            f"Invalid changed_since format: {changed_since!r}. Expected YYYY-MM-DD."
        )
    if not entity_types:
        raise QuickBooksQueryConstructionError("At least one entity type is required.")

    names: list[str] = []
    for et in entity_types:
        entry = get_registry_entry(et)
        names.append(entry.quickbooks_entity_name)

    # CDC query: SELECT * FROM EntityType1, EntityType2, ... CHANGEDSINCE date
    entities = ", ".join(names)
    return f"SELECT * FROM {entities} CHANGEDSINCE '{changed_since}'"
