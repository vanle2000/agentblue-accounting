"""Drift detection for ML feature and label distributions.

Uses Population Stability Index (PSI) for numeric features and
Jensen-Shannon Divergence (JSD) for categorical features/labels.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import structlog

logger = structlog.get_logger(__name__)

# Minimum bin count for PSI calculation.
_PSI_N_BINS = 10
# Small epsilon to avoid log(0).
_EPSILON = 1e-10


class DriftDetector:
    """Detects distribution drift between reference and current data."""

    def detect_drift(
        self,
        reference_data: dict[str, np.ndarray[Any, Any]],
        current_data: dict[str, np.ndarray[Any, Any]],
    ) -> dict[str, Any]:
        """Detect drift between reference and current feature distributions.

        For numeric features, computes PSI (Population Stability Index).
        For categorical features, computes JSD (Jensen-Shannon Divergence).

        Args:
            reference_data: Dict of feature_name -> numpy array from the
                training/reference period.
            current_data: Dict of feature_name -> numpy array from the
                current/production period.

        Returns:
            A drift report dict with keys:
                - feature_drift: dict of feature_name -> drift_result
                - label_drift: empty (use detect_label_drift for labels)
                - warnings: list of warning strings
                - summary: overall drift summary
        """
        feature_drift: dict[str, dict[str, Any]] = {}
        warnings: list[str] = []

        common_features = set(reference_data.keys()) & set(current_data.keys())
        if not common_features:
            warnings.append("No common features between reference and current data.")
            return {
                "feature_drift": {},
                "label_drift": {},
                "warnings": warnings,
                "summary": {"drifted_features": 0, "total_features": 0},
            }

        drifted_count = 0
        for feature in sorted(common_features):
            ref = reference_data[feature]
            cur = current_data[feature]

            if self._is_numeric(ref):
                result = self._compute_psi(feature, ref, cur)
            else:
                result = self._compute_jsd(feature, ref, cur)

            feature_drift[feature] = result

            if result.get("drifted", False):
                drifted_count += 1
                warnings.append(
                    f"Feature '{feature}' shows drift: "
                    f"{result['metric_name']}={result['metric_value']:.4f}"
                )

        return {
            "feature_drift": feature_drift,
            "label_drift": {},
            "warnings": warnings,
            "summary": {
                "drifted_features": drifted_count,
                "total_features": len(common_features),
            },
        }

    def detect_label_drift(
        self,
        reference_labels: np.ndarray[Any, Any],
        current_labels: np.ndarray[Any, Any],
    ) -> dict[str, Any]:
        """Detect drift in label distributions using JSD.

        Args:
            reference_labels: Label array from the training period.
            current_labels: Label array from the current period.

        Returns:
            A dict with keys: metric_name, metric_value, drifted, details.
        """
        return self._compute_jsd("_label_", reference_labels, current_labels)

    def _compute_psi(
        self,
        feature_name: str,
        reference: np.ndarray[Any, Any],
        current: np.ndarray[Any, Any],
    ) -> dict[str, Any]:
        """Compute Population Stability Index for a numeric feature.

        PSI interpretation:
            < 0.1  : no significant change
            0.1-0.2: moderate change
            > 0.2  : significant change

        Args:
            feature_name: Name of the feature (for logging).
            reference: Reference distribution array.
            current: Current distribution array.

        Returns:
            PSI result dict.
        """
        from agentblue.ml.constants import DRIFT_WARNING_THRESHOLD

        # Compute shared bin edges.
        combined = np.concatenate([reference, current])
        bin_edges = np.histogram_bin_edges(combined, bins=_PSI_N_BINS)

        ref_counts, _ = np.histogram(reference, bins=bin_edges)
        cur_counts, _ = np.histogram(current, bins=bin_edges)

        # Normalize to proportions.
        ref_prop = ref_counts / max(len(reference), 1) + _EPSILON
        cur_prop = cur_counts / max(len(current), 1) + _EPSILON

        # PSI = sum((cur - ref) * ln(cur / ref))
        psi = float(np.sum((cur_prop - ref_prop) * np.log(cur_prop / ref_prop)))

        drifted = psi > float(DRIFT_WARNING_THRESHOLD)

        return {
            "metric_name": "PSI",
            "metric_value": psi,
            "drifted": drifted,
            "threshold": float(DRIFT_WARNING_THRESHOLD),
            "reference_size": len(reference),
            "current_size": len(current),
        }

    def _compute_jsd(
        self,
        feature_name: str,
        reference: np.ndarray[Any, Any],
        current: np.ndarray[Any, Any],
    ) -> dict[str, Any]:
        """Compute Jensen-Shannon Divergence for a categorical feature.

        JSD is bounded in [0, ln(2)] and is symmetric.

        Args:
            feature_name: Name of the feature (for logging).
            reference: Reference distribution array.
            current: Current distribution array.

        Returns:
            JSD result dict.
        """
        from agentblue.ml.constants import DRIFT_WARNING_THRESHOLD

        # Build category probability distributions.
        ref_cats, ref_counts = np.unique(reference, return_counts=True)
        cur_cats, cur_counts = np.unique(current, return_counts=True)

        all_cats = np.union1d(ref_cats, cur_cats)

        ref_dist = np.zeros(len(all_cats))
        cur_dist = np.zeros(len(all_cats))

        for i, cat in enumerate(all_cats):
            ref_idx = np.where(ref_cats == cat)[0]
            if len(ref_idx) > 0:
                ref_dist[i] = ref_counts[ref_idx[0]]
            cur_idx = np.where(cur_cats == cat)[0]
            if len(cur_idx) > 0:
                cur_dist[i] = cur_counts[cur_idx[0]]

        # Normalize.
        ref_sum = ref_dist.sum()
        cur_sum = cur_dist.sum()
        if ref_sum > 0:
            ref_dist /= ref_sum
        if cur_sum > 0:
            cur_dist /= cur_sum

        # Add epsilon to avoid log(0).
        ref_dist = ref_dist + _EPSILON
        cur_dist = cur_dist + _EPSILON
        ref_dist /= ref_dist.sum()
        cur_dist /= cur_dist.sum()

        # JSD = 0.5 * KL(ref || m) + 0.5 * KL(cur || m)
        m = 0.5 * (ref_dist + cur_dist)
        jsd = 0.5 * self._kl_divergence(ref_dist, m) + 0.5 * self._kl_divergence(cur_dist, m)

        drifted = jsd > float(DRIFT_WARNING_THRESHOLD)

        return {
            "metric_name": "JSD",
            "metric_value": float(jsd),
            "drifted": drifted,
            "threshold": float(DRIFT_WARNING_THRESHOLD),
            "reference_size": len(reference),
            "current_size": len(current),
            "unique_categories": len(all_cats),
        }

    @staticmethod
    def _kl_divergence(
        p: np.ndarray[Any, Any],
        q: np.ndarray[Any, Any],
    ) -> float:
        """Compute KL(p || q)."""
        return float(np.sum(p * np.log(p / q)))

    @staticmethod
    def _is_numeric(arr: np.ndarray[Any, Any]) -> bool:
        """Check if a numpy array is numeric."""
        return np.issubdtype(arr.dtype, np.number)
