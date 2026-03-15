# TLC Demand Forecasting MLOps

Projet MLOps portfolio pour prevoir la demande TLC par `zone x heure`, suivre les experiments MLflow, promouvoir un modele `champion`, rejouer un holdout historique comme un flux pseudo-live, puis afficher predictions, observations et erreur dans Grafana.

Le projet est pense comme un systeme complet, pas comme un simple notebook:

- provisionnement infra avec Terraform
- configuration serveur avec Ansible
- pipeline data et ML en scripts Python
- tracking et registry avec MLflow
- monitoring metier avec Grafana
- CI/CD GitHub Actions vers `staging` et `production`

Le runbook d'execution est dans [`DEPLOYMENT.md`](/Users/stefen/tl_demand_forecasting/DEPLOYMENT.md). Ce `README` sert surtout a comprendre le systeme, son architecture, ses choix et le parcours de la donnee.

## 1. Ce que le projet cherche a demontrer

Le projet repond a une question simple:

> peut-on construire une stack MLOps credibile qui va de la donnee brute a un tableau de bord metier, avec un vrai processus de validation et de promotion modele ?

Ici, la reponse est oui, avec trois contraintes importantes:

- la source TLC publique n'est pas un vrai flux temps reel, donc le projet utilise un `replay historique`
- MLflow n'est pas expose publiquement; on y accede via tunnel SSH
- la promotion modele n'est pas basee sur "le score le plus joli", mais sur des baselines, un holdout gele et des quality gates

## 2. Architecture d'ensemble

### Vue simple

```text
                           +----------------------+
                           |   GitHub Actions     |
                           |  CI + CD + OIDC AWS  |
                           +----------+-----------+
                                      |
                                      v
+--------------------+      +-----------------------------+
| Machine locale     |      | AWS staging / production    |
|                    |      |                             |
| - ingestion TLC    |      | - EC2 m6i.large            |
| - feature eng      |----->| - PostgreSQL               |
| - training         | SSH  | - MLflow (localhost:5000)  |
| - evaluation       |      | - Grafana (:3000)          |
| - promotion        |      | - replay systemd timer     |
| - terraform        |      | - IAM role                 |
| - ansible          |      | - Security Group           |
+---------+----------+      +-------------+---------------+
          |                                 |
          |                                 v
          |                     +---------------------------+
          +-------------------->| S3                        |
                                | - raw/                    |
                                | - features/               |
                                | - holdout/                |
                                | - mlflow-artifacts/       |
                                | - reports/                |
                                +---------------------------+
```

### Vue data flow

```text
TLC parquet mensuels
    + taxi_zone_lookup.csv
    + taxi_zones shapefile
            |
            v
 scripts/ingest_tlc.py
 scripts/build_zone_centroids.py
            |
            v
 data/raw/*
            |
            v
 scripts/build_features.py
    -> aggregation zone x heure
    -> features calendaires
    -> features de lags / rolling
    -> split train / holdout
            |
            +------------------> data/processed/train_features.parquet
            +------------------> data/holdout/holdout_features.parquet
            |
            v
 scripts/train_models.py
    -> baselines
    -> LightGBM
    -> XGBoost
    -> MLflow experiment runs
            |
            v
 scripts/evaluate_models.py
    -> reports/run_summary.csv
    -> reports/best_run.json
            |
            v
 scripts/promote_champion.py
    -> MLflow Registry alias: champion
    -> reports/promotion_decision.json
            |
            v
 prediction_service/run_replay_cycle.py
    -> lit holdout
    -> charge models:/...@champion
    -> predit heure courante
    -> compare aux actuals holdout
    -> ecrit dans PostgreSQL
            |
            v
 Grafana
    -> geomap
    -> predicted vs actual
    -> MAE
    -> alertes
```

## 3. Parcours complet de la donnee

### Phase 1. Ingestion brute

Le script [`ingest_tlc.py`](/Users/stefen/tl_demand_forecasting/scripts/ingest_tlc.py) telecharge les fichiers TLC officiels par mois et, si demande, les pousse dans S3.

Ce qu'il fait:

- telecharge les fichiers `yellow_tripdata_YYYY-MM.parquet`
- telecharge `taxi_zone_lookup.csv`
- ecrit tout dans `data/raw/`
- peut uploader vers `s3://.../raw/`

Pourquoi cette phase existe:

- separer la source brute du reste du pipeline
- pouvoir rejouer ou reconstruire les features a partir d'une base stable
- garder un artefact simple a auditer

### Phase 2. Enrichissement geographique

Le script [`build_zone_centroids.py`](/Users/stefen/tl_demand_forecasting/scripts/build_zone_centroids.py) telecharge le shapefile officiel des taxi zones, calcule le centroid de chaque zone et produit `data/raw/taxi_zone_centroids.csv`.

Ce qu'il fait:

- telecharge `taxi_zones.zip`
- convertit les coordonnees depuis `EPSG:2263` vers `EPSG:4326`
- calcule `latitude` et `longitude` par `LocationID`
- permet au Geomap Grafana d'afficher les zones

Pourquoi cette phase existe:

- `taxi_zone_lookup.csv` ne contient pas les coordonnees
- sans centroides, la carte a peu ou pas de valeur

### Phase 3. Construction du dataset modele

Le script [`build_features.py`](/Users/stefen/tl_demand_forecasting/scripts/build_features.py) est le coeur de la preparation des donnees.

Ce qu'il fait:

- lit les parquets bruts avec DuckDB
- agrege les pickups en `target_trips` par `zone_id x heure`
- filtre les timestamps aberrants en se basant sur les bornes attendues deduites des noms de fichiers mensuels
- enrichit avec `zone_name`, `borough`, `latitude`, `longitude`
- construit les features temporelles et historiques
- split le dataset en:
  - `train_features.parquet`
  - `holdout_features.parquet`

Pourquoi cette phase existe:

- transformer des trajets individuels en un probleme de forecasting tabulaire
- preparer un holdout final qui ne sera pas utilise pour l'entrainement
- produire exactement les memes colonnes pour training et replay

### Fenetre de donnees actuellement utilisee

Au moment actuel du projet, les fichiers ingeres couvrent `6 mois` de `yellow taxi`:

- `janvier 2024`
- `fevrier 2024`
- `mars 2024`
- `avril 2024`
- `mai 2024`
- `juin 2024`

Le dataset construit couvre:

- `features` complets: du `1 janvier 2024 00:00:00` au `30 juin 2024 23:00:00`
- `train`: du `1 janvier 2024 00:00:00` au `23 juin 2024 23:00:00`
- `holdout`: du `24 juin 2024 00:00:00` au `30 juin 2024 23:00:00`

Autrement dit:

- le modele apprend sur `1 janvier -> 23 juin`
- il est ensuite evalue sur une semaine cachee `24 juin -> 30 juin`
- cette meme semaine sert ensuite au replay pseudo-live dans Grafana

### Phase 4. Entrainement des challengers

Le script [`train_models.py`](/Users/stefen/tl_demand_forecasting/scripts/train_models.py) entraine et compare les candidats.

Ce qu'il fait:

- charge `train_features.parquet` et `holdout_features.parquet`
- verifie l'integrite des donnees
- construit une validation temporelle de type `expanding-window CV`
- loggue d'abord les baselines
- entraine ensuite les modeles challengers
- ecrit toutes les metriques et artefacts dans MLflow

Pourquoi cette phase existe:

- eviter les faux bons scores dus a un split aleatoire
- comparer les modeles a des references simples
- garder un historique MLflow exploitable

### Phase 5. Evaluation et export de rapports

Le script [`evaluate_models.py`](/Users/stefen/tl_demand_forecasting/scripts/evaluate_models.py) extrait un resume lisible de MLflow.

Ce qu'il fait:

- recupere tous les runs termines
- trie les runs eligibles par performance holdout
- exporte:
  - [`reports/run_summary.csv`](/Users/stefen/tl_demand_forecasting/reports/run_summary.csv)
  - [`reports/best_run.json`](/Users/stefen/tl_demand_forecasting/reports/best_run.json)

Pourquoi cette phase existe:

- sortir de MLflow un resume diffable, simple a lire et facile a versionner
- permettre des quality gates CI sans devoir parser toute l'API MLflow

### Phase 6. Promotion du champion

Le script [`promote_champion.py`](/Users/stefen/tl_demand_forecasting/scripts/promote_champion.py) ne promeut pas automatiquement "le meilleur score". Il applique des garde-fous.

Ce qu'il fait:

- lit les runs MLflow eligibles
- identifie le meilleur challenger `lightgbm` ou `xgboost`
- verifie qu'il:
  - bat la meilleure baseline
  - respecte `holdout_mase < 1`
  - ne regresse pas face au champion courant
- enregistre le modele dans MLflow Registry
- met a jour l'alias `candidate`
- met a jour l'alias `champion` seulement si toutes les gates passent
- exporte [`promotion_decision.json`](/Users/stefen/tl_demand_forecasting/reports/promotion_decision.json)

Pourquoi cette phase existe:

- eviter une promotion basee sur un unique run "chanceux"
- separer clairement `candidate` et `champion`
- rendre la decision traquable

### Phase 7. Replay pseudo-live

Le service [`run_replay_cycle.py`](/Users/stefen/tl_demand_forecasting/prediction_service/run_replay_cycle.py) tourne sur EC2 via `systemd`.

Ce qu'il fait a chaque cycle:

- charge le `holdout` depuis S3 ou local
- lit `replay_state.current_hour`
- charge `models:/tlc-demand-forecasting@champion`
- calcule les predictions pour cette heure
- compare aux `actual_trips` du holdout
- ecrit les lignes dans la table `zone_predictions`
- avance le curseur temporel vers l'heure suivante

Pourquoi cette phase existe:

- le projet ne pretend pas avoir un vrai flux temps reel TLC
- le replay permet de montrer un systeme "vivant" et defensable
- le dashboard bouge dans le temps avec de vraies erreurs de prediction

### Phase 8. Tableau de bord et alerting

Grafana lit PostgreSQL et affiche:

- une carte des zones avec demande moyenne
- une courbe `predicted vs actual`
- un tableau de MAE sur la selection
- une stat MAE sur 24h pour la selection
- un dashboard separe `TLC Operations` pour la fraicheur du replay, la couverture des batches et l'etat du champion

Des alertes sont aussi provisionnees:

- fraicheur du replay
- couverture du dernier batch
- derive MAE sur 24h

Pourquoi cette phase existe:

- relier le pipeline ML a une lecture metier simple
- rendre visibles les erreurs et la fraicheur du systeme
- simuler un minimum d'exploitation "prod"

## 4. Pourquoi chaque brique existe

| Brique | Fichiers principaux | Role | Pourquoi |
| --- | --- | --- | --- |
| Terraform | [`terraform/main.tf`](/Users/stefen/tl_demand_forecasting/terraform/main.tf) | cree l'infra AWS | infra reproductible et versionnee |
| Ansible | [`ansible/playbooks/site.yml`](/Users/stefen/tl_demand_forecasting/ansible/playbooks/site.yml) | configure l'EC2 | eviter la configuration manuelle |
| S3 | `raw/`, `features/`, `holdout/`, `mlflow-artifacts/` | stockage persistant | separer artefacts, donnees et modele |
| PostgreSQL | [`schema.sql`](/Users/stefen/tl_demand_forecasting/prediction_service/sql/schema.sql) | backend MLflow + predictions | centraliser metadata et monitoring |
| MLflow | [`mlflow.yml`](/Users/stefen/tl_demand_forecasting/ansible/playbooks/mlflow.yml) | tracking et registry | standard simple et lisible |
| Replay service | [`run_replay_cycle.py`](/Users/stefen/tl_demand_forecasting/prediction_service/run_replay_cycle.py) | pseudo-live | dashboard defensable sans vraie source live |
| Grafana | [`tlc-dashboard.json.j2`](/Users/stefen/tl_demand_forecasting/ansible/templates/tlc-dashboard.json.j2) | visualisation | lecture metier immediate |
| Quality gates | [`check_quality.py`](/Users/stefen/tl_demand_forecasting/scripts/check_quality.py), [`quality_gates.json`](/Users/stefen/tl_demand_forecasting/config/quality_gates.json) | validation CI | imposer des seuils explicites |
| GitHub Actions | [`.github/workflows/ci.yml`](/Users/stefen/tl_demand_forecasting/.github/workflows/ci.yml), [`.github/workflows/deploy-reusable.yml`](/Users/stefen/tl_demand_forecasting/.github/workflows/deploy-reusable.yml) | CI/CD | automatiser test, deploy staging et prod |

## 5. Choix ML: quoi, comment, pourquoi

### Unite de prediction

L'unite predite est:

- une `zone TLC`
- une `heure`
- une cible `target_trips`

Autrement dit, on ne predit pas des trajets individuels. On predit un volume agrege.

Exemple:

- `2024-06-24 08:00:00`, zone `JFK Airport` -> le modele predit un nombre attendu de trajets pour cette heure
- `2024-06-28 18:00:00`, zone `Penn Station/Madison Sq West` -> autre prediction, toujours sur une heure precise

La signification des colonnes dans PostgreSQL et Grafana est:

- `predicted_trips`: le volume estime par le modele champion pour `zone x heure`
- `actual_trips`: le volume reel observe dans le holdout pour cette meme `zone x heure`
- `absolute_error`: `abs(predicted_trips - actual_trips)`

Point important:

- `actual_trips` n'est pas une simulation
- cette valeur vient directement de `target_trips` dans le dataset holdout rejoue par [`run_replay_cycle.py`](/Users/stefen/tl_demand_forecasting/prediction_service/run_replay_cycle.py)

### Features utilisees

Les colonnes sont centralisees dans [`feature_builder.py`](/Users/stefen/tl_demand_forecasting/prediction_service/feature_builder.py).

Features calendaires:

- `hour_of_day`
- `day_of_week`
- `day_of_month`
- `month`
- `is_weekend`
- `hour_sin`, `hour_cos`
- `dow_sin`, `dow_cos`

Features de memoire:

- `lag_1h`
- `lag_2h`
- `lag_24h`
- `lag_168h`

Features de tendance:

- `rolling_mean_6h`
- `rolling_mean_24h`
- `rolling_std_24h`
- `trend_ratio`

Pourquoi ce choix:

- la demande taxi est fortement cyclique
- les lags courts capturent l'inertie immediate
- les lags 24h et 168h capturent saisonnalites journaliere et hebdomadaire
- les rolling stats aident a stabiliser les zones peu denses

### Baselines

Le projet impose trois baselines:

- `seasonal_naive_24h`
- `seasonal_naive_168h`
- `rolling_mean_24h`

Pourquoi:

- une baseline dit si le probleme vaut la peine d'etre modele
- si un modele complexe ne bat pas `lag_24h` ou `lag_168h`, il n'a pas sa place en production

### Modeles challengers

Les deux modeles actuellement supportes sont:

- `LightGBM`
- `XGBoost`

Hyperparametres actuels:

`lightgbm`

- `n_estimators=500`
- `learning_rate=0.05`
- `num_leaves=63`
- `subsample=0.8`
- `colsample_bytree=0.8`
- `random_state=42`

`xgboost`

- `n_estimators=400`
- `learning_rate=0.05`
- `max_depth=8`
- `subsample=0.8`
- `colsample_bytree=0.8`
- `objective=reg:squarederror`
- `random_state=42`

Pourquoi ces modeles:

- ils gerent bien les donnees tabulaires
- ils fonctionnent bien avec peu de preprocessing
- ils capturent facilement non-linearites et interactions
- ils sont simples a recharger dans MLflow

Pourquoi pas un deep learning ici:

- le but du projet est d'illustrer une stack MLOps claire
- le gain probable ne justifie pas la complexite operationnelle

### Protocole de validation

Le projet n'utilise pas un simple split aleatoire.

Il utilise:

- `expanding-window CV` sur le train
- un `holdout` final gele sur les `7 derniers jours`

Pourquoi:

- eviter la fuite temporelle
- simuler un vrai contexte de forecasting
- reserver une fenetre finale pour la decision de promotion

### Metriques

Le projet suit principalement:

- `MAE`
- `RMSE`
- `MASE`

Pourquoi `MASE` est importante:

- elle compare l'erreur du modele a une erreur naive saisonniere
- `MASE < 1` signifie que le modele fait mieux qu'une reference naive

### Champion actuel

Snapshot actuel issu de [`best_run.json`](/Users/stefen/tl_demand_forecasting/reports/best_run.json):

- modele: `lightgbm`
- `holdout_mae = 6.4256`
- `holdout_rmse = 14.3644`
- `holdout_mase = 0.4161`
- gain vs meilleure baseline holdout: `+46.34%`
- version registry courante: `4`

Comparaison rapide:

- `lightgbm` holdout MAE: `6.4256`
- `xgboost` holdout MAE: `6.4413`
- meilleure baseline holdout MAE: `11.9738`

Interpretation:

- le modele champion bat clairement les baselines
- l'erreur moyenne reste non nulle, donc ce n'est pas un systeme "parfait"
- le pipeline est surtout credible parce qu'il mesure ses erreurs correctement

Sanity-check sur la prod au `15 mars 2026`:

- `20574` lignes de replay evaluees
- fenetre rejouee: `2024-06-24 00:00:00` -> `2024-06-30 23:00:00`
- moyenne `predicted_trips`: `39.27`
- moyenne `actual_trips`: `39.38`
- biais moyen: `-0.11`
- total predit: `807883`
- total reel: `810193`
- correlation `predicted vs actual`: `0.9817`

Interpretation de ce sanity-check:

- les predictions suivent tres bien la forme globale de la demande
- le modele a maintenant une legere tendance a sous-predire
- les ecarts restent surtout visibles sur certains pics, mais l'ensemble reste coherent

## 6. Comment le systeme decide si un modele est acceptable

Les quality gates versionnees dans [`quality_gates.json`](/Users/stefen/tl_demand_forecasting/config/quality_gates.json) imposent notamment:

- modele autorise dans `lightgbm` ou `xgboost`
- `holdout_mae <= 8.0`
- `holdout_mase <= 1.0`
- `cv_mae_std <= 1.0`
- amelioration holdout vs meilleure baseline `>= 10%`
- au moins `3` baselines presentes
- promotion approuvee
- toutes les gates de promotion a `true`

Consequence:

- un bon score isole ne suffit pas
- il faut a la fois performance, stabilite et decision de promotion validee

## 7. Ce qui tourne vraiment sur AWS

Pour chaque environnement `staging` et `production`, Terraform cree:

- une EC2 `m6i.large`
- une Elastic IP
- un bucket S3 chiffre et versionne
- un role IAM pour l'EC2
- un Security Group
- optionnellement un role IAM OIDC assume par GitHub Actions

Ansible configure ensuite:

- PostgreSQL
- MLflow
- Grafana
- le service `tlc-replay.service`
- le timer `tlc-replay.timer`

### Base de donnees

La table principale pour le dashboard est `zone_predictions`.

Colonnes importantes:

- `target_hour`
- `zone_id`
- `zone_name`
- `borough`
- `latitude`, `longitude`
- `predicted_trips`
- `actual_trips`
- `absolute_error`
- `model_version`
- `model_alias`
- `generated_at`

La table `replay_state` stocke le curseur temporel du replay.

## 8. CI/CD et environnements

Le repo contient quatre workflows principaux:

- [`.github/workflows/ci.yml`](/Users/stefen/tl_demand_forecasting/.github/workflows/ci.yml)
- [`.github/workflows/deploy-staging.yml`](/Users/stefen/tl_demand_forecasting/.github/workflows/deploy-staging.yml)
- [`.github/workflows/deploy.yml`](/Users/stefen/tl_demand_forecasting/.github/workflows/deploy.yml)
- [`.github/workflows/deploy-reusable.yml`](/Users/stefen/tl_demand_forecasting/.github/workflows/deploy-reusable.yml)

Ce qu'ils font:

- `CI` lance tests Python, validation Terraform et syntax-check Ansible
- `Deploy Staging` se declenche sur `push` vers `main`
- `Deploy Production` se declenche sur tag `prod-v*`
- `deploy-reusable.yml` partage la logique Terraform + Ansible

Points de securite importants:

- pas de credentials AWS longs dans GitHub
- GitHub assume un role IAM via OIDC
- une ouverture SSH temporaire est ajoutee uniquement pour l'IP du runner
- `MLFLOW_DB_PASSWORD` et `GRAFANA_ADMIN_PASSWORD` sont geres comme secrets GitHub d'environnement

## 9. Comment lire le dashboard Grafana

Les dashboards provisionnes sont:

- [`TLC Demand Forecasting`](/Users/stefen/tl_demand_forecasting/ansible/templates/tlc-dashboard.json.j2) pour la lecture metier
- [`TLC Operations`](/Users/stefen/tl_demand_forecasting/ansible/templates/tlc-operations-dashboard.json.j2) pour la sante du replay et du monitoring

Le dashboard metier est ancre sur `MAX(target_hour)` disponible en base, pas sur l'heure murale du serveur.

Panneaux:

- `Demand Geomap`: moyenne predite / observee / erreur sur les 24h les plus recentes du replay
- `Predicted vs Actual`: courbe sur 7 jours pour la zone selectionnee ou `All`
- `MAE for Selection`: tableau par zone ou sur la selection
- `MAE for Selection` stat: moyenne d'erreur recente

Lecture du panel `Predicted vs Actual`:

- l'axe horizontal represente le temps, donc les heures rejouees
- l'axe vertical represente le nombre de trajets
- avec `Zone = All`, Grafana peut abreger l'axe gauche en milliers
- par exemple `1.25` signifie environ `1250 trajets`, `2.75` signifie environ `2750 trajets`

Si les labels visibles en bas tombent surtout a `00:00`, ce n'est pas parce que la prediction est quotidienne. C'est seulement le choix d'etiquetage de l'axe sur une fenetre de 7 jours. Les donnees restent bien horaires.

Panneaux operations:

- `Replay Rows Total`
- `Latest Batch Rows`
- `Replay Freshness Minutes`
- `MAE 24h`
- `Replay Coverage by Target Hour`
- `Absolute Error by Target Hour`
- `Latest Batch Details`
- `Replay Cursor State`

Alertes provisionnees:

- `TLC Replay Freshness`
- `TLC Replay Coverage`
- `TLC Model MAE 24h`

## 10. Structure du repo

| Dossier | Contenu |
| --- | --- |
| [`terraform/`](/Users/stefen/tl_demand_forecasting/terraform) | ressources AWS, SG, IAM, OIDC, outputs |
| [`ansible/`](/Users/stefen/tl_demand_forecasting/ansible) | playbooks et templates systemd/Grafana/MLflow |
| [`scripts/`](/Users/stefen/tl_demand_forecasting/scripts) | ingestion, features, training, evaluation, promotion, quality gates |
| [`prediction_service/`](/Users/stefen/tl_demand_forecasting/prediction_service) | replay pseudo-live et helper features |
| [`config/`](/Users/stefen/tl_demand_forecasting/config) | seuils de quality gates |
| [`reports/`](/Users/stefen/tl_demand_forecasting/reports) | artefacts d'evaluation exportes |
| [`tests/`](/Users/stefen/tl_demand_forecasting/tests) | tests unitaires du pipeline |
| [`databricks/`](/Users/stefen/tl_demand_forecasting/databricks) | notebooks optionnels de demo |

## 11. Commandes utiles

Initialisation locale:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements/local.txt
```

Preparation des donnees:

```bash
python scripts/build_zone_centroids.py --upload-s3 --s3-bucket "$S3_BUCKET"
python scripts/ingest_tlc.py --year 2024 --months 1 2 3 --upload-s3 --s3-bucket "$S3_BUCKET"
python scripts/build_features.py --upload-s3 --s3-bucket "$S3_BUCKET"
```

Training et promotion:

```bash
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
python scripts/train_models.py
python scripts/evaluate_models.py
python scripts/promote_champion.py
python scripts/check_quality.py
```

Validation locale:

```bash
make test
make quality-gates
make tf-validate
make ansible-syntax
```

## 12. Limites connues

Le projet est solide pour un portfolio ou une petite stack interne, mais il a encore des limites normales:

- la source TLC n'est pas temps reel
- le replay n'est pas un vrai service de prediction online
- il n'y a pas de feature store dedie
- il n'y a pas encore de multi-model serving ou A/B testing
- `production` repose sur un tag immutable GitHub, pas sur une vraie approval native d'environnement si la capacite GitHub n'est pas disponible

## 13. Role de Databricks

Les notebooks dans [`databricks/`](/Users/stefen/tl_demand_forecasting/databricks) sont optionnels.

Ils servent a:

- faire de l'EDA
- prototyper des features
- montrer une variante notebook du projet

Ils ne font pas partie du chemin critique de production.
