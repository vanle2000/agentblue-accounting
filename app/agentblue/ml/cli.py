"""CLI commands for ML operations (Stage 8).

Provides argparse-based commands for dataset building, training,
evaluation, shadow activation, and drift reporting.

All commands require authentication via a JWT token passed via
--token flag or AGENTBLUE_CLI_TOKEN environment variable.

Usage:
    python -m agentblue.ml.cli build-dataset --realm-id <id> --token <jwt>
    python -m agentblue.ml.cli train --dataset-id <id> --realm-id <id> --token <jwt>
    python -m agentblue.ml.cli evaluate --model-id <id> --token <jwt>
    python -m agentblue.ml.cli activate-shadow --model-id <id> --token <jwt>
    python -m agentblue.ml.cli drift-report --realm-id <id> --model-id <id> --token <jwt>

All commands return nonzero on failure and support --json for
machine-readable output.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from agentblue.db.session import get_session_factory
from agentblue.security.config import SecuritySettings
from agentblue.security.roles import Permission, has_permission

if TYPE_CHECKING:
    from agentblue.security.principal import Principal

logger = structlog.get_logger(__name__)


def _output_json(data: dict[str, Any], use_json: bool) -> None:
    """Print output as JSON or human-readable text."""
    if use_json:
        print(json.dumps(data, indent=2, default=str))
    else:
        for key, value in data.items():
            print(f"  {key}: {value}")


def _resolve_token(args: argparse.Namespace) -> str:
    """Resolve the authentication token from args or environment.

    Args:
        args: Parsed CLI arguments.

    Returns:
        The token string.

    Raises:
        SystemExit: If no token is available.
    """
    token = getattr(args, "token", None) or os.environ.get("AGENTBLUE_CLI_TOKEN", "")
    if not token:
        print(
            "Error: Authentication required. Provide --token or set "
            "AGENTBLUE_CLI_TOKEN environment variable.",
            file=sys.stderr,
        )
        sys.exit(1)
    return token


def _authenticate(token: str) -> Principal:
    """Validate the token and return a Principal.

    Args:
        token: JWT token string.

    Returns:
        Authenticated Principal.

    Raises:
        SystemExit: If authentication fails.
    """
    from agentblue.security.auth import _payload_to_principal, decode_token

    settings = SecuritySettings()
    try:
        payload = decode_token(token, settings)
        principal = _payload_to_principal(payload, correlation_id="cli")
    except Exception as exc:
        print(f"Error: Authentication failed: {exc}", file=sys.stderr)
        sys.exit(1)

    if not principal.active:
        print("Error: Account is inactive.", file=sys.stderr)
        sys.exit(1)

    return principal


def _require_permission(principal: Principal, permission: Permission) -> None:
    """Verify the principal has the required permission.

    Args:
        principal: Authenticated principal.
        permission: Required permission.

    Raises:
        SystemExit: If permission denied.
    """
    if not has_permission(principal.roles, permission):
        print(
            f"Error: Permission denied. Required: {permission.value}. "
            f"Your roles: {sorted(r.value for r in principal.roles)}",
            file=sys.stderr,
        )
        sys.exit(1)


def _require_realm(principal: Principal, realm_id: str) -> None:
    """Verify the principal has access to the specified realm.

    Args:
        principal: Authenticated principal.
        realm_id: Realm to check.

    Raises:
        SystemExit: If realm access denied.
    """
    if not principal.has_realm_access(realm_id):
        print(
            f"Error: Access denied to realm '{realm_id}'. "
            f"Your realms: {sorted(principal.realm_ids)}",
            file=sys.stderr,
        )
        sys.exit(1)


async def _cmd_build_dataset(args: argparse.Namespace) -> int:
    """Build a training dataset."""
    from agentblue.ml.services import MLService

    token = _resolve_token(args)
    principal = _authenticate(token)
    _require_permission(principal, Permission.ML_DATASET_CREATE)
    _require_realm(principal, args.realm_id)

    logger.info(
        "cli_build_dataset",
        principal_id=principal.principal_id,
        realm_id=args.realm_id,
    )

    factory = get_session_factory()
    async with factory() as session:
        service = MLService()
        try:
            result = await service.build_dataset(
                session,
                realm_id=args.realm_id,
                feature_version=args.feature_version,
                min_rows=args.min_rows,
                min_class_support=args.min_class_support,
            )
            await session.commit()
            print("Dataset built successfully.")
            _output_json(result, args.json)
            return 0
        except Exception as exc:
            await session.rollback()
            print(f"Error: {exc}", file=sys.stderr)
            return 1


async def _cmd_train(args: argparse.Namespace) -> int:
    """Start a training run."""
    from agentblue.ml.services import MLService

    token = _resolve_token(args)
    principal = _authenticate(token)
    _require_permission(principal, Permission.ML_TRAIN)
    _require_realm(principal, args.realm_id)

    logger.info(
        "cli_train",
        principal_id=principal.principal_id,
        realm_id=args.realm_id,
        model_type=args.model_type,
    )

    factory = get_session_factory()
    async with factory() as session:
        service = MLService()
        try:
            result = await service.start_training(
                session,
                dataset_id=args.dataset_id,
                realm_id=args.realm_id,
                model_type=args.model_type,
                calibration_method=args.calibration_method,
                seed=args.seed,
            )
            await session.commit()
            print("Training run started.")
            _output_json(result, args.json)
            return 0
        except Exception as exc:
            await session.rollback()
            print(f"Error: {exc}", file=sys.stderr)
            return 1


async def _cmd_evaluate(args: argparse.Namespace) -> int:
    """Evaluate a model and print metrics."""
    from sqlalchemy import select

    from agentblue.ml.models import MlModel

    token = _resolve_token(args)
    principal = _authenticate(token)
    _require_permission(principal, Permission.ML_READ)

    logger.info(
        "cli_evaluate",
        principal_id=principal.principal_id,
        model_id=args.model_id,
    )

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(MlModel).where(MlModel.id == args.model_id))
        model = result.scalar_one_or_none()
        if model is None:
            print(f"Error: Model not found: {args.model_id}", file=sys.stderr)
            return 1

        # Realm check: model must belong to one of the principal's realms.
        if model.realm_id and not principal.has_realm_access(model.realm_id):
            print(
                f"Error: Access denied to model in realm '{model.realm_id}'.",
                file=sys.stderr,
            )
            return 1

        metrics = model.metrics or {}
        print(f"Model: {model.model_type} ({model.id})")
        print(f"Status: {model.status}")
        _output_json(
            {
                "model_id": model.id,
                "model_type": model.model_type,
                "status": model.status,
                "feature_version": model.feature_version,
                "metrics": metrics,
            },
            args.json,
        )
        return 0


async def _cmd_activate_shadow(args: argparse.Namespace) -> int:
    """Activate a model in shadow mode."""
    from agentblue.ml.services import MLService

    token = _resolve_token(args)
    principal = _authenticate(token)
    _require_permission(principal, Permission.ML_SHADOW_ACTIVATE)

    logger.info(
        "cli_activate_shadow",
        principal_id=principal.principal_id,
        model_id=args.model_id,
    )

    factory = get_session_factory()
    async with factory() as session:
        service = MLService()
        try:
            result = await service.activate_shadow(session, args.model_id)
            await session.commit()
            print("Shadow mode activated.")
            _output_json(result, args.json)
            return 0
        except Exception as exc:
            await session.rollback()
            print(f"Error: {exc}", file=sys.stderr)
            return 1


async def _cmd_drift_report(args: argparse.Namespace) -> int:
    """Generate a drift report."""
    from sqlalchemy import select

    from agentblue.ml.models import MlDriftReport, MlModel

    token = _resolve_token(args)
    principal = _authenticate(token)
    _require_permission(principal, Permission.ML_TRAIN)
    _require_realm(principal, args.realm_id)

    logger.info(
        "cli_drift_report",
        principal_id=principal.principal_id,
        realm_id=args.realm_id,
        model_id=args.model_id,
    )

    factory = get_session_factory()
    async with factory() as session:
        # Verify model exists.
        result = await session.execute(select(MlModel).where(MlModel.id == args.model_id))
        model = result.scalar_one_or_none()
        if model is None:
            print(f"Error: Model not found: {args.model_id}", file=sys.stderr)
            return 1

        now = datetime.now(UTC)

        # Store a placeholder drift report.
        report = MlDriftReport(
            realm_id=args.realm_id,
            model_id=args.model_id,
            window_start=now,
            window_end=now,
            feature_drift={},
            label_drift={},
            warnings=["Drift computation requires feature extraction pipeline."],
        )
        session.add(report)
        await session.commit()

        print("Drift report generated.")
        _output_json(
            {
                "report_id": report.id,
                "realm_id": report.realm_id,
                "model_id": report.model_id,
                "warnings": report.warnings or [],
            },
            args.json,
        )
        return 0


def _add_auth_args(parser: argparse.ArgumentParser) -> None:
    """Add authentication arguments to a subparser."""
    parser.add_argument(
        "--token",
        default=None,
        help="JWT authentication token (or set AGENTBLUE_CLI_TOKEN env var)",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="agentblue.ml.cli",
        description="Agent Blue ML CLI (Stage 8)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # build-dataset
    p_build = subparsers.add_parser("build-dataset", help="Build a training dataset")
    p_build.add_argument("--realm-id", required=True, help="QuickBooks realm ID")
    p_build.add_argument("--feature-version", default="1.0", help="Feature version")
    p_build.add_argument("--min-rows", type=int, default=500, help="Minimum rows")
    p_build.add_argument(
        "--min-class-support", type=int, default=20, help="Minimum examples per class"
    )
    p_build.add_argument("--json", action="store_true", help="JSON output")
    _add_auth_args(p_build)

    # train
    p_train = subparsers.add_parser("train", help="Start a training run")
    p_train.add_argument("--dataset-id", required=True, help="Dataset ID")
    p_train.add_argument("--realm-id", required=True, help="QuickBooks realm ID")
    p_train.add_argument("--model-type", default="HIST_GRADIENT_BOOSTING", help="Model type")
    p_train.add_argument("--calibration-method", default="ISOTONIC", help="Calibration method")
    p_train.add_argument("--seed", type=int, default=42, help="Random seed")
    p_train.add_argument("--json", action="store_true", help="JSON output")
    _add_auth_args(p_train)

    # evaluate
    p_eval = subparsers.add_parser("evaluate", help="Evaluate a model")
    p_eval.add_argument("--model-id", required=True, help="Model ID")
    p_eval.add_argument("--json", action="store_true", help="JSON output")
    _add_auth_args(p_eval)

    # activate-shadow
    p_shadow = subparsers.add_parser("activate-shadow", help="Activate shadow mode")
    p_shadow.add_argument("--model-id", required=True, help="Model ID")
    p_shadow.add_argument("--json", action="store_true", help="JSON output")
    _add_auth_args(p_shadow)

    # drift-report
    p_drift = subparsers.add_parser("drift-report", help="Generate drift report")
    p_drift.add_argument("--realm-id", required=True, help="QuickBooks realm ID")
    p_drift.add_argument("--model-id", required=True, help="Model ID")
    p_drift.add_argument("--window-days", type=int, default=30, help="Window days")
    p_drift.add_argument("--json", action="store_true", help="JSON output")
    _add_auth_args(p_drift)

    return parser


_COMMANDS: dict[str, Any] = {
    "build-dataset": _cmd_build_dataset,
    "train": _cmd_train,
    "evaluate": _cmd_evaluate,
    "activate-shadow": _cmd_activate_shadow,
    "drift-report": _cmd_drift_report,
}


def main() -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    handler = _COMMANDS.get(args.command)
    if handler is None:
        parser.print_help()
        return 1

    return asyncio.run(handler(args))


if __name__ == "__main__":
    sys.exit(main())
