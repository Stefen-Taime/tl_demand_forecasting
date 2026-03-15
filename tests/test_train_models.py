from __future__ import annotations

import pandas as pd
import pytest

from prediction_service.feature_builder import FEATURE_COLUMNS, TARGET_COLUMN, TIME_COLUMN, ZONE_ID_COLUMN
from scripts.train_models import build_expanding_folds, ensure_temporal_holdout, validate_frame


def make_frame(hours: int, start: str = "2024-01-01 00:00:00", zones: tuple[int, ...] = (1, 2)) -> pd.DataFrame:
    records = []
    for hour_index, hour in enumerate(pd.date_range(start=start, periods=hours, freq="h")):
        for zone_id in zones:
            record = {
                TIME_COLUMN: hour,
                ZONE_ID_COLUMN: zone_id,
                "zone_name": f"zone-{zone_id}",
                "borough": "Manhattan",
                "latitude": 40.7 + zone_id / 100.0,
                "longitude": -73.9 - zone_id / 100.0,
                TARGET_COLUMN: float(hour_index + zone_id),
            }
            for feature_index, column in enumerate(FEATURE_COLUMNS, start=1):
                record[column] = float(feature_index + zone_id)
            records.append(record)
    return pd.DataFrame(records)


def test_validate_frame_rejects_duplicate_zone_hour_rows() -> None:
    frame = make_frame(hours=2)
    duplicated = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)

    with pytest.raises(ValueError, match="duplicated zone/hour rows"):
        validate_frame(duplicated, "train dataset")


def test_build_expanding_folds_caps_requested_splits_to_available_history() -> None:
    frame = make_frame(hours=20)

    folds = build_expanding_folds(frame, requested_splits=4, horizon_hours=4, min_train_hours=8)

    assert len(folds) == 3
    assert folds[0]["train_rows"] == 16
    assert folds[0]["valid_rows"] == 8
    assert folds[-1]["valid_rows"] == 8


def test_ensure_temporal_holdout_detects_overlap() -> None:
    train_frame = make_frame(hours=10, start="2024-01-01 00:00:00")
    holdout_frame = make_frame(hours=2, start="2024-01-01 09:00:00")

    with pytest.raises(ValueError, match="Holdout leakage detected"):
        ensure_temporal_holdout(train_frame, holdout_frame)
