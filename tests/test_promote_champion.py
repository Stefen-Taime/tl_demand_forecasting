from __future__ import annotations

import pandas as pd

from scripts.promote_champion import rank_candidates


def test_rank_candidates_filters_and_orders_best_model_first() -> None:
    runs = pd.DataFrame(
        [
            {
                "run_id": "baseline-1",
                "params.model_type": "seasonal_naive_24h",
                "metrics.holdout_mae": 10.0,
                "metrics.holdout_mase": 0.8,
                "metrics.cv_mae_mean": 10.5,
                "metrics.holdout_mae_improvement_vs_best_baseline_pct": 0.0,
            },
            {
                "run_id": "model-1",
                "params.model_type": "xgboost",
                "metrics.holdout_mae": 7.0,
                "metrics.holdout_mase": 0.4,
                "metrics.cv_mae_mean": 7.2,
                "metrics.holdout_mae_improvement_vs_best_baseline_pct": 0.3,
            },
            {
                "run_id": "model-2",
                "params.model_type": "lightgbm",
                "metrics.holdout_mae": 8.0,
                "metrics.holdout_mase": 0.5,
                "metrics.cv_mae_mean": 8.2,
                "metrics.holdout_mae_improvement_vs_best_baseline_pct": 0.2,
            },
        ]
    )

    ranked = rank_candidates(runs)

    assert list(ranked["run_id"]) == ["model-1", "model-2"]
