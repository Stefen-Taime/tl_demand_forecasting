from __future__ import annotations

import argparse
import os
import tempfile
import zipfile
from pathlib import Path

import boto3
import pandas as pd
import requests
import shapefile
from pyproj import Transformer
from shapely.geometry import shape


TAXI_ZONES_URL = "https://d37ci6vzurychx.cloudfront.net/misc/taxi_zones.zip"


def download_zip(destination: Path) -> None:
    response = requests.get(TAXI_ZONES_URL, timeout=120)
    response.raise_for_status()
    destination.write_bytes(response.content)


def build_centroids(zip_path: Path) -> pd.DataFrame:
    with tempfile.TemporaryDirectory() as temp_dir:
        with zipfile.ZipFile(zip_path, "r") as archive:
            archive.extractall(temp_dir)

        shp_path = Path(temp_dir) / "taxi_zones" / "taxi_zones.shp"
        reader = shapefile.Reader(str(shp_path))
        transformer = Transformer.from_crs("EPSG:2263", "EPSG:4326", always_xy=True)

        rows = []
        for shape_record in reader.iterShapeRecords():
            record = shape_record.record.as_dict()
            geometry = shape(shape_record.shape.__geo_interface__)
            centroid = geometry.centroid
            longitude, latitude = transformer.transform(centroid.x, centroid.y)
            rows.append(
                {
                    "LocationID": int(record["LocationID"]),
                    "latitude": float(latitude),
                    "longitude": float(longitude),
                }
            )

    return pd.DataFrame(rows).sort_values("LocationID").reset_index(drop=True)


def upload_to_s3(bucket: str, local_path: Path, key: str) -> None:
    boto3.client("s3").upload_file(str(local_path), bucket, key)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build taxi zone centroid CSV from the official TLC shapefile.")
    parser.add_argument("--output", default="data/raw/taxi_zone_centroids.csv")
    parser.add_argument("--upload-s3", action="store_true")
    parser.add_argument("--s3-bucket", default=os.getenv("S3_BUCKET"))
    args = parser.parse_args()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as temp_dir:
        zip_path = Path(temp_dir) / "taxi_zones.zip"
        print(f"Downloading {TAXI_ZONES_URL}")
        download_zip(zip_path)
        centroids = build_centroids(zip_path)

    centroids.to_csv(output_path, index=False)
    print(f"Wrote {output_path}")

    if args.upload_s3:
        if not args.s3_bucket:
            raise ValueError("--upload-s3 requires --s3-bucket or S3_BUCKET.")
        upload_to_s3(args.s3_bucket, output_path, "raw/taxi_zone_centroids.csv")
        print(f"Uploaded {output_path} -> s3://{args.s3_bucket}/raw/taxi_zone_centroids.csv")


if __name__ == "__main__":
    main()
