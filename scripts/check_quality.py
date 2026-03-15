from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enforce quality gates on exported ML validation artifacts.")
    parser.add_argument("--config", default="config/quality_gates.json")
    parser.add_argument("--best-run", default="reports/best_run.json")
    parser.add_argument("--promotion-decision", default="reports/promotion_decision.json")
    parser.add_argument("--output-path", default="reports/quality_gate_report.json")
    return parser.parse_args()


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text())


def build_check(name: str, passed: bool, observed: Any, expected: Any) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "observed": observed,
        "expected": expected,
    }


def evaluate_quality(
    best_run: dict[str, Any],
    promotion_decision: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    baselines = best_run.get("baselines", [])
    gates = promotion_decision.get("gates", {})

    checks = [
        build_check(
            "allowed_model_type",
            best_run.get("model_type") in config["allowed_model_types"],
            best_run.get("model_type"),
            config["allowed_model_types"],
        ),
        build_check(
            "holdout_mae",
            float(best_run["holdout_mae"]) <= float(config["max_holdout_mae"]),
            float(best_run["holdout_mae"]),
            {"lte": float(config["max_holdout_mae"])},
        ),
        build_check(
            "holdout_mase",
            float(best_run["holdout_mase"]) <= float(config["max_holdout_mase"]),
            float(best_run["holdout_mase"]),
            {"lte": float(config["max_holdout_mase"])},
        ),
        build_check(
            "cv_mae_std",
            float(best_run["cv_mae_std"]) <= float(config["max_cv_mae_std"]),
            float(best_run["cv_mae_std"]),
            {"lte": float(config["max_cv_mae_std"])},
        ),
        build_check(
            "holdout_improvement_vs_best_baseline_pct",
            float(best_run["holdout_mae_improvement_vs_best_baseline_pct"])
            >= float(config["min_holdout_improvement_vs_best_baseline_pct"]),
            float(best_run["holdout_mae_improvement_vs_best_baseline_pct"]),
            {"gte": float(config["min_holdout_improvement_vs_best_baseline_pct"])},
        ),
        build_check(
            "baseline_count",
            len(baselines) >= int(config["min_baseline_count"]),
            len(baselines),
            {"gte": int(config["min_baseline_count"])},
        ),
    ]

    if config.get("require_approved_promotion", True):
        checks.append(
            build_check(
                "promotion_approved",
                bool(promotion_decision.get("approved_for_champion")),
                bool(promotion_decision.get("approved_for_champion")),
                True,
            )
        )

    if config.get("require_all_promotion_gates", True):
        checks.append(
            build_check(
                "promotion_gates",
                bool(gates) and all(bool(value) for value in gates.values()),
                gates,
                "all true",
            )
        )

    return {
        "passed": all(check["passed"] for check in checks),
        "model_type": best_run.get("model_type"),
        "run_id": best_run.get("run_id"),
        "checks": checks,
    }


def main() -> None:
    args = parse_args()
    config = load_json(args.config)
    best_run = load_json(args.best_run)
    promotion_decision = load_json(args.promotion_decision)

    report = evaluate_quality(best_run, promotion_decision, config)
    output_path = Path(args.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))

    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
