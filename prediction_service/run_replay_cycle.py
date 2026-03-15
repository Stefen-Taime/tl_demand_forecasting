from __future__ import annotations

import io
import os
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone

import boto3
import mlflow
import mlflow.pyfunc
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

from feature_builder import TARGET_COLUMN, TIME_COLUMN, ZONE_ID_COLUMN, build_model_matrix


@dataclass
class Settings:
    db_uri: str
    mlflow_tracking_uri: str
    model_name: str
    model_alias: str
    s3_bucket: str | None
    holdout_key: str
    local_holdout_path: str


def load_settings() -> Settings:
    return Settings(
        db_uri=os.environ["PREDICTIONS_DB_URI"],
        mlflow_tracking_uri=os.getenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000"),
        model_name=os.getenv("MODEL_NAME", "tlc-demand-forecasting"),
        model_alias=os.getenv("MODEL_ALIAS", "champion"),
        s3_bucket=os.getenv("S3_BUCKET"),
        holdout_key=os.getenv("HOLDOUT_KEY", "holdout/holdout_features.parquet"),
        local_holdout_path=os.getenv("LOCAL_HOLDOUT_PATH", "data/holdout/holdout_features.parquet"),
    )


def load_holdout_frame(settings: Settings) -> pd.DataFrame:
    if settings.s3_bucket:
        client = boto3.client("s3")
        response = client.get_object(Bucket=settings.s3_bucket, Key=settings.holdout_key)
        return pd.read_parquet(io.BytesIO(response["Body"].read()))

    if not os.path.exists(settings.local_holdout_path):
        raise FileNotFoundError(
            f"Holdout file not found: {settings.local_holdout_path}. "
            "Build features first or set S3_BUCKET/HOLDOUT_KEY."
        )
    return pd.read_parquet(settings.local_holdout_path)


def get_connection(settings: Settings):
    return psycopg2.connect(settings.db_uri)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay holdout predictions into PostgreSQL.")
    parser.add_argument(
        "--cycles",
        type=int,
        default=1,
        help="Number of replay cycles to execute in this process.",
    )
    parser.add_argument(
        "--until-wrap",
        action="store_true",
        help="Replay until the holdout window wraps back to the starting hour.",
    )
    parser.add_argument(
        "--prune-window",
        action="store_true",
        help="Delete replay rows for the active alias that fall outside the current holdout window.",
    )
    args = parser.parse_args()
    if args.cycles < 1:
        parser.error("--cycles must be >= 1")
    if args.until_wrap and args.cycles != 1:
        parser.error("--cycles and --until-wrap are mutually exclusive")
    return args


def ensure_replay_state(conn, available_hours: list[pd.Timestamp]) -> pd.Timestamp:
    with conn.cursor() as cur:
        cur.execute("SELECT current_hour FROM replay_state WHERE id = 1")
        row = cur.fetchone()
        if row:
            return pd.Timestamp(row[0])

        initial_hour = available_hours[0].to_pydatetime()
        cur.execute(
            """
            INSERT INTO replay_state (id, current_hour, updated_at)
            VALUES (1, %s, NOW())
            ON CONFLICT (id) DO NOTHING
            """,
            (initial_hour,),
        )
        conn.commit()
        return pd.Timestamp(initial_hour)


def load_model(settings: Settings):
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    model_uri = f"models:/{settings.model_name}@{settings.model_alias}"
    model = mlflow.pyfunc.load_model(model_uri)
    client = mlflow.tracking.MlflowClient(settings.mlflow_tracking_uri)
    version = client.get_model_version_by_alias(settings.model_name, settings.model_alias)
    return model, version.version


def upsert_predictions(conn, frame: pd.DataFrame, model_name: str, model_version: str, model_alias: str) -> int:
    rows = []
    for record in frame.to_dict(orient="records"):
        rows.append(
            (
                record[TIME_COLUMN].to_pydatetime(),
                int(record[ZONE_ID_COLUMN]),
                record["zone_name"],
                record.get("borough"),
                None if pd.isna(record.get("latitude")) else float(record.get("latitude")),
                None if pd.isna(record.get("longitude")) else float(record.get("longitude")),
                float(record["predicted_trips"]),
                float(record[TARGET_COLUMN]),
                float(record["absolute_error"]),
                model_name,
                str(model_version),
                model_alias,
                True,
                "reconciled",
            )
        )

    statement = """
        INSERT INTO zone_predictions (
            target_hour,
            zone_id,
            zone_name,
            borough,
            latitude,
            longitude,
            predicted_trips,
            actual_trips,
            absolute_error,
            model_name,
            model_version,
            model_alias,
            replay_mode,
            status
        ) VALUES %s
        ON CONFLICT (target_hour, zone_id, model_alias) DO UPDATE
        SET
            predicted_trips = EXCLUDED.predicted_trips,
            actual_trips = EXCLUDED.actual_trips,
            absolute_error = EXCLUDED.absolute_error,
            model_name = EXCLUDED.model_name,
            model_version = EXCLUDED.model_version,
            replay_mode = EXCLUDED.replay_mode,
            status = EXCLUDED.status,
            generated_at = NOW()
    """
    with conn.cursor() as cur:
        execute_values(cur, statement, rows)
    conn.commit()
    return len(rows)


def advance_replay_state(conn, next_hour: pd.Timestamp) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO replay_state (id, current_hour, updated_at)
            VALUES (1, %s, NOW())
            ON CONFLICT (id) DO UPDATE
            SET current_hour = EXCLUDED.current_hour,
                updated_at = NOW()
            """,
            (next_hour.to_pydatetime(),),
        )
    conn.commit()


def prune_replay_window(conn, settings: Settings, available_hours: list[pd.Timestamp]) -> int:
    lower_bound = available_hours[0].to_pydatetime()
    upper_bound = available_hours[-1].to_pydatetime()
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM zone_predictions
            WHERE model_alias = %s
              AND replay_mode = TRUE
              AND (target_hour < %s OR target_hour > %s)
            """,
            (settings.model_alias, lower_bound, upper_bound),
        )
        deleted_rows = cur.rowcount
    conn.commit()
    return deleted_rows


def resolve_current_hour(conn, available_hours: list[pd.Timestamp]) -> pd.Timestamp:
    current_hour = ensure_replay_state(conn, available_hours)
    if current_hour not in available_hours:
        return available_hours[0]
    return current_hour


def run_cycle(
    conn,
    settings: Settings,
    holdout: pd.DataFrame,
    available_hours: list[pd.Timestamp],
    model,
    model_version: str,
) -> tuple[pd.Timestamp, pd.Timestamp, int]:
    current_hour = resolve_current_hour(conn, available_hours)
    selected = holdout[holdout[TIME_COLUMN] == current_hour].copy()

    selected["predicted_trips"] = model.predict(build_model_matrix(selected))
    selected["predicted_trips"] = selected["predicted_trips"].clip(lower=0)
    selected["absolute_error"] = (selected["predicted_trips"] - selected[TARGET_COLUMN]).abs()

    inserted = upsert_predictions(
        conn,
        selected,
        model_name=settings.model_name,
        model_version=str(model_version),
        model_alias=settings.model_alias,
    )

    current_index = available_hours.index(current_hour)
    next_hour = available_hours[(current_index + 1) % len(available_hours)]
    advance_replay_state(conn, next_hour)

    print(
        f"[{datetime.now(timezone.utc).isoformat()}] replayed {inserted} rows for "
        f"{current_hour.isoformat()} -> next {next_hour.isoformat()}"
    )
    return current_hour, next_hour, inserted


def run() -> None:
    args = parse_args()
    settings = load_settings()
    holdout = load_holdout_frame(settings).sort_values([TIME_COLUMN, ZONE_ID_COLUMN]).reset_index(drop=True)
    holdout[TIME_COLUMN] = pd.to_datetime(holdout[TIME_COLUMN], utc=False)
    available_hours = [pd.Timestamp(value) for value in sorted(holdout[TIME_COLUMN].dropna().unique())]
    if not available_hours:
        raise RuntimeError("Holdout dataset is empty.")

    conn = get_connection(settings)
    try:
        if args.prune_window:
            deleted_rows = prune_replay_window(conn, settings, available_hours)
            print(
                f"[{datetime.now(timezone.utc).isoformat()}] pruned {deleted_rows} stale replay row(s) "
                f"for alias {settings.model_alias}"
            )

        model, model_version = load_model(settings)
        start_hour = resolve_current_hour(conn, available_hours)
        cycles_run = 0
        total_rows = 0

        while True:
            _, next_hour, inserted = run_cycle(
                conn,
                settings,
                holdout,
                available_hours,
                model,
                str(model_version),
            )
            cycles_run += 1
            total_rows += inserted

            if args.until_wrap and next_hour == start_hour:
                break
            if not args.until_wrap and cycles_run >= args.cycles:
                break

        print(
            f"[{datetime.now(timezone.utc).isoformat()}] completed {cycles_run} cycle(s), "
            f"wrote {total_rows} rows total"
        )
    finally:
        conn.close()


if __name__ == "__main__":
    run()
