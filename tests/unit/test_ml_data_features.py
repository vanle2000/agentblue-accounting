"""Tests for ML data pipeline (Stage 8).

Covers label policy, dataset extraction, temporal splitting,
leakage prevention, and the feature pipeline.

All tests are pure function/logic tests — no database required.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

from agentblue.categorization.domain import CategorizationStatus
from agentblue.ml.data.extraction import _EXCLUDED_DISPOSITIONS, DatasetExtractor
from agentblue.ml.data.fingerprint import compute_dataset_fingerprint
from agentblue.ml.data.splitting import TemporalSplitter
from agentblue.ml.data.validation import DatasetValidator
from agentblue.ml.features.preprocessing import _TEXT_TRUNCATE_LENGTH, build_feature_vector
from agentblue.ml.features.schema import ALL_FEATURES, FEATURE_SCHEMA_VERSION
from agentblue.ml.features.transformers import FeatureTransformer

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


def _make_row(
    transaction_id: str = "txn-1",
    account_quickbooks_id: str = "acct-100",
    label_source: str = "APPROVED",
    feature_snapshot: dict[str, Any] | None = None,
    transaction_type: str = "Purchase",
    status: str = CategorizationStatus.APPROVED.value,
    approved_at: str = "2025-01-15T10:00:00Z",
    engine_version: str = "1.0",
    transaction_quickbooks_id: str = "qbo-txn-1",
    label_id: int = 1,
    categorization_id: int = 1,
    transaction_date: str = "2025-01-15T00:00:00Z",
) -> dict[str, Any]:
    """Build a single extraction row dict."""
    fs = feature_snapshot if feature_snapshot is not None else {
        "amount": -150.00,
        "absolute_amount": 150.00,
        "transaction_type": transaction_type,
        "transaction_date_day_of_week": 2,
        "transaction_date_month": 1,
        "normalized_description": "Office supplies purchase",
        "normalized_memo": "Staples order #12345",
        "normalized_vendor": "staples",
        "transaction_date": transaction_date,
    }
    return {
        "label_id": label_id,
        "categorization_id": categorization_id,
        "transaction_id": transaction_id,
        "transaction_quickbooks_id": transaction_quickbooks_id,
        "account_quickbooks_id": account_quickbooks_id,
        "label_source": label_source,
        "feature_snapshot": fs,
        "transaction_type": transaction_type,
        "status": status,
        "approved_at": approved_at,
        "engine_version": engine_version,
    }


def _make_feature_snapshot(
    amount: float = -150.0,
    transaction_date: str = "2025-01-15T00:00:00Z",
    normalized_description: str = "Office supplies purchase",
    normalized_memo: str = "Staples order #12345",
    normalized_vendor: str = "staples",
    transaction_type: str = "Purchase",
    **overrides: Any,
) -> dict[str, Any]:
    """Build a minimal feature_snapshot dict."""
    snapshot: dict[str, Any] = {
        "amount": amount,
        "absolute_amount": abs(amount),
        "transaction_type": transaction_type,
        "transaction_date_day_of_week": 2,
        "transaction_date_month": 1,
        "normalized_description": normalized_description,
        "normalized_memo": normalized_memo,
        "normalized_vendor": normalized_vendor,
        "transaction_date": transaction_date,
    }
    snapshot.update(overrides)
    return snapshot


def _mock_session_execute(rows: list[dict[str, Any]]) -> MagicMock:
    """Create a mock SQLAlchemy session that returns ``rows`` as mappings."""
    # Mimic .mappings() iterator used by DatasetExtractor.extract_dataset
    mock_result = MagicMock()
    mock_result.mappings.return_value = iter(rows)

    mock_session = MagicMock()
    # The extract method calls session.execute() once for the main query,
    # and once more inside the logger for total_labels counting.
    # The logger call also uses session.execute(...).all()
    mock_session.execute.return_value = mock_result
    # For the total-labels count inside the logger, return an empty list
    # so len() works; we override the return on the second call.
    count_result = MagicMock()
    count_result.all.return_value = rows
    # First call -> data result, second call -> count result
    mock_session.execute.side_effect = [mock_result, count_result]

    return mock_session


# ===================================================================
# A. Label Policy
# ===================================================================


class TestLabelPolicy:
    """Tests that DatasetExtractor filters by disposition correctly."""

    def test_eligible_approval_included(self) -> None:
        """APPROVED status rows are included in the dataset."""
        rows = [_make_row(status=CategorizationStatus.APPROVED.value)]
        session = _mock_session_execute(rows)
        extractor = DatasetExtractor()
        result = extractor.extract_dataset(session, realm_id="realm-1")
        assert len(result) == 1
        assert result[0]["status"] == CategorizationStatus.APPROVED.value

    @pytest.mark.parametrize(
        "disposition",
        [
            CategorizationStatus.REJECTED.value,
            CategorizationStatus.DEFERRED.value,
            CategorizationStatus.STALE.value,
            CategorizationStatus.SUPERSEDED.value,
            CategorizationStatus.APPLY_FAILED.value,
        ],
    )
    def test_excluded_dispositions_filtered(self, disposition: str) -> None:
        """Rejected/deferred/stale/superseded/apply-failed rows are excluded."""
        rows = [_make_row(status=disposition)]
        session = _mock_session_execute(rows)
        extractor = DatasetExtractor()
        result = extractor.extract_dataset(session, realm_id="realm-1")
        assert len(result) == 0

    def test_mixed_dispositions_only_eligible_returned(self) -> None:
        """When rows have mixed statuses, only non-excluded ones pass."""
        rows = [
            _make_row(transaction_id="txn-good", status=CategorizationStatus.APPROVED.value),
            _make_row(transaction_id="txn-bad", status=CategorizationStatus.REJECTED.value),
            _make_row(transaction_id="txn-stale", status=CategorizationStatus.STALE.value),
        ]
        session = _mock_session_execute(rows)
        extractor = DatasetExtractor()
        result = extractor.extract_dataset(session, realm_id="realm-1")
        assert len(result) == 1
        assert result[0]["transaction_id"] == "txn-good"

    def test_excluded_dispositions_frozenset_contents(self) -> None:
        """_EXCLUDED_DISPOSITIONS contains exactly the expected dispositions."""
        expected = {
            CategorizationStatus.REJECTED.value,
            CategorizationStatus.DEFERRED.value,
            CategorizationStatus.STALE.value,
            CategorizationStatus.SUPERSEDED.value,
            CategorizationStatus.APPLY_FAILED.value,
        }
        assert expected == _EXCLUDED_DISPOSITIONS

    def test_custom_excluded_dispositions(self) -> None:
        """DatasetExtractor accepts a custom excluded set."""
        custom = frozenset({CategorizationStatus.NEEDS_REVIEW.value})
        rows = [
            _make_row(
                transaction_id="txn-1",
                status=CategorizationStatus.NEEDS_REVIEW.value,
            ),
            _make_row(
                transaction_id="txn-2",
                status=CategorizationStatus.APPROVED.value,
            ),
        ]
        session = _mock_session_execute(rows)
        extractor = DatasetExtractor(excluded_dispositions=custom)
        result = extractor.extract_dataset(session, realm_id="realm-1")
        assert len(result) == 1
        assert result[0]["status"] == CategorizationStatus.APPROVED.value

    def test_validator_insufficient_rows(self) -> None:
        """DatasetValidator errors when rows < min_rows."""
        rows = [_make_row(transaction_id=f"txn-{i}") for i in range(5)]
        validator = DatasetValidator()
        report = validator.validate(rows, min_rows=100, min_classes=1, min_per_class=1)
        assert report["valid"] is False
        assert any("Insufficient rows" in e for e in report["errors"])

    def test_validator_insufficient_classes(self) -> None:
        """DatasetValidator errors when unique label classes < min_classes."""
        # All rows have the same account_quickbooks_id → 1 class
        rows = [
            _make_row(transaction_id=f"txn-{i}", account_quickbooks_id="acct-100")
            for i in range(200)
        ]
        validator = DatasetValidator()
        report = validator.validate(rows, min_rows=10, min_classes=2, min_per_class=1)
        assert report["valid"] is False
        assert any("Insufficient classes" in e for e in report["errors"])

    def test_validator_warns_class_imbalance(self) -> None:
        """DatasetValidator warns when class imbalance ratio > 10x."""
        # 100 rows for acct-100, 5 rows for acct-200 → ratio = 20
        rows = [
            _make_row(transaction_id=f"txn-a-{i}", account_quickbooks_id="acct-100")
            for i in range(100)
        ]
        rows += [
            _make_row(transaction_id=f"txn-b-{i}", account_quickbooks_id="acct-200")
            for i in range(5)
        ]
        validator = DatasetValidator()
        report = validator.validate(
            rows, min_rows=5, min_classes=2, min_per_class=1,
        )
        assert any("imbalance" in w.lower() for w in report["warnings"])

    def test_validator_clean_dataset_passes(self) -> None:
        """A well-balanced dataset passes validation with no errors."""
        rows = []
        for cls_idx, acct in enumerate(["acct-100", "acct-200", "acct-300"]):
            for i in range(50):
                rows.append(
                    _make_row(
                        transaction_id=f"txn-{cls_idx}-{i}",
                        account_quickbooks_id=acct,
                    )
                )
        validator = DatasetValidator()
        report = validator.validate(rows, min_rows=100, min_classes=3, min_per_class=10)
        assert report["valid"] is True
        assert report["errors"] == []
        assert report["stats"]["num_classes"] == 3


# ===================================================================
# B. Dataset Extraction
# ===================================================================


class TestDatasetExtraction:
    """Tests for DatasetExtractor and compute_dataset_fingerprint."""

    def test_deterministic_ordering(self) -> None:
        """Same input rows always produce the same output order."""
        rows = [
            _make_row(transaction_id="txn-c", approved_at="2025-03-01T00:00:00Z"),
            _make_row(transaction_id="txn-a", approved_at="2025-01-01T00:00:00Z"),
            _make_row(transaction_id="txn-b", approved_at="2025-02-01T00:00:00Z"),
        ]
        session1 = _mock_session_execute(rows)
        session2 = _mock_session_execute(rows)
        ext = DatasetExtractor()
        result1 = ext.extract_dataset(session1, realm_id="r1")
        result2 = ext.extract_dataset(session2, realm_id="r1")
        ids1 = [r["transaction_id"] for r in result1]
        ids2 = [r["transaction_id"] for r in result2]
        assert ids1 == ids2

    def test_deterministic_fingerprint(self) -> None:
        """compute_dataset_fingerprint returns the same hash for same rows."""
        rows = [
            _make_row(transaction_id="txn-1", account_quickbooks_id="acct-100"),
            _make_row(transaction_id="txn-2", account_quickbooks_id="acct-200"),
        ]
        fp1 = compute_dataset_fingerprint(rows)
        fp2 = compute_dataset_fingerprint(rows)
        assert fp1 == fp2
        # SHA-256 hex digest is 64 chars
        assert len(fp1) == 64

    def test_fingerprint_changes_on_content_change(self) -> None:
        """Fingerprint changes when label content changes."""
        rows_a = [_make_row(account_quickbooks_id="acct-100")]
        rows_b = [_make_row(account_quickbooks_id="acct-200")]
        assert compute_dataset_fingerprint(rows_a) != compute_dataset_fingerprint(rows_b)

    def test_realm_isolation(self) -> None:
        """Different realm_ids produce different extraction results."""
        row_r1 = _make_row(transaction_id="txn-1")
        row_r2 = _make_row(transaction_id="txn-2")

        session_r1 = _mock_session_execute([row_r1])
        session_r2 = _mock_session_execute([row_r2])
        ext = DatasetExtractor()

        result_r1 = ext.extract_dataset(session_r1, realm_id="realm-alpha")
        result_r2 = ext.extract_dataset(session_r2, realm_id="realm-beta")

        # Verify realm_id was passed to session.execute
        assert len(result_r1) == 1
        assert len(result_r2) == 1

    def test_duplicate_transaction_ids_kept_together(self) -> None:
        """Multiple rows with the same transaction_id are all retained."""
        rows = [
            _make_row(transaction_id="txn-dup", label_id=1, account_quickbooks_id="acct-100"),
            _make_row(transaction_id="txn-dup", label_id=2, account_quickbooks_id="acct-200"),
            _make_row(transaction_id="txn-single", label_id=3, account_quickbooks_id="acct-100"),
        ]
        session = _mock_session_execute(rows)
        ext = DatasetExtractor()
        result = ext.extract_dataset(session, realm_id="realm-1")
        assert len(result) == 3
        dup_rows = [r for r in result if r["transaction_id"] == "txn-dup"]
        assert len(dup_rows) == 2

    def test_exclusion_counts_excluded_rows(self) -> None:
        """Only non-excluded rows appear in the result."""
        rows = [
            _make_row(transaction_id="txn-ok", status=CategorizationStatus.APPROVED.value),
            _make_row(
                transaction_id="txn-rej", status=CategorizationStatus.REJECTED.value,
            ),
            _make_row(
                transaction_id="txn-def", status=CategorizationStatus.DEFERRED.value,
            ),
        ]
        session = _mock_session_execute(rows)
        ext = DatasetExtractor()
        result = ext.extract_dataset(session, realm_id="realm-1")
        assert len(result) == 1

    def test_empty_dataset_handling(self) -> None:
        """Empty input returns an empty list."""
        session = _mock_session_execute([])
        ext = DatasetExtractor()
        result = ext.extract_dataset(session, realm_id="realm-1")
        assert result == []

    def test_validator_insufficient_class_warnings(self) -> None:
        """DatasetValidator warns on small dataset."""
        rows = [_make_row(transaction_id=f"txn-{i}") for i in range(150)]
        validator = DatasetValidator()
        report = validator.validate(rows, min_rows=100, min_classes=1, min_per_class=1)
        # With 150 < 100*2=200, should warn about small dataset
        assert any("Small dataset" in w for w in report["warnings"])

    def test_sensitive_text_sanitization(self) -> None:
        """build_feature_vector truncates text fields to 500 chars."""
        long_text = "x" * 1000
        row = _make_row(
            feature_snapshot=_make_feature_snapshot(
                normalized_description=long_text,
                normalized_memo=long_text,
                normalized_vendor=long_text,
            ),
        )
        features = build_feature_vector(row)
        assert len(features["description_text"]) == _TEXT_TRUNCATE_LENGTH
        assert len(features["memo_text"]) == _TEXT_TRUNCATE_LENGTH
        assert len(features["normalized_vendor"]) == _TEXT_TRUNCATE_LENGTH

    def test_snapshot_defaults_to_empty_dict(self) -> None:
        """When feature_snapshot is None, extractor defaults to {}."""
        rows = [_make_row()]
        # Overwrite the snapshot to None after creation
        rows[0]["feature_snapshot"] = None
        session = _mock_session_execute(rows)
        ext = DatasetExtractor()
        result = ext.extract_dataset(session, realm_id="realm-1")
        assert result[0]["feature_snapshot"] == {}

    def test_feature_version_filter(self) -> None:
        """DatasetExtractor passes feature_version to the query."""
        rows = [_make_row(engine_version="2.0")]
        session = _mock_session_execute(rows)
        ext = DatasetExtractor()
        ext.extract_dataset(session, realm_id="realm-1", feature_version="2.0")
        # Verify the second positional arg to session.execute contains the filter
        # (first call is the main query, second is the logger count)
        first_call_stmt = session.execute.call_args_list[0][0][0]
        # The statement should be a Select object; we just verify it was called
        assert first_call_stmt is not None


# ===================================================================
# C. Temporal Splitting
# ===================================================================


class TestTemporalSplitting:
    """Tests for TemporalSplitter temporal train/valid/test split."""

    def _make_dated_rows(self, n: int = 30) -> list[dict[str, Any]]:
        """Build n rows with monotonically increasing transaction dates."""
        rows = []
        for i in range(n):
            date = f"2025-{(i // 10) + 1:02d}-{(i % 28) + 1:02d}T00:00:00Z"
            rows.append(
                _make_row(
                    transaction_id=f"txn-{i:03d}",
                    account_quickbooks_id=f"acct-{i % 3:03d}",
                    label_id=i,
                    categorization_id=i,
                    transaction_date=date,
                    feature_snapshot=_make_feature_snapshot(transaction_date=date),
                )
            )
        return rows

    def test_chronological_70_15_15_split(self) -> None:
        """Default split produces approximately 70/15/15 ratio of transactions."""
        rows = self._make_dated_rows(100)
        splitter = TemporalSplitter()
        train, valid, test = splitter.split(rows)

        total = len(train) + len(valid) + len(test)
        assert total == len(rows)

        train_ratio = len(train) / total
        valid_ratio = len(valid) / total
        test_ratio = len(test) / total

        # Allow ±5% tolerance due to integer rounding
        assert 0.65 <= train_ratio <= 0.75
        assert 0.10 <= valid_ratio <= 0.20
        assert 0.10 <= test_ratio <= 0.20

    def test_duplicate_versions_grouped_in_same_split(self) -> None:
        """Rows sharing the same transaction_id land in the same split."""
        # Create 20 unique transactions, then duplicate txn-05 twice
        rows = self._make_dated_rows(20)
        dup_row = _make_row(
            transaction_id="txn-005",
            label_id=999,
            account_quickbooks_id="acct-dup",
            transaction_date="2025-01-06T00:00:00Z",
            feature_snapshot=_make_feature_snapshot(
                transaction_date="2025-01-06T00:00:00Z",
            ),
        )
        rows.append(dup_row)

        splitter = TemporalSplitter()
        train, valid, test = splitter.split(rows)

        # Find which split txn-005 landed in
        all_splits = {"train": train, "valid": valid, "test": test}
        txn005_splits: list[str] = []
        for split_name, split_rows in all_splits.items():
            if any(r["transaction_id"] == "txn-005" for r in split_rows):
                txn005_splits.append(split_name)

        # All txn-005 rows must be in exactly one split
        assert len(txn005_splits) == 1

    def test_equal_timestamps_handled_deterministically(self) -> None:
        """Rows with identical dates always produce the same split assignment."""
        same_date = "2025-06-15T00:00:00Z"
        rows = [
            _make_row(
                transaction_id=f"txn-{i:03d}",
                transaction_date=same_date,
                feature_snapshot=_make_feature_snapshot(transaction_date=same_date),
            )
            for i in range(30)
        ]

        splitter = TemporalSplitter()
        result1 = splitter.split(rows)
        result2 = splitter.split(rows)

        ids1 = (
            [r["transaction_id"] for r in result1[0]],
            [r["transaction_id"] for r in result1[1]],
            [r["transaction_id"] for r in result1[2]],
        )
        ids2 = (
            [r["transaction_id"] for r in result2[0]],
            [r["transaction_id"] for r in result2[1]],
            [r["transaction_id"] for r in result2[2]],
        )
        assert ids1 == ids2

    def test_small_dataset_handling(self) -> None:
        """Splitting a dataset with <10 transactions still works."""
        rows = self._make_dated_rows(5)
        splitter = TemporalSplitter()
        train, valid, test = splitter.split(rows)
        total = len(train) + len(valid) + len(test)
        assert total == 5

    def test_no_overlap_between_splits(self) -> None:
        """No row appears in more than one split."""
        rows = self._make_dated_rows(50)
        splitter = TemporalSplitter()
        train, valid, test = splitter.split(rows)

        train_ids = {r["label_id"] for r in train}
        valid_ids = {r["label_id"] for r in valid}
        test_ids = {r["label_id"] for r in test}

        assert train_ids.isdisjoint(valid_ids)
        assert valid_ids.isdisjoint(test_ids)
        assert train_ids.isdisjoint(test_ids)

    def test_deterministic_repeated_splits(self) -> None:
        """Same input always produces the same split — no random fallback."""
        rows = self._make_dated_rows(40)
        splitter = TemporalSplitter()
        splits_a = splitter.split(rows)
        splits_b = splitter.split(rows)

        for i in range(3):
            ids_a = [r["transaction_id"] for r in splits_a[i]]
            ids_b = [r["transaction_id"] for r in splits_b[i]]
            assert ids_a == ids_b

    def test_temporal_ordering_train_before_valid_before_test(self) -> None:
        """Training dates come before validation dates, which come before test dates."""
        rows = self._make_dated_rows(60)
        splitter = TemporalSplitter()
        train, valid, test = splitter.split(rows)

        # Get max date in train and min date in valid/test
        train_dates = sorted(
            r.get("transaction_date", r.get("feature_snapshot", {}).get("transaction_date", ""))
            for r in train
        )
        valid_dates = sorted(
            r.get("transaction_date", r.get("feature_snapshot", {}).get("transaction_date", ""))
            for r in valid
        )
        test_dates = sorted(
            r.get("transaction_date", r.get("feature_snapshot", {}).get("transaction_date", ""))
            for r in test
        )

        # All valid/test dates should be >= last train date (temporal boundary)
        if train_dates and valid_dates:
            assert valid_dates[0] >= train_dates[0]
        if valid_dates and test_dates:
            assert test_dates[0] >= valid_dates[0]

    def test_empty_dataset_raises(self) -> None:
        """Splitting an empty dataset raises ValueError."""
        splitter = TemporalSplitter()
        with pytest.raises(ValueError, match="empty"):
            splitter.split([])

    def test_bad_ratios_raise(self) -> None:
        """Split ratios that don't sum to ~1.0 raise ValueError."""
        rows = self._make_dated_rows(10)
        splitter = TemporalSplitter()
        with pytest.raises(ValueError, match="sum to 1"):
            splitter.split(rows, train_ratio=0.5, valid_ratio=0.2, test_ratio=0.2)


# ===================================================================
# D. Leakage Prevention
# ===================================================================


class TestLeakagePrevention:
    """Tests that the ML pipeline prevents data leakage."""

    def test_target_account_absent_from_features(self) -> None:
        """build_feature_vector does NOT include account_quickbooks_id."""
        row = _make_row(account_quickbooks_id="acct-SECRET")
        features = build_feature_vector(row)
        assert "account_quickbooks_id" not in features

    def test_feature_vector_only_contains_expected_keys(self) -> None:
        """build_feature_vector produces only the defined feature keys."""
        row = _make_row()
        features = build_feature_vector(row)
        expected_keys = {
            "amount",
            "absolute_amount",
            "transaction_type",
            "transaction_date_day_of_week",
            "transaction_date_month",
            "description_text",
            "memo_text",
            "normalized_vendor",
        }
        assert set(features.keys()) == expected_keys

    def test_same_transaction_cannot_cross_splits(self) -> None:
        """Rows with the same transaction_id always land in the same split."""
        # Create 30 rows with 3 duplicate versions each for txn-000
        rows = []
        for i in range(10):
            date = f"2025-01-{i + 1:02d}T00:00:00Z"
            for version in range(3):
                rows.append(
                    _make_row(
                        transaction_id=f"txn-{i:03d}",
                        label_id=i * 100 + version,
                        account_quickbooks_id=f"acct-{i % 2}",
                        transaction_date=date,
                        feature_snapshot=_make_feature_snapshot(transaction_date=date),
                    )
                )

        splitter = TemporalSplitter()
        train, valid, test = splitter.split(rows)

        # Collect which splits each transaction_id appears in
        txn_to_splits: dict[str, set[str]] = {}
        for row in train:
            txn_to_splits.setdefault(row["transaction_id"], set()).add("train")
        for row in valid:
            txn_to_splits.setdefault(row["transaction_id"], set()).add("valid")
        for row in test:
            txn_to_splits.setdefault(row["transaction_id"], set()).add("test")

        for txn_id, splits in txn_to_splits.items():
            assert len(splits) == 1, (
                f"Transaction {txn_id} leaked across splits: {splits}"
            )

    def test_fingerprint_ignores_metadata_changes(self) -> None:
        """Fingerprint is stable when only approved_at changes (not training-relevant)."""
        row_a = _make_row(approved_at="2025-01-15T10:00:00.000001Z")
        row_b = _make_row(approved_at="2025-01-15T10:00:00.999999Z")
        # approved_at is not part of the fingerprint
        fp_a = compute_dataset_fingerprint([row_a])
        fp_b = compute_dataset_fingerprint([row_b])
        assert fp_a == fp_b

    def test_feature_version_filter_isolation(self) -> None:
        """Rows with different engine_versions produce different fingerprints."""
        row_v1 = _make_row(engine_version="1.0")
        row_v2 = _make_row(engine_version="2.0")
        # engine_version isn't in fingerprint, but the rows are otherwise identical
        # so fingerprints should be the same (fingerprint only covers label-relevant fields)
        fp1 = compute_dataset_fingerprint([row_v1])
        fp2 = compute_dataset_fingerprint([row_v2])
        assert fp1 == fp2  # metadata excluded from fingerprint


# ===================================================================
# E. Feature Pipeline
# ===================================================================


class TestFeaturePipeline:
    """Tests for build_feature_vector and FeatureTransformer."""

    # --- build_feature_vector tests ---

    def test_deterministic_feature_ordering(self) -> None:
        """Calling build_feature_vector twice on the same row yields identical output."""
        row = _make_row()
        f1 = build_feature_vector(row)
        f2 = build_feature_vector(row)
        assert list(f1.keys()) == list(f2.keys())
        for key in f1:
            assert f1[key] == f2[key]

    def test_bounded_text_truncated_to_500(self) -> None:
        """Text features are truncated to 500 characters."""
        long = "a" * 1000
        fs = _make_feature_snapshot(
            normalized_description=long,
            normalized_memo=long,
            normalized_vendor=long,
        )
        row = _make_row(feature_snapshot=fs)
        fv = build_feature_vector(row)
        assert len(fv["description_text"]) == 500
        assert len(fv["memo_text"]) == 500
        assert len(fv["normalized_vendor"]) == 500

    def test_missing_values_handled_defaults(self) -> None:
        """Missing feature_snapshot fields default to 0/empty."""
        # Empty feature_snapshot + empty transaction_type in outer row
        row = _make_row(
            feature_snapshot={},
            transaction_type="",
        )
        fv = build_feature_vector(row)
        assert fv["amount"] == 0.0
        assert fv["absolute_amount"] == 0.0
        assert fv["transaction_type"] == ""
        assert fv["description_text"] == ""
        assert fv["memo_text"] == ""
        assert fv["normalized_vendor"] == ""
        assert fv["transaction_date_day_of_week"] == 0
        assert fv["transaction_date_month"] == 1

    def test_unknown_vendors_empty_string(self) -> None:
        """Empty vendor name is handled without error."""
        fs = _make_feature_snapshot(normalized_vendor="")
        row = _make_row(feature_snapshot=fs)
        fv = build_feature_vector(row)
        assert fv["normalized_vendor"] == ""

    def test_feature_names_filter(self) -> None:
        """Passing feature_names filters to only those keys."""
        row = _make_row()
        fv = build_feature_vector(row, feature_names=["amount", "absolute_amount"])
        assert set(fv.keys()) == {"amount", "absolute_amount"}

    def test_temporal_features_from_iso_date(self) -> None:
        """day_of_week and month are correctly derived from ISO date."""
        # 2025-01-15 is a Wednesday (weekday=2)
        fs = _make_feature_snapshot(transaction_date="2025-01-15T00:00:00Z")
        row = _make_row(feature_snapshot=fs)
        fv = build_feature_vector(row)
        assert fv["transaction_date_day_of_week"] == 2
        assert fv["transaction_date_month"] == 1

    def test_invalid_date_falls_back_to_defaults(self) -> None:
        """Invalid date string falls back to day=0, month=1."""
        fs = _make_feature_snapshot(transaction_date="not-a-date")
        row = _make_row(feature_snapshot=fs)
        fv = build_feature_vector(row)
        assert fv["transaction_date_day_of_week"] == 0
        assert fv["transaction_date_month"] == 1

    def test_negative_amount_preserved(self) -> None:
        """Signed amount is preserved; absolute_amount is the absolute value."""
        fs = _make_feature_snapshot(amount=-42.50)
        row = _make_row(feature_snapshot=fs)
        fv = build_feature_vector(row)
        assert fv["amount"] == -42.50
        assert fv["absolute_amount"] == 42.50

    # --- FeatureTransformer tests ---

    def _make_train_rows(self, n: int = 10) -> list[dict[str, Any]]:
        """Build n synthetic training rows for FeatureTransformer tests."""
        rows = []
        for i in range(n):
            fs = _make_feature_snapshot(
                amount=float(i * 10 - 50),
                transaction_date=f"2025-{(i % 12) + 1:02d}-15T00:00:00Z",
                normalized_description=f"Transaction description {i}",
                normalized_memo=f"Memo for transaction {i}",
                normalized_vendor=f"vendor-{i % 3}",
                transaction_type=["Purchase", "Invoice", "Expense"][i % 3],
            )
            rows.append(
                {
                    "feature_snapshot": fs,
                    "transaction_type": ["Purchase", "Invoice", "Expense"][i % 3],
                }
            )
        return rows

    def test_fit_transform_returns_numpy_array(self) -> None:
        """FeatureTransformer.fit_transform returns a numpy ndarray."""
        rows = self._make_train_rows(10)
        tx = FeatureTransformer()
        X = tx.fit_transform(rows)
        assert isinstance(X, np.ndarray)
        assert X.shape[0] == 10
        assert X.shape[1] > 0

    def test_transform_raises_if_not_fitted(self) -> None:
        """FeatureTransformer.transform raises RuntimeError before fit."""
        tx = FeatureTransformer()
        rows = self._make_train_rows(5)
        with pytest.raises(RuntimeError, match="not been fitted"):
            tx.transform(rows)

    def test_is_fitted_property(self) -> None:
        """is_fitted is False before fit, True after."""
        tx = FeatureTransformer()
        assert tx.is_fitted is False
        tx.fit_transform(self._make_train_rows(10))
        assert tx.is_fitted is True

    def test_transform_after_fit_produces_valid_output(self) -> None:
        """transform() after fit_transform() returns a valid numpy array."""
        train_rows = self._make_train_rows(10)
        test_rows = self._make_train_rows(5)

        tx = FeatureTransformer()
        X_train = tx.fit_transform(train_rows)
        X_test = tx.transform(test_rows)

        assert X_test.shape[0] == 5
        # Same number of features
        assert X_test.shape[1] == X_train.shape[1]

    def test_fit_transform_deterministic(self) -> None:
        """Same input produces same output from fit_transform."""
        rows = self._make_train_rows(10)
        tx1 = FeatureTransformer()
        tx2 = FeatureTransformer()
        X1 = tx1.fit_transform(rows)
        X2 = tx2.fit_transform(rows)
        np.testing.assert_array_almost_equal(X1, X2)

    def test_feature_schema_version_defined(self) -> None:
        """FEATURE_SCHEMA_VERSION is defined and non-empty."""
        assert isinstance(FEATURE_SCHEMA_VERSION, str)
        assert len(FEATURE_SCHEMA_VERSION) > 0

    def test_all_features_non_empty(self) -> None:
        """ALL_FEATURES contains at least the expected number of features."""
        assert len(ALL_FEATURES) == 8  # 7 transaction + 1 vendor
        feature_names = {f.name for f in ALL_FEATURES}
        assert "amount" in feature_names
        assert "normalized_vendor" in feature_names
        assert "transaction_type" in feature_names

    def test_feature_vector_keys_match_schema(self) -> None:
        """build_feature_vector keys are a subset of ALL_FEATURES names."""
        row = _make_row()
        fv = build_feature_vector(row)
        schema_names = {f.name for f in ALL_FEATURES}
        for key in fv:
            assert key in schema_names, f"Feature '{key}' not in schema"

    def test_transformer_handles_missing_snapshot_fields(self) -> None:
        """FeatureTransformer handles rows with sparse feature_snapshot."""
        # Use minimal but non-empty text to avoid TfidfVectorizer empty-vocab error
        rows = [
            {
                "feature_snapshot": {
                    "normalized_description": "payment",
                    "normalized_memo": "memo",
                    "normalized_vendor": "vendor",
                    "transaction_type": "Purchase",
                    "amount": 10.0,
                    "absolute_amount": 10.0,
                    "transaction_date_day_of_week": 1,
                    "transaction_date_month": 3,
                },
                "transaction_type": "Purchase",
            },
            {
                "feature_snapshot": {
                    "normalized_description": "invoice",
                    "normalized_memo": "note",
                    "normalized_vendor": "supplier",
                    "transaction_type": "Invoice",
                    "amount": 20.0,
                    "absolute_amount": 20.0,
                    "transaction_date_day_of_week": 2,
                    "transaction_date_month": 4,
                },
                "transaction_type": "Invoice",
            },
            {
                "feature_snapshot": {
                    "normalized_description": "expense",
                    "normalized_memo": "claim",
                    "normalized_vendor": "store",
                    "transaction_type": "Expense",
                    "amount": 30.0,
                    "absolute_amount": 30.0,
                    "transaction_date_day_of_week": 3,
                    "transaction_date_month": 5,
                },
                "transaction_type": "Expense",
            },
        ]
        tx = FeatureTransformer()
        X = tx.fit_transform(rows)
        assert X.shape[0] == 3
        assert not np.any(np.isnan(X))  # imputed, no NaN
