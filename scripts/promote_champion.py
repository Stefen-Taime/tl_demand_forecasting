from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

import mlflow
import mlflow.pyfunc
import numpy as np
import pandas as pd
from mlflow.exceptions import MlflowException
from sklearn.metrics import mean_absolute_error, mean_squared_error

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from prediction_service.feature_builder import TARGET_COLUMN, build_model_matrix

ELIGIBLE_MODEL_TYPES = {"lightgbm", "xgboost"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Promote only validated challengers to the champion alias.")
    parser.add_argument("--experiment", default="tlc-demand-forecasting")
    parser.add_argument("--model-name", default="tlc-demand-forecasting")
    parser.add_argument("--holdout-dataset", default="data/holdout/holdout_features.parquet")
    parser.add_argument("--max-holdout-mase", type=float, default=1.0)
    parser.add_argument("--min-baseline-improvement-pct", type=float, default=0.0)
    parser.add_argument("--min-champion-improvement-pct", type=float, default=0.0)
    parser.add_argument("--output-path", default="reports/promotion_decision.json")
    return parser.parse_args()


def ensure_registered_model(client: mlflow.MlflowClient, name: str) -> None:
    try:
        client.get_registered_model(name)
    except MlflowException:
        client.create_registered_model(name)


def load_holdout_frame(path: str) -> pd.DataFrame:
    frame = pd.read_parquet(path).sort_values("target_hour").reset_index(drop=True)
    if frame.empty:
        raise ValueError("Holdout dataset is empty.")
    return frame


def rank_candidates(runs: pd.DataFrame) -> pd.DataFrame:
    required_columns = [
        "metrics.holdout_mae",
        "metrics.holdout_mase",
        "metrics.cv_mae_mean",
        "metrics.holdout_mae_improvement_vs_best_baseline_pct",
        "params.model_type",
    ]
    missing = [column for column in required_columns if column not in runs.columns]
    if missing:
        raise RuntimeError(f"MLflow runs are missing required validation metrics: {missing}")

    candidates = runs[
        runs["params.model_type"].isin(ELIGIBLE_MODEL_TYPES)
        & runs["metrics.holdout_mae"].notna()
        & runs["metrics.holdout_mase"].notna()
        & runs["metrics.cv_mae_mean"].notna()
    ].copy()
    if candidates.empty:
        raise RuntimeError("No eligible challenger runs found with holdout metrics.")

    return candidates.sort_values(
        by=["metrics.holdout_mae", "metrics.cv_mae_mean", "metrics.holdout_mase"],
        ascending=[True, True, True],
        na_position="last",
    )


def holdout_metrics_for_model(model_uri: str, holdout_frame: pd.DataFrame) -> dict[str, float]:
    model = mlflow.pyfunc.load_model(model_uri)
    predictions = np.asarray(model.predict(build_model_matrix(holdout_frame)), dtype=float)
    predictions = np.clip(predictions, a_min=0, a_max=None)
    y_true = holdout_frame[TARGET_COLUMN]
    mae = float(mean_absolute_error(y_true, predictions))
    rmse = float(np.sqrt(mean_squared_error(y_true, predictions)))
    return {"holdout_mae": mae, "holdout_rmse": rmse}


def current_champion_metrics(
    client: mlflow.MlflowClient,
    model_name: str,
    holdout_frame: pd.DataFrame,
) -> dict[str, object]:
    try:
        champion_version = client.get_model_version_by_alias(model_name, "champion")
    except MlflowException:
        return {"exists": False}

    model_uri = f"models:/{model_name}@champion"
    metrics = holdout_metrics_for_model(model_uri, holdout_frame)
    return {
        "exists": True,
        "version": champion_version.version,
        "run_id": champion_version.run_id,
        **metrics,
    }


def set_version_tags(
    client: mlflow.MlflowClient,
    model_name: str,
    version: str,
    decision_payload: dict[str, object],
) -> None:
    for key, value in decision_payload.items():
        client.set_model_version_tag(model_name, version, key, json.dumps(value) if isinstance(value, dict) else str(value))


def main() -> None:
    args = parse_args()

    tracking_uri = os.getenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000")
    mlflow.set_tracking_uri(tracking_uri)
    client = mlflow.MlflowClient()
    experiment = mlflow.get_experiment_by_name(args.experiment)
    if experiment is None:
        raise ValueError(f"Experiment not found: {args.experiment}")

    holdout_frame = load_holdout_frame(args.holdout_dataset)
    runs = mlflow.search_runs(
        experiment_ids=[experiment.experiment_id],
        filter_string="attributes.status = 'FINISHED'",
    )
    ranked_candidates = rank_candidates(runs)
    best_run = ranked_candidates.iloc[0]

    challenger = {
        "run_id": best_run["run_id"],
        "run_name": best_run.get("tags.mlflow.runName"),
        "model_type": best_run.get("params.model_type"),
        "holdout_mae": float(best_run["metrics.holdout_mae"]),
        "holdout_rmse": float(best_run["metrics.holdout_rmse"]),
        "holdout_mase": float(best_run["metrics.holdout_mase"]),
        "cv_mae_mean": float(best_run["metrics.cv_mae_mean"]),
        "cv_mae_std": float(best_run.get("metrics.cv_mae_std", np.nan)),
        "holdout_mae_improvement_vs_best_baseline_pct": float(
            best_run["metrics.holdout_mae_improvement_vs_best_baseline_pct"]
        ),
    }

    champion = current_champion_metrics(client, args.model_name, holdout_frame)

    gates = {
        "beats_best_baseline": challenger["holdout_mae_improvement_vs_best_baseline_pct"]
        > args.min_baseline_improvement_pct,
        "holdout_mase_lt_threshold": challenger["holdout_mase"] < args.max_holdout_mase,
    }

    champion_gate_detail: dict[str, object]
    if not champion["exists"]:
        champion_gate_detail = {"status": "skipped", "reason": "no_existing_champion"}
        gates["beats_current_champion"] = True
    elif champion["run_id"] == challenger["run_id"]:
        champion_gate_detail = {"status": "skipped", "reason": "challenger_is_current_champion"}
        gates["beats_current_champion"] = True
    else:
        required_mae = champion["holdout_mae"] * (1 - args.min_champion_improvement_pct)
        gates["beats_current_champion"] = challenger["holdout_mae"] < required_mae
        champion_gate_detail = {
            "status": "evaluated",
            "current_champion_holdout_mae": champion["holdout_mae"],
            "current_champion_holdout_rmse": champion["holdout_rmse"],
            "required_mae": required_mae,
        }

    approved = all(gates.values())

    ensure_registered_model(client, args.model_name)
    model_uri = f"runs:/{challenger['run_id']}/model"
    created = mlflow.register_model(model_uri=model_uri, name=args.model_name)
    client.set_registered_model_alias(args.model_name, "candidate", created.version)

    decision = {
        "approved_for_champion": approved,
        "challenger": challenger,
        "champion_comparison": champion_gate_detail,
        "gates": gates,
        "registered_version": created.version,
    }
    set_version_tags(
        client,
        args.model_name,
        created.version,
        {
            "validation_status": "approved" if approved else "rejected",
            "holdout_mae": challenger["holdout_mae"],
            "holdout_mase": challenger["holdout_mase"],
            "cv_mae_mean": challenger["cv_mae_mean"],
            "gates": gates,
        },
    )
    client.set_tag(challenger["run_id"], "validation_status", "approved" if approved else "rejected")
    client.set_tag(challenger["run_id"], "promotion_decision", json.dumps(gates))

    if approved:
        client.set_registered_model_alias(args.model_name, "champion", created.version)

    output_path = Path(args.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(decision, indent=2))

    if approved:
        print(
            f"Promoted run {challenger['run_id']} to models:/{args.model_name}@champion "
            f"(version {created.version})"
        )
    else:
        print(
            f"Registered run {challenger['run_id']} as candidate version {created.version}, "
            "but it did not pass promotion gates."
        )
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
