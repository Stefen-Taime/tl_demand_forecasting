from __future__ import annotations

from typing import Iterable

import pandas as pd

TARGET_COLUMN = "target_trips"
TIME_COLUMN = "target_hour"
ZONE_ID_COLUMN = "zone_id"

METADATA_COLUMNS = [
    TIME_COLUMN,
    ZONE_ID_COLUMN,
    "zone_name",
    "borough",
    "latitude",
    "longitude",
    TARGET_COLUMN,
]

FEATURE_COLUMNS = [
    "hour_of_day",
    "day_of_week",
    "day_of_month",
    "month",
    "is_weekend",
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
    "lag_1h",
    "lag_2h",
    "lag_24h",
    "lag_168h",
    "rolling_mean_6h",
    "rolling_mean_24h",
    "rolling_std_24h",
    "trend_ratio",
]


def ensure_feature_columns(df: pd.DataFrame) -> pd.DataFrame:
    frame = df.copy()
    for column in FEATURE_COLUMNS:
        if column not in frame.columns:
            frame[column] = 0.0
    return frame


def build_model_matrix(df: pd.DataFrame) -> pd.DataFrame:
    frame = ensure_feature_columns(df)
    return frame[FEATURE_COLUMNS].astype(float)


def required_columns() -> Iterable[str]:
    return METADATA_COLUMNS + FEATURE_COLUMNS
