from __future__ import annotations

from scripts.check_quality import evaluate_quality


def test_evaluate_quality_passes_when_all_checks_are_met() -> None:
    report = evaluate_quality(
        best_run={
            "run_id": "run-1",
            "model_type": "xgboost",
            "holdout_mae": 6.5,
            "holdout_mase": 0.4,
            "cv_mae_std": 0.2,
            "holdout_mae_improvement_vs_best_baseline_pct": 0.45,
            "baselines": [{"name": "b1"}, {"name": "b2"}, {"name": "b3"}],
        },
        promotion_decision={
            "approved_for_champion": True,
            "gates": {
                "beats_best_baseline": True,
                "holdout_mase_lt_threshold": True,
                "beats_current_champion": True,
            },
        },
        config={
            "allowed_model_types": ["xgboost", "lightgbm"],
            "max_holdout_mae": 8.0,
            "max_holdout_mase": 1.0,
            "max_cv_mae_std": 1.0,
            "min_holdout_improvement_vs_best_baseline_pct": 0.1,
            "min_baseline_count": 3,
            "require_approved_promotion": True,
            "require_all_promotion_gates": True,
        },
    )

    assert report["passed"] is True


def test_evaluate_quality_fails_when_model_does_not_clear_thresholds() -> None:
    report = evaluate_quality(
        best_run={
            "run_id": "run-2",
            "model_type": "prophet",
            "holdout_mae": 12.0,
            "holdout_mase": 1.2,
            "cv_mae_std": 1.8,
            "holdout_mae_improvement_vs_best_baseline_pct": 0.01,
            "baselines": [{"name": "b1"}],
        },
        promotion_decision={
            "approved_for_champion": False,
            "gates": {"beats_best_baseline": False},
        },
        config={
            "allowed_model_types": ["xgboost", "lightgbm"],
            "max_holdout_mae": 8.0,
            "max_holdout_mase": 1.0,
            "max_cv_mae_std": 1.0,
            "min_holdout_improvement_vs_best_baseline_pct": 0.1,
            "min_baseline_count": 3,
            "require_approved_promotion": True,
            "require_all_promotion_gates": True,
        },
    )

    assert report["passed"] is False
    assert any(check["name"] == "allowed_model_type" and not check["passed"] for check in report["checks"])
