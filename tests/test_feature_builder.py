from __future__ import annotations

import pandas as pd

from prediction_service.feature_builder import FEATURE_COLUMNS, build_model_matrix, ensure_feature_columns


def test_ensure_feature_columns_adds_missing_features() -> None:
    frame = pd.DataFrame({"zone_id": [1], "target_hour": ["2024-01-01 00:00:00"]})

    enriched = ensure_feature_columns(frame)

    for column in FEATURE_COLUMNS:
        assert column in enriched.columns
        assert float(enriched.iloc[0][column]) == 0.0


def test_build_model_matrix_returns_ordered_numeric_feature_frame() -> None:
    frame = pd.DataFrame({column: [index] for index, column in enumerate(FEATURE_COLUMNS, start=1)})

    matrix = build_model_matrix(frame)

    assert list(matrix.columns) == FEATURE_COLUMNS
    assert all(dtype.kind == "f" for dtype in matrix.dtypes)
