from __future__ import annotations

import argparse
import os
from pathlib import Path

import boto3
import requests


BASE_URL = "https://d37ci6vzurychx.cloudfront.net/trip-data"
ZONE_LOOKUP_URL = "https://d37ci6vzurychx.cloudfront.net/misc/taxi_zone_lookup.csv"


def download_file(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    destination.write_bytes(response.content)


def upload_to_s3(bucket: str, local_path: Path, remote_key: str) -> None:
    client = boto3.client("s3")
    client.upload_file(str(local_path), bucket, remote_key)


def build_month_key(taxi_type: str, year: int, month: int) -> str:
    return f"{taxi_type}_tripdata_{year}-{month:02d}.parquet"


def main() -> None:
    parser = argparse.ArgumentParser(description="Download TLC parquet data and optionally upload it to S3.")
    parser.add_argument("--taxi-type", default="yellow", choices=["yellow", "green", "fhv", "fhvhv"])
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--months", type=int, nargs="+", required=True)
    parser.add_argument("--raw-dir", default="data/raw")
    parser.add_argument("--upload-s3", action="store_true")
    parser.add_argument("--s3-bucket", default=os.getenv("S3_BUCKET"))
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir)
    for month in args.months:
        filename = build_month_key(args.taxi_type, args.year, month)
        url = f"{BASE_URL}/{filename}"
        destination = raw_dir / filename
        print(f"Downloading {url}")
        download_file(url, destination)
        if args.upload_s3:
            if not args.s3_bucket:
                raise ValueError("--upload-s3 requires --s3-bucket or S3_BUCKET.")
            upload_to_s3(args.s3_bucket, destination, f"raw/{filename}")
            print(f"Uploaded to s3://{args.s3_bucket}/raw/{filename}")

    zone_lookup_path = raw_dir / "taxi_zone_lookup.csv"
    if not zone_lookup_path.exists():
        print(f"Downloading {ZONE_LOOKUP_URL}")
        download_file(ZONE_LOOKUP_URL, zone_lookup_path)
        if args.upload_s3 and args.s3_bucket:
            upload_to_s3(args.s3_bucket, zone_lookup_path, "raw/taxi_zone_lookup.csv")


if __name__ == "__main__":
    main()
