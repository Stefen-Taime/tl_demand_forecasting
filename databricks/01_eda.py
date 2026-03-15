# Databricks notebook source
# MAGIC %md
# MAGIC # TLC EDA
# MAGIC
# MAGIC Notebook optionnel pour inspecter les donnees TLC et verifier les distributions de base.

# COMMAND ----------

dbutils.widgets.text("input_path", "/tmp/tlc/raw/*.parquet")
input_path = dbutils.widgets.get("input_path")

# COMMAND ----------

from pyspark.sql import functions as F

raw_df = spark.read.parquet(input_path)
display(raw_df.limit(10))

# COMMAND ----------

hourly_df = (
    raw_df
    .withColumn("target_hour", F.date_trunc("hour", F.col("tpep_pickup_datetime")))
    .groupBy("target_hour", "PULocationID")
    .agg(F.count("*").alias("target_trips"))
    .orderBy("target_hour", "PULocationID")
)

display(hourly_df.limit(20))

# COMMAND ----------

summary_df = (
    hourly_df
    .groupBy("PULocationID")
    .agg(
        F.avg("target_trips").alias("avg_trips"),
        F.max("target_trips").alias("max_trips"),
        F.count("*").alias("n_hours")
    )
    .orderBy(F.desc("avg_trips"))
)

display(summary_df.limit(20))
