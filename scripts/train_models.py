from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

import mlflow
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from prediction_service.feature_builder import (
    FEATURE_COLUMNS,
    TARGET_COLUMN,
    TIME_COLUMN,
    ZONE_ID_COLUMN,
    build_model_matrix,
    required_columns,
)

BASELINE_SPECS = {
    "seasonal_naive_24h": "lag_24h",
    "seasonal_naive_168h": "lag_168h",
    "rolling_mean_24h": "rolling_mean_24h",
}
REFERENCE_BASELINE_NAME = "seasonal_naive_168h"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train candidate forecasting models with time-aware validation and a frozen holdout."
    )
    parser.add_argument("--train-dataset", default="data/processed/train_features.parquet")
    parser.add_argument("--holdout-dataset", default="data/holdout/holdout_features.parquet")
    parser.add_argument("--experiment", default="tlc-demand-forecasting")
    parser.add_argument("--cv-splits", type=int, default=4)
    parser.add_argument("--cv-horizon-hours", type=int, default=24 * 7)
    parser.add_argument("--min-train-hours", type=int, default=24 * 28)
    parser.add_argument("--seasonal-period", type=int, default=24 * 7)
    return parser.parse_args()


def load_frame(path: str, dataset_name: str) -> pd.DataFrame:
    frame = pd.read_parquet(path)
    validate_frame(frame, dataset_name)
    frame[TIME_COLUMN] = pd.to_datetime(frame[TIME_COLUMN])
    return frame.sort_values([TIME_COLUMN, ZONE_ID_COLUMN]).reset_index(drop=True)


def validate_frame(frame: pd.DataFrame, dataset_name: str) -> None:
    missing_columns = sorted(set(required_columns()) - set(frame.columns))
    if missing_columns:
        raise ValueError(f"{dataset_name} is missing columns: {missing_columns}")
    if frame.empty:
        raise ValueError(f"{dataset_name} is empty.")
    if frame[TIME_COLUMN].isna().any():
        raise ValueError(f"{dataset_name} contains null {TIME_COLUMN} values.")
    if frame[TARGET_COLUMN].isna().any():
        raise ValueError(f"{dataset_name} contains null {TARGET_COLUMN} values.")
    if (frame[TARGET_COLUMN] < 0).any():
        raise ValueError(f"{dataset_name} contains negative targets.")

    duplicates = frame.duplicated(subset=[TIME_COLUMN, ZONE_ID_COLUMN]).sum()
    if duplicates:
        raise ValueError(f"{dataset_name} contains {duplicates} duplicated zone/hour rows.")


def frame_summary(frame: pd.DataFrame) -> dict[str, float | int | str]:
    return {
        "rows": int(len(frame)),
        "hours": int(frame[TIME_COLUMN].nunique()),
        "zones": int(frame[ZONE_ID_COLUMN].nunique()),
        "target_mean": float(frame[TARGET_COLUMN].mean()),
        "target_median": float(frame[TARGET_COLUMN].median()),
        "target_p95": float(frame[TARGET_COLUMN].quantile(0.95)),
        "target_max": float(frame[TARGET_COLUMN].max()),
        "start_time": pd.Timestamp(frame[TIME_COLUMN].min()).isoformat(),
        "end_time": pd.Timestamp(frame[TIME_COLUMN].max()).isoformat(),
    }


def build_expanding_folds(
    frame: pd.DataFrame,
    requested_splits: int,
    horizon_hours: int,
    min_train_hours: int,
) -> list[dict[str, object]]:
    unique_hours = list(pd.to_datetime(frame[TIME_COLUMN].drop_duplicates()).sort_values())
    max_splits = (len(unique_hours) - min_train_hours) // horizon_hours
    if max_splits < 1:
        raise ValueError(
            "Not enough history to build time-aware folds. "
            f"Need at least {min_train_hours + horizon_hours} unique hours, got {len(unique_hours)}."
        )

    actual_splits = min(requested_splits, max_splits)
    first_train_end = len(unique_hours) - actual_splits * horizon_hours

    folds: list[dict[str, object]] = []
    for fold_index in range(actual_splits):
        train_end = first_train_end + fold_index * horizon_hours
        valid_end = train_end + horizon_hours

        train_hours = unique_hours[:train_end]
        valid_hours = unique_hours[train_end:valid_end]

        train_frame = frame[frame[TIME_COLUMN].isin(train_hours)].copy()
        valid_frame = frame[frame[TIME_COLUMN].isin(valid_hours)].copy()

        folds.append(
            {
                "fold": fold_index + 1,
                "train_frame": train_frame,
                "valid_frame": valid_frame,
                "train_start": pd.Timestamp(train_hours[0]).isoformat(),
                "train_end": pd.Timestamp(train_hours[-1]).isoformat(),
                "valid_start": pd.Timestamp(valid_hours[0]).isoformat(),
                "valid_end": pd.Timestamp(valid_hours[-1]).isoformat(),
                "train_rows": int(len(train_frame)),
                "valid_rows": int(len(valid_frame)),
            }
        )
    return folds


def seasonal_scale(frame: pd.DataFrame, seasonal_period: int) -> float:
    ordered = frame.sort_values([ZONE_ID_COLUMN, TIME_COLUMN])
    diffs = ordered.groupby(ZONE_ID_COLUMN)[TARGET_COLUMN].diff(seasonal_period).abs().dropna()
    if diffs.empty:
        raise ValueError(
            f"Cannot compute a seasonal scale with period={seasonal_period}; not enough historical depth."
        )
    scale = float(diffs.mean())
    if scale == 0:
        raise ValueError("Seasonal scale is zero; MASE would be undefined.")
    return scale


def regression_metrics(y_true: pd.Series, y_pred: np.ndarray, mase_scale: float) -> dict[str, float]:
    mae = float(mean_absolute_error(y_true, y_pred))
    rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
    return {
        "mae": mae,
        "rmse": rmse,
        "mase": float(mae / mase_scale),
    }


def aggregate_fold_metrics(fold_metrics: list[dict[str, float]]) -> dict[str, float]:
    aggregated: dict[str, float] = {}
    for metric_name in ["mae", "rmse", "mase"]:
        values = np.array([fold[metric_name] for fold in fold_metrics], dtype=float)
        aggregated[f"cv_{metric_name}_mean"] = float(values.mean())
        aggregated[f"cv_{metric_name}_std"] = float(values.std(ddof=0))
        aggregated[f"cv_{metric_name}_max"] = float(values.max())
    return aggregated


def baseline_predictions(frame: pd.DataFrame, column_name: str) -> np.ndarray:
    predictions = frame[column_name].astype(float).to_numpy()
    return np.clip(predictions, a_min=0, a_max=None)


def fit_model_and_predict(model, train_frame: pd.DataFrame, valid_frame: pd.DataFrame) -> np.ndarray:
    x_train = build_model_matrix(train_frame)
    x_valid = build_model_matrix(valid_frame)
    y_train = train_frame[TARGET_COLUMN]
    model.fit(x_train, y_train)
    predictions = np.asarray(model.predict(x_valid), dtype=float)
    return np.clip(predictions, a_min=0, a_max=None)


def log_validation_artifacts(
    fold_payload: list[dict[str, object]],
    dataset_payload: dict[str, object],
    metrics_payload: dict[str, object],
) -> None:
    mlflow.log_dict({"folds": fold_payload}, "validation/folds.json")
    mlflow.log_dict(dataset_payload, "validation/datasets.json")
    mlflow.log_dict(metrics_payload, "validation/metrics.json")


def run_baseline_candidate(
    run_name: str,
    prediction_column: str,
    train_frame: pd.DataFrame,
    holdout_frame: pd.DataFrame,
    folds: list[dict[str, object]],
    seasonal_period: int,
    cv_horizon_hours: int,
    dataset_payload: dict[str, object],
) -> dict[str, object]:
    fold_payload: list[dict[str, object]] = []
    fold_metrics: list[dict[str, float]] = []

    for fold in folds:
        fold_train = fold["train_frame"]
        fold_valid = fold["valid_frame"]
        mase_scale = seasonal_scale(fold_train, seasonal_period)
        predictions = baseline_predictions(fold_valid, prediction_column)
        metrics = regression_metrics(fold_valid[TARGET_COLUMN], predictions, mase_scale)
        fold_payload.append(
            {
                "fold": fold["fold"],
                "train_start": fold["train_start"],
                "train_end": fold["train_end"],
                "valid_start": fold["valid_start"],
                "valid_end": fold["valid_end"],
                **metrics,
            }
        )
        fold_metrics.append(metrics)

    cv_metrics = aggregate_fold_metrics(fold_metrics)
    holdout_scale = seasonal_scale(train_frame, seasonal_period)
    holdout_predictions = baseline_predictions(holdout_frame, prediction_column)
    holdout_metrics = regression_metrics(holdout_frame[TARGET_COLUMN], holdout_predictions, holdout_scale)

    all_metrics = {
        **cv_metrics,
        "holdout_mae": holdout_metrics["mae"],
        "holdout_rmse": holdout_metrics["rmse"],
        "holdout_mase": holdout_metrics["mase"],
    }

    with mlflow.start_run(run_name=run_name):
        mlflow.set_tags(
            {
                "candidate_kind": "baseline",
                "task_granularity": "zone_hourly",
                "validation_protocol": "expanding_window_cv_plus_frozen_holdout",
                "reference_baseline": REFERENCE_BASELINE_NAME,
            }
        )
        mlflow.log_params(
            {
                "model_type": run_name,
                "prediction_source": prediction_column,
                "seasonal_period": seasonal_period,
                "cv_splits": len(folds),
                "cv_horizon_hours": cv_horizon_hours,
            }
        )
        mlflow.log_metrics(all_metrics)
        log_validation_artifacts(
            fold_payload=fold_payload,
            dataset_payload=dataset_payload,
            metrics_payload={"aggregate": all_metrics, "candidate": run_name},
        )
        print(f"{run_name}: {all_metrics}")

    return {"run_name": run_name, "metrics": all_metrics}


def run_model_candidate(
    run_name: str,
    model_factory,
    model_params: dict[str, object],
    train_frame: pd.DataFrame,
    holdout_frame: pd.DataFrame,
    folds: list[dict[str, object]],
    seasonal_period: int,
    cv_horizon_hours: int,
    dataset_payload: dict[str, object],
    baseline_context: dict[str, float],
) -> dict[str, object]:
    fold_payload: list[dict[str, object]] = []
    fold_metrics: list[dict[str, float]] = []

    for fold in folds:
        fold_train = fold["train_frame"]
        fold_valid = fold["valid_frame"]
        mase_scale = seasonal_scale(fold_train, seasonal_period)
        predictions = fit_model_and_predict(model_factory(), fold_train, fold_valid)
        metrics = regression_metrics(fold_valid[TARGET_COLUMN], predictions, mase_scale)
        fold_payload.append(
            {
                "fold": fold["fold"],
                "train_start": fold["train_start"],
                "train_end": fold["train_end"],
                "valid_start": fold["valid_start"],
                "valid_end": fold["valid_end"],
                **metrics,
            }
        )
        fold_metrics.append(metrics)

    cv_metrics = aggregate_fold_metrics(fold_metrics)

    final_model = model_factory()
    holdout_scale = seasonal_scale(train_frame, seasonal_period)
    holdout_predictions = fit_model_and_predict(final_model, train_frame, holdout_frame)
    holdout_metrics = regression_metrics(holdout_frame[TARGET_COLUMN], holdout_predictions, holdout_scale)

    best_baseline_holdout_mae = baseline_context["best_baseline_holdout_mae"]
    reference_baseline_holdout_mae = baseline_context["reference_baseline_holdout_mae"]
    all_metrics = {
        **cv_metrics,
        "holdout_mae": holdout_metrics["mae"],
        "holdout_rmse": holdout_metrics["rmse"],
        "holdout_mase": holdout_metrics["mase"],
        "baseline_best_holdout_mae": best_baseline_holdout_mae,
        "baseline_reference_holdout_mae": reference_baseline_holdout_mae,
        "holdout_mae_improvement_vs_best_baseline_pct": float(
            (best_baseline_holdout_mae - holdout_metrics["mae"]) / best_baseline_holdout_mae
        ),
        "holdout_mae_improvement_vs_reference_baseline_pct": float(
            (reference_baseline_holdout_mae - holdout_metrics["mae"]) / reference_baseline_holdout_mae
        ),
    }

    with mlflow.start_run(run_name=run_name):
        mlflow.set_tags(
            {
                "candidate_kind": "model",
                "task_granularity": "zone_hourly",
                "validation_protocol": "expanding_window_cv_plus_frozen_holdout",
                "reference_baseline": REFERENCE_BASELINE_NAME,
            }
        )
        mlflow.log_params(
            {
                "model_type": run_name,
                "seasonal_period": seasonal_period,
                "cv_splits": len(folds),
                "cv_horizon_hours": cv_horizon_hours,
                "n_features": len(FEATURE_COLUMNS),
                **model_params,
            }
        )
        mlflow.log_metrics(all_metrics)
        log_validation_artifacts(
            fold_payload=fold_payload,
            dataset_payload=dataset_payload,
            metrics_payload={"aggregate": all_metrics, "candidate": run_name},
        )
        mlflow.sklearn.log_model(final_model, name="model")
        print(f"{run_name}: {all_metrics}")

    return {"run_name": run_name, "metrics": all_metrics}


def build_dataset_payload(train_frame: pd.DataFrame, holdout_frame: pd.DataFrame) -> dict[str, object]:
    return {
        "train": frame_summary(train_frame),
        "holdout": frame_summary(holdout_frame),
    }


def ensure_temporal_holdout(train_frame: pd.DataFrame, holdout_frame: pd.DataFrame) -> None:
    train_end = pd.Timestamp(train_frame[TIME_COLUMN].max())
    holdout_start = pd.Timestamp(holdout_frame[TIME_COLUMN].min())
    if train_end >= holdout_start:
        raise ValueError(
            f"Holdout leakage detected: train ends at {train_end.isoformat()}, "
            f"holdout starts at {holdout_start.isoformat()}."
        )


def main() -> None:
    args = parse_args()

    tracking_uri = os.getenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000")
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(args.experiment)

    train_frame = load_frame(args.train_dataset, "train dataset")
    holdout_frame = load_frame(args.holdout_dataset, "holdout dataset")
    ensure_temporal_holdout(train_frame, holdout_frame)

    folds = build_expanding_folds(
        train_frame,
        requested_splits=args.cv_splits,
        horizon_hours=args.cv_horizon_hours,
        min_train_hours=args.min_train_hours,
    )
    dataset_payload = build_dataset_payload(train_frame, holdout_frame)

    baseline_results = []
    for run_name, prediction_column in BASELINE_SPECS.items():
        baseline_results.append(
            run_baseline_candidate(
                run_name=run_name,
                prediction_column=prediction_column,
                train_frame=train_frame,
                holdout_frame=holdout_frame,
                folds=folds,
                seasonal_period=args.seasonal_period,
                cv_horizon_hours=args.cv_horizon_hours,
                dataset_payload=dataset_payload,
            )
        )

    best_baseline_holdout_mae = min(result["metrics"]["holdout_mae"] for result in baseline_results)
    reference_baseline_holdout_mae = next(
        result["metrics"]["holdout_mae"]
        for result in baseline_results
        if result["run_name"] == REFERENCE_BASELINE_NAME
    )
    baseline_context = {
        "best_baseline_holdout_mae": float(best_baseline_holdout_mae),
        "reference_baseline_holdout_mae": float(reference_baseline_holdout_mae),
    }

    from lightgbm import LGBMRegressor
    from xgboost import XGBRegressor

    candidates = [
        (
            "lightgbm",
            lambda: LGBMRegressor(
                n_estimators=500,
                learning_rate=0.05,
                num_leaves=63,
                subsample=0.8,
                colsample_bytree=0.8,
                random_state=42,
                n_jobs=-1,
            ),
            {
                "n_estimators": 500,
                "learning_rate": 0.05,
                "num_leaves": 63,
                "subsample": 0.8,
                "colsample_bytree": 0.8,
                "random_state": 42,
            },
        ),
        (
            "xgboost",
            lambda: XGBRegressor(
                n_estimators=400,
                learning_rate=0.05,
                max_depth=8,
                subsample=0.8,
                colsample_bytree=0.8,
                objective="reg:squarederror",
                random_state=42,
                n_jobs=-1,
            ),
            {
                "n_estimators": 400,
                "learning_rate": 0.05,
                "max_depth": 8,
                "subsample": 0.8,
                "colsample_bytree": 0.8,
                "objective": "reg:squarederror",
                "random_state": 42,
            },
        ),
    ]

    for run_name, model_factory, model_params in candidates:
        run_model_candidate(
            run_name=run_name,
            model_factory=model_factory,
            model_params=model_params,
            train_frame=train_frame,
            holdout_frame=holdout_frame,
            folds=folds,
            seasonal_period=args.seasonal_period,
            cv_horizon_hours=args.cv_horizon_hours,
            dataset_payload=dataset_payload,
            baseline_context=baseline_context,
        )

    print(json.dumps({"dataset": dataset_payload, "baseline_context": baseline_context}, indent=2))


if __name__ == "__main__":
    main()
