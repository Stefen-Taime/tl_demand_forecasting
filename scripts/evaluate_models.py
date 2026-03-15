from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import mlflow
import pandas as pd

ELIGIBLE_MODEL_TYPES = {"lightgbm", "xgboost"}


def rank_runs(runs: pd.DataFrame) -> pd.DataFrame:
    ranked = runs.copy()
    if "metrics.holdout_mae" in ranked.columns:
        ranked = ranked[ranked["metrics.holdout_mae"].notna()].copy()
    if ranked.empty:
        raise RuntimeError("No runs with holdout metrics found in MLflow.")
    return ranked.sort_values(
        by=[
            "metrics.holdout_mae",
            "metrics.cv_mae_mean",
            "metrics.holdout_mase",
        ],
        ascending=[True, True, True],
        na_position="last",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Export ranked validation summaries from MLflow.")
    parser.add_argument("--experiment", default="tlc-demand-forecasting")
    parser.add_argument("--output-dir", default="reports")
    args = parser.parse_args()

    mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000"))
    experiment = mlflow.get_experiment_by_name(args.experiment)
    if experiment is None:
        raise ValueError(f"Experiment not found: {args.experiment}")

    runs = mlflow.search_runs(
        experiment_ids=[experiment.experiment_id],
        filter_string="attributes.status = 'FINISHED'",
    )
    if runs.empty:
        raise RuntimeError("No runs found in MLflow.")

    ranked = rank_runs(runs)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    summary_columns = [
        "run_id",
        "tags.mlflow.runName",
        "tags.candidate_kind",
        "params.model_type",
        "metrics.cv_mae_mean",
        "metrics.cv_mae_std",
        "metrics.cv_mase_mean",
        "metrics.holdout_mae",
        "metrics.holdout_rmse",
        "metrics.holdout_mase",
        "metrics.holdout_mae_improvement_vs_best_baseline_pct",
        "start_time",
    ]
    summary_path = output_dir / "run_summary.csv"
    available_summary_columns = [column for column in summary_columns if column in ranked.columns]
    ranked[available_summary_columns].to_csv(summary_path, index=False)

    model_runs = ranked[
        ranked["params.model_type"].isin(ELIGIBLE_MODEL_TYPES)
        & (ranked.get("tags.candidate_kind", pd.Series(index=ranked.index, dtype=object)) == "model")
    ].copy()
    if model_runs.empty:
        raise RuntimeError("No eligible model runs found in MLflow.")

    best_model = model_runs.iloc[0]
    baseline_rows = ranked[
        ranked.get("tags.candidate_kind", pd.Series(index=ranked.index, dtype=object)) == "baseline"
    ].copy()

    best_payload = {
        "run_id": best_model["run_id"],
        "run_name": best_model.get("tags.mlflow.runName"),
        "model_type": best_model.get("params.model_type"),
        "cv_mae_mean": best_model.get("metrics.cv_mae_mean"),
        "cv_mae_std": best_model.get("metrics.cv_mae_std"),
        "cv_mase_mean": best_model.get("metrics.cv_mase_mean"),
        "holdout_mae": best_model.get("metrics.holdout_mae"),
        "holdout_rmse": best_model.get("metrics.holdout_rmse"),
        "holdout_mase": best_model.get("metrics.holdout_mase"),
        "holdout_mae_improvement_vs_best_baseline_pct": best_model.get(
            "metrics.holdout_mae_improvement_vs_best_baseline_pct"
        ),
        "reference_baseline_holdout_mae": best_model.get("metrics.baseline_reference_holdout_mae"),
        "best_baseline_holdout_mae": best_model.get("metrics.baseline_best_holdout_mae"),
        "baselines": [],
    }
    for _, baseline in baseline_rows.iterrows():
        best_payload["baselines"].append(
            {
                "run_name": baseline.get("tags.mlflow.runName"),
                "holdout_mae": baseline.get("metrics.holdout_mae"),
                "holdout_mase": baseline.get("metrics.holdout_mase"),
            }
        )

    best_path = output_dir / "best_run.json"
    best_path.write_text(json.dumps(best_payload, indent=2))

    print(f"Wrote {summary_path}")
    print(f"Wrote {best_path}")


if __name__ == "__main__":
    main()
