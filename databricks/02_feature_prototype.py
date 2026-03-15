# Databricks notebook source
# MAGIC %md
# MAGIC # Feature Engineering Prototype
# MAGIC
# MAGIC Prototype notebook. La version de reference pour le projet reste `scripts/build_features.py`.

# COMMAND ----------

dbutils.widgets.text("input_path", "/tmp/tlc/raw/*.parquet")
dbutils.widgets.text("output_path", "/tmp/tlc/features")
input_path = dbutils.widgets.get("input_path")
output_path = dbutils.widgets.get("output_path")

# COMMAND ----------

from pyspark.sql import Window
from pyspark.sql import functions as F

hourly_df = (
    spark.read.parquet(input_path)
    .withColumn("target_hour", F.date_trunc("hour", F.col("tpep_pickup_datetime")))
    .groupBy("target_hour", "PULocationID")
    .agg(F.count("*").alias("target_trips"))
)

window_1h = Window.partitionBy("PULocationID").orderBy("target_hour")
window_24h = window_1h.rowsBetween(-24, -1)

features_df = (
    hourly_df
    .withColumn("hour_of_day", F.hour("target_hour"))
    .withColumn("day_of_week", F.dayofweek("target_hour"))
    .withColumn("lag_1h", F.lag("target_trips", 1).over(window_1h))
    .withColumn("lag_24h", F.lag("target_trips", 24).over(window_1h))
    .withColumn("rolling_mean_24h", F.avg("target_trips").over(window_24h))
    .fillna(0)
)

display(features_df.limit(20))

# COMMAND ----------

(
    features_df
    .write
    .mode("overwrite")
    .parquet(output_path)
)

print(f"Features written to {output_path}")
