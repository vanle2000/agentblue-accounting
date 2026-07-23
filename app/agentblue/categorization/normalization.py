"""Vendor and text normalization for categorization.

Deterministic, safe normalization for matching keys.
"""

from __future__ import annotations

import re
import unicodedata

# Known payment-processor prefixes to strip
_PROCESSOR_PREFIXES = re.compile(
    r"^(SQ\s*\*|PAYPAL\s*\*|ACH\s+(PAYMENT|TRANSFER)\s*[-:]?\s*|"
    r"POS\s+|DEBIT\s+|CREDIT\s+|CHECK\s+|TFR\s+|XFER\s+)",
    re.IGNORECASE,
)

# Legal suffixes to normalize
_LEGAL_SUFFIXES = re.compile(
    r",?\s*(LLC|L\.L\.C\.|INC\.?|CORP\.?|CORPORATION|CO\.?|COMPANY|"
    r"LP|LLP|PLLC|PC|PA|DBA|SOLE\s+PROPRIETOR)\s*$",
    re.IGNORECASE,
)

# Collapse whitespace
_WHITESPACE = re.compile(r"\s+")


def normalize_vendor(raw: str) -> str:
    """Normalize vendor/payee name to a deterministic matching key.

    - Unicode NFC normalize
    - casefold
    - strip legal suffixes
    - strip processor prefixes
    - collapse whitespace
    - trim
    """
    if not raw:
        return ""

    text = unicodedata.normalize("NFC", raw)
    text = text.casefold().strip()
    text = _PROCESSOR_PREFIXES.sub("", text)
    text = _LEGAL_SUFFIXES.sub("", text)
    text = _WHITESPACE.sub(" ", text).strip()
    return text


def normalize_text(raw: str) -> str:
    """Normalize description/memo text for keyword matching.

    - Unicode NFC
    - casefold
    - collapse whitespace
    - strip
    """
    if not raw:
        return ""
    text = unicodedata.normalize("NFC", raw)
    text = text.casefold().strip()
    text = _WHITESPACE.sub(" ", text)
    return text
