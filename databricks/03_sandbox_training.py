# Databricks notebook source
# MAGIC %md
# MAGIC # Sandbox Training
# MAGIC
# MAGIC Notebook optionnel de comparaison rapide. Le chemin de production pour le tracking reste le workflow local + tunnel MLflow.

# COMMAND ----------

dbutils.widgets.text("features_path", "/tmp/tlc/features")
features_path = dbutils.widgets.get("features_path")

# COMMAND ----------

features_df = spark.read.parquet(features_path)
pdf = features_df.toPandas()
pdf.head()

# COMMAND ----------

from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import train_test_split
from lightgbm import LGBMRegressor

feature_columns = [
    "hour_of_day",
    "day_of_week",
    "lag_1h",
    "lag_24h",
    "rolling_mean_24h",
]

x = pdf[feature_columns]
y = pdf["target_trips"]
x_train, x_valid, y_train, y_valid = train_test_split(x, y, test_size=0.2, shuffle=False)

model = LGBMRegressor(n_estimators=200, learning_rate=0.05, random_state=42)
model.fit(x_train, y_train)
predictions = model.predict(x_valid)
mae = mean_absolute_error(y_valid, predictions)
print({"mae": mae})
