# Databricks Notebooks

Ces notebooks sont optionnels. Ils servent a:

- faire une EDA rapide
- prototyper du feature engineering
- montrer une variante notebook du projet

Ils ne remplacent pas les scripts de reference dans `scripts/`.

## Notebooks fournis

- `01_eda.py`
- `02_feature_prototype.py`
- `03_sandbox_training.py`

## Utilisation

1. Importer les fichiers `.py` dans Databricks.
2. Adapter les chemins d'entree vers DBFS, Unity Catalog ou un volume temporaire.
3. Ne pas faire dependre le projet principal de ces notebooks.
