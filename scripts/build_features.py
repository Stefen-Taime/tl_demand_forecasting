from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys

import boto3
import duckdb
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from prediction_service.feature_builder import FEATURE_COLUMNS, TARGET_COLUMN, TIME_COLUMN, ZONE_ID_COLUMN


def upload_if_needed(bucket: str | None, local_path: Path, remote_key: str) -> None:
    if not bucket:
        return
    boto3.client("s3").upload_file(str(local_path), bucket, remote_key)
    print(f"Uploaded {local_path} -> s3://{bucket}/{remote_key}")


def load_zone_lookup(raw_dir: Path) -> pd.DataFrame:
    lookup_path = raw_dir / "taxi_zone_lookup.csv"
    if not lookup_path.exists():
        return pd.DataFrame(columns=[ZONE_ID_COLUMN, "zone_name", "borough", "latitude", "longitude"])
    frame = pd.read_csv(lookup_path)
    frame = frame.rename(columns={"LocationID": ZONE_ID_COLUMN, "Zone": "zone_name", "Borough": "borough"})
    for field in ["latitude", "longitude"]:
        if field not in frame.columns:
            frame[field] = np.nan
    centroids_path = raw_dir / "taxi_zone_centroids.csv"
    if centroids_path.exists():
        centroids = pd.read_csv(centroids_path)
        centroids = centroids.rename(columns={"LocationID": ZONE_ID_COLUMN})
        frame = frame.merge(centroids[[ZONE_ID_COLUMN, "latitude", "longitude"]], on=ZONE_ID_COLUMN, how="left", suffixes=("", "_geo"))
        if "latitude_geo" in frame.columns:
            frame["latitude"] = frame["latitude_geo"].combine_first(frame["latitude"])
            frame["longitude"] = frame["longitude_geo"].combine_first(frame["longitude"])
            frame = frame.drop(columns=["latitude_geo", "longitude_geo"])
    return frame[[ZONE_ID_COLUMN, "zone_name", "borough", "latitude", "longitude"]]


def infer_expected_bounds(raw_dir: Path) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    pattern = re.compile(r".*_tripdata_(\d{4})-(\d{2})\.parquet$")
    month_starts: list[pd.Timestamp] = []
    for path in raw_dir.glob("*_tripdata_*.parquet"):
        match = pattern.match(path.name)
        if not match:
            continue
        year, month = match.groups()
        month_starts.append(pd.Timestamp(year=int(year), month=int(month), day=1))

    if not month_starts:
        return None

    start = min(month_starts)
    end = max(month_starts) + pd.offsets.MonthBegin(1)
    return start, end


def aggregate_hourly(raw_dir: Path) -> pd.DataFrame:
    parquet_files = sorted(str(path) for path in raw_dir.glob("*_tripdata_*.parquet"))
    if not parquet_files:
        raise FileNotFoundError("No TLC parquet files found in data/raw. Run ingest_tlc.py first.")

    file_list = ", ".join(f"'{path}'" for path in parquet_files)
    bounds = infer_expected_bounds(raw_dir)
    date_filter = ""
    if bounds is not None:
        start, end = bounds
        date_filter = (
            f"AND tpep_pickup_datetime >= TIMESTAMP '{start.strftime('%Y-%m-%d %H:%M:%S')}' "
            f"AND tpep_pickup_datetime < TIMESTAMP '{end.strftime('%Y-%m-%d %H:%M:%S')}'"
        )
    query = f"""
        SELECT
            DATE_TRUNC('hour', tpep_pickup_datetime) AS target_hour,
            CAST(PULocationID AS INTEGER) AS zone_id,
            COUNT(*) AS target_trips
        FROM read_parquet([{file_list}])
        WHERE tpep_pickup_datetime IS NOT NULL
          AND PULocationID IS NOT NULL
          {date_filter}
        GROUP BY 1, 2
        ORDER BY 1, 2
    """
    relation = duckdb.sql(query)
    return relation.df()


def engineer_features(hourly: pd.DataFrame, zone_lookup: pd.DataFrame) -> pd.DataFrame:
    frame = hourly.copy()
    frame[TIME_COLUMN] = pd.to_datetime(frame[TIME_COLUMN])
    frame = frame.sort_values([ZONE_ID_COLUMN, TIME_COLUMN]).reset_index(drop=True)
    frame = frame.merge(zone_lookup, on=ZONE_ID_COLUMN, how="left")

    frame["hour_of_day"] = frame[TIME_COLUMN].dt.hour
    frame["day_of_week"] = frame[TIME_COLUMN].dt.dayofweek
    frame["day_of_month"] = frame[TIME_COLUMN].dt.day
    frame["month"] = frame[TIME_COLUMN].dt.month
    frame["is_weekend"] = (frame["day_of_week"] >= 5).astype(int)
    frame["hour_sin"] = np.sin(2 * np.pi * frame["hour_of_day"] / 24)
    frame["hour_cos"] = np.cos(2 * np.pi * frame["hour_of_day"] / 24)
    frame["dow_sin"] = np.sin(2 * np.pi * frame["day_of_week"] / 7)
    frame["dow_cos"] = np.cos(2 * np.pi * frame["day_of_week"] / 7)

    grouped = frame.groupby(ZONE_ID_COLUMN)[TARGET_COLUMN]
    frame["lag_1h"] = grouped.shift(1)
    frame["lag_2h"] = grouped.shift(2)
    frame["lag_24h"] = grouped.shift(24)
    frame["lag_168h"] = grouped.shift(168)

    lagged_series = frame["lag_1h"]
    frame["rolling_mean_6h"] = lagged_series.groupby(frame[ZONE_ID_COLUMN]).transform(
        lambda series: series.rolling(window=6, min_periods=1).mean()
    )
    frame["rolling_mean_24h"] = lagged_series.groupby(frame[ZONE_ID_COLUMN]).transform(
        lambda series: series.rolling(window=24, min_periods=1).mean()
    )
    frame["rolling_std_24h"] = lagged_series.groupby(frame[ZONE_ID_COLUMN]).transform(
        lambda series: series.rolling(window=24, min_periods=2).std()
    )
    frame["trend_ratio"] = frame["rolling_mean_6h"] / frame["rolling_mean_24h"].replace(0, np.nan)

    frame["zone_name"] = frame["zone_name"].fillna("Unknown")
    frame["borough"] = frame["borough"].fillna("Unknown")
    frame[FEATURE_COLUMNS] = frame[FEATURE_COLUMNS].fillna(0.0)
    return frame


def split_datasets(frame: pd.DataFrame, holdout_hours: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    sorted_hours = sorted(frame[TIME_COLUMN].drop_duplicates())
    if len(sorted_hours) <= holdout_hours:
        raise ValueError("Not enough hourly points to carve a holdout split.")
    cutoff = sorted_hours[-holdout_hours]
    train = frame[frame[TIME_COLUMN] < cutoff].copy()
    holdout = frame[frame[TIME_COLUMN] >= cutoff].copy()
    return train, holdout


def main() -> None:
    parser = argparse.ArgumentParser(description="Build zone x hour features from raw TLC parquet files.")
    parser.add_argument("--raw-dir", default="data/raw")
    parser.add_argument("--processed-dir", default="data/processed")
    parser.add_argument("--holdout-dir", default="data/holdout")
    parser.add_argument("--holdout-hours", type=int, default=24 * 7)
    parser.add_argument("--s3-bucket", default=os.getenv("S3_BUCKET"))
    parser.add_argument("--upload-s3", action="store_true")
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir)
    processed_dir = Path(args.processed_dir)
    holdout_dir = Path(args.holdout_dir)
    processed_dir.mkdir(parents=True, exist_ok=True)
    holdout_dir.mkdir(parents=True, exist_ok=True)

    hourly = aggregate_hourly(raw_dir)
    zone_lookup = load_zone_lookup(raw_dir)
    features = engineer_features(hourly, zone_lookup)
    train, holdout = split_datasets(features, args.holdout_hours)

    features_path = processed_dir / "features.parquet"
    train_path = processed_dir / "train_features.parquet"
    holdout_path = holdout_dir / "holdout_features.parquet"

    features.to_parquet(features_path, index=False)
    train.to_parquet(train_path, index=False)
    holdout.to_parquet(holdout_path, index=False)

    print(f"Wrote {features_path}")
    print(f"Wrote {train_path}")
    print(f"Wrote {holdout_path}")

    if args.upload_s3:
        if not args.s3_bucket:
            raise ValueError("--upload-s3 requires --s3-bucket or S3_BUCKET.")
        upload_if_needed(args.s3_bucket, features_path, "features/features.parquet")
        upload_if_needed(args.s3_bucket, train_path, "features/train_features.parquet")
        upload_if_needed(args.s3_bucket, holdout_path, "holdout/holdout_features.parquet")


if __name__ == "__main__":
    main()
