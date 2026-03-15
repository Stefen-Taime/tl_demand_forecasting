# PRD v3 - TLC Demand Forecasting MLOps

**Project**: TLC Demand Forecasting MLOps
**Version**: v3.0
**Date**: March 15, 2026
**Status**: aligned with the current repository and deployed implementation

---

## 0. Executive Summary

This project builds a complete MLOps chain to forecast TLC taxi demand at the `zone x hour` level, evaluate multiple models, promote a guarded `champion`, and expose predictions and errors through Grafana.

The system does not pretend that the public TLC dataset is a real-time stream. Instead, it uses a historical replay design:

- a final holdout is carved out after training data is prepared
- a service on EC2 replays that holdout one hour at a time
- the `champion` model generates predictions
- the real holdout targets are reused as `actuals`
- Grafana displays predictions, errors, and trends

The project is intentionally "prod-like" because it includes:

- versioned AWS infrastructure
- separate `staging` and `production`
- GitHub CI/CD
- quality gates
- a real time-aware validation protocol
- guarded model promotion
- operational alerts

---

## 1. Problem and Objective

### Problem

The project needs to show how raw taxi trip files can be turned into an MLOps system that is understandable, reviewable, and technically defensible.

Without an MLOps frame, the common outcome is:

- notebooks that cannot be replayed
- fragile metrics
- models without release governance
- no connection between training and observability

### Objective

Build a system that covers the full loop:

1. ingest raw TLC data
2. produce a `zone x hour` dataset
3. train multiple candidates
4. compare them to required baselines
5. promote a `champion` only if the gates pass
6. run a historical replay
7. surface the result in Grafana

### Expected outcome

At the end, an observer should be able to understand:

- where the data comes from
- how features are built
- which models were compared
- why the promoted model won
- how the dashboard is populated
- how the infrastructure is deployed and secured

---

## 2. Scope and Non-Goals

### In scope

- AWS provisioning with Terraform
- EC2 configuration with Ansible
- S3 storage
- MLflow tracking and model registry
- predictions and monitoring in PostgreSQL + Grafana
- GitHub Actions CI
- `staging` and `production` CD
- time-aware validation and a frozen final holdout
- quality gates and alerts

### Out of scope

- true TLC real-time streaming
- low-latency HTTP serving for online predictions
- Kubernetes
- a dedicated feature store
- online learning
- Airflow or Dagster orchestration
- large-scale distributed experimentation

### Databricks role

Databricks is not a production dependency.

It remains optional for:

- exploratory analysis
- notebook demos
- fast feature prototyping

The deployed path does not depend on Databricks.

---

## 3. Users and Use Cases

### Primary user

A technical recruiter, hiring manager, or data/ML team evaluating the maturity of the project.

### Main use cases

1. Read the pipeline end to end
2. Replay the full training and promotion flow
3. Inspect a model promotion backed by explicit gates
4. Open Grafana and interpret the prediction errors
5. Deploy infrastructure to `staging` and `production`

---

## 4. Target Architecture

### 4.1 System view

```text
                               +-----------------------+
                               |      GitHub           |
                               |  source + Actions     |
                               +-----------+-----------+
                                           |
                                           | OIDC + CI/CD
                                           v
 +----------------------+        +-----------------------------+
 | Local machine        |        | AWS environment            |
 |                      |        | staging or production      |
 | - terraform          |        |                             |
 | - ansible            |        | +-------------------------+ |
 | - ingest_tlc.py      |        | | EC2 m6i.large           | |
 | - build_features.py  |  SSH   | |                         | |
 | - train_models.py    +------->| | PostgreSQL              | |
 | - evaluate_models.py |        | | MLflow localhost:5000   | |
 | - promote_champion.py|        | | Grafana :3000           | |
 |                      |        | | replay systemd timer    | |
 +----------+-----------+        | +------------+------------+ |
            |                    |              |              |
            | boto3 / S3         |              | localhost    |
            v                    |              v              |
 +----------------------+        |      +------------------+   |
 | S3                   |<-------+      | PostgreSQL       |   |
 | - raw/               |               | - mlflow db      |   |
 | - features/          |               | - predictions db |   |
 | - holdout/           |               +------------------+   |
 | - mlflow-artifacts/  |                                          |
 | - reports/           |                                          |
 +----------------------+                                          |
                                                                   |
                         Grafana <----------------------------------+
                         reads zone_predictions and exposes the business view
```

### 4.2 Data lineage

```text
Raw TLC files
    |
    +--> scripts/ingest_tlc.py
    |
    +--> data/raw/*.parquet
    +--> data/raw/taxi_zone_lookup.csv
    +--> data/raw/taxi_zone_centroids.csv
            |
            v
scripts/build_features.py
    |
    +--> data/processed/features.parquet
    +--> data/processed/train_features.parquet
    +--> data/holdout/holdout_features.parquet
            |
            v
scripts/train_models.py
    |
    +--> MLflow runs
    +--> MLflow artifacts
            |
            v
scripts/evaluate_models.py
    |
    +--> reports/run_summary.csv
    +--> reports/best_run.json
            |
            v
scripts/promote_champion.py
    |
    +--> MLflow Registry alias champion
    +--> reports/promotion_decision.json
            |
            v
prediction_service/run_replay_cycle.py
    |
    +--> PostgreSQL.zone_predictions
    +--> PostgreSQL.replay_state
            |
            v
Grafana dashboards + alert rules
```

### 4.3 Why this architecture

This architecture was chosen because it:

- separates training from operations
- keeps MLflow private
- versions the infrastructure
- supports realistic `staging` and `production`
- avoids inventing a fake streaming story

---

## 5. Components and Responsibilities

| Component | Files | What it does | Why it exists |
| --- | --- | --- | --- |
| Terraform | [`terraform/main.tf`](terraform/main.tf) | creates EC2, EIP, S3, IAM, Security Groups, OIDC | makes infrastructure reproducible |
| Ansible base | [`ansible/playbooks/site.yml`](ansible/playbooks/site.yml) | installs the Ubuntu foundation | avoids manual server setup |
| Ansible PostgreSQL | [`ansible/playbooks/postgresql.yml`](ansible/playbooks/postgresql.yml) | creates databases, users, schema | backs MLflow and prediction storage |
| Ansible MLflow | [`ansible/playbooks/mlflow.yml`](ansible/playbooks/mlflow.yml) | installs MLflow and systemd | tracks runs and registry state |
| Ansible Grafana | [`ansible/playbooks/grafana.yml`](ansible/playbooks/grafana.yml) | installs Grafana, datasource, dashboards, alerts | exposes business and ops visibility |
| Ansible replay | [`ansible/playbooks/prediction_timer.yml`](ansible/playbooks/prediction_timer.yml) | deploys replay service and timer | simulates a production feed |
| Ingestion | [`scripts/ingest_tlc.py`](scripts/ingest_tlc.py) | downloads TLC parquet files | freezes the raw source |
| Geography | [`scripts/build_zone_centroids.py`](scripts/build_zone_centroids.py) | computes zone centroids | powers the geomap |
| Feature engineering | [`scripts/build_features.py`](scripts/build_features.py) | builds the model dataset | converts raw trips into supervised rows |
| Training | [`scripts/train_models.py`](scripts/train_models.py) | trains baselines and challengers | compares candidates correctly |
| Evaluation | [`scripts/evaluate_models.py`](scripts/evaluate_models.py) | exports readable reports | supports review and CI gates |
| Promotion | [`scripts/promote_champion.py`](scripts/promote_champion.py) | updates `candidate` and `champion` | governs model release |
| Quality gates | [`scripts/check_quality.py`](scripts/check_quality.py) | enforces versioned thresholds | blocks silent regressions |
| Replay service | [`prediction_service/run_replay_cycle.py`](prediction_service/run_replay_cycle.py) | writes predictions plus actuals | closes the monitoring loop |

---

## 6. Data Contract

### 6.1 Raw sources

Main source:

- monthly TLC files `yellow_tripdata_YYYY-MM.parquet`

Supporting sources:

- `taxi_zone_lookup.csv`
- `taxi_zones.zip` shapefile

### 6.2 Analysis grain

The prediction grain is:

- one TLC pickup zone
- one hour
- one target value: `target_trips`

The system forecasts aggregate demand, not individual rides.

### 6.3 Metadata columns

Key metadata attached to each row:

- `zone_id`
- `zone_name`
- `borough`
- `latitude`
- `longitude`
- `target_hour`

### 6.4 Features

Feature groups:

- calendar: `hour_of_day`, `day_of_week`, `day_of_month`, `month`, `is_weekend`
- cyclical encodings: `hour_sin`, `hour_cos`, `dow_sin`, `dow_cos`
- lags: `lag_1h`, `lag_2h`, `lag_24h`, `lag_168h`
- rolling statistics: `rolling_mean_6h`, `rolling_mean_24h`, `rolling_std_24h`
- short-term ratio: `trend_ratio`

### 6.5 Data quality rules

The data preparation step enforces:

- non-null `tpep_pickup_datetime`
- non-null `PULocationID`
- timestamp filtering based on the month bounds implied by the raw filenames
- temporal ordering by `zone_id` and `target_hour`
- no train/holdout overlap

### 6.6 Currently ingested and evaluated window

As of `March 15, 2026`, the repository and production deployment use:

- `yellow_tripdata_2024-01.parquet`
- `yellow_tripdata_2024-02.parquet`
- `yellow_tripdata_2024-03.parquet`
- `yellow_tripdata_2024-04.parquet`
- `yellow_tripdata_2024-05.parquet`
- `yellow_tripdata_2024-06.parquet`

The full dataset covers:

- `2024-01-01 00:00:00` -> `2024-06-30 23:00:00`

The real split is:

- `train`: `2024-01-01 00:00:00` -> `2024-06-23 23:00:00`
- `holdout`: `2024-06-24 00:00:00` -> `2024-06-30 23:00:00`

So:

- training happens on `January 1 -> June 23`
- final evaluation and replay happen on `June 24 -> June 30`

---

## 7. ML Design

### 7.1 Why a tabular forecasting problem

This project treats forecasting as an aggregated tabular problem rather than a deep sequence modeling problem.

Reasons:

- the `zone x hour` grain fits gradient boosting well
- the feature set is interpretable
- the operational complexity stays moderate
- the dataset size does not require distributed deep learning

### 7.2 Required baselines

The pipeline always logs the following baselines:

- `seasonal_naive_24h`
- `seasonal_naive_168h`
- `rolling_mean_24h`

Why they are mandatory:

- `lag_24h` and `lag_168h` are natural references for hourly taxi demand
- if a complex model cannot beat them, it should not be promoted

### 7.3 Challenger models

Current challengers:

- `lightgbm`
- `xgboost`

Current parameter sets:

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
- `objective='reg:squarederror'`
- `random_state=42`

Why these challengers:

- they are strong tabular baselines
- they work well with the engineered lag features
- they are easier to operationalize than heavier alternatives for this scope

### 7.4 Validation strategy

Validation has two layers:

1. expanding-window cross-validation on the training set
2. one frozen final holdout over the last `7 days`

The exact holdout split is not a hand-picked date. It is driven by the last `168` hours of the available dataset, which corresponds to one full week.

Why `7 days`:

- the prediction grain is hourly
- a full week captures weekday and weekend effects
- the model explicitly uses `lag_168h`
- it leaves most of the data available for fitting

### 7.4.b Exact semantics of predictions and actuals

For one `zone x hour` row:

- `predicted_trips` = output of the current `champion`
- `actual_trips` = true `target_trips` value from the frozen holdout
- `absolute_error` = absolute difference between the two

The replay does not fabricate the actuals.

[`prediction_service/run_replay_cycle.py`](prediction_service/run_replay_cycle.py):

1. reads one holdout hour
2. applies the champion model
3. copies the real holdout target into `actual_trips`
4. computes the error
5. writes the result into PostgreSQL

### 7.5 Metrics

Tracked metrics:

- `MAE`
- `RMSE`
- `MASE`

Interpretation:

- `MAE` = average miss in trips per `zone x hour`
- `RMSE` = stronger penalty for large misses
- `MASE < 1` = model beats a seasonal naive reference

### 7.6 Quality gates

Promotion and CI rely on explicit thresholds:

- `holdout_mae <= 8.0`
- `holdout_mase <= 1.0`
- improvement versus best baseline holdout MAE `>= 10%`

The intent is to turn model release into a governed decision instead of an ad hoc choice.

### 7.7 Current model state

Current report snapshot:

- champion: `lightgbm`
- `holdout_mae = 6.4256`
- `holdout_rmse = 14.3644`
- `holdout_mase = 0.4161`
- improvement versus best baseline: `+46.34%`
- approved registry version: `4`

Interpretation:

- the system clearly beats the baselines
- the performance is inside the current gates
- the challenger also beat the previous champion on holdout

Production sanity check on `March 15, 2026`:

- evaluated rows: `20574`
- replay window: `2024-06-24 00:00:00` -> `2024-06-30 23:00:00`
- mean `predicted_trips`: `39.27`
- mean `actual_trips`: `39.38`
- mean bias: `-0.11`
- total predicted: `807883`
- total actual: `810193`
- correlation `predicted vs actual`: `0.9817`

How to read this:

- the model tracks the global demand shape well
- there is a slight under-prediction bias
- some peak periods remain harder than the average case

---

## 8. Promotion and Model Governance

### 8.1 Promotion rules

A challenger is promoted only if it:

- beats the best baseline on the final holdout
- satisfies `holdout_mase < 1`
- beats the current champion on the same holdout

### 8.2 MLflow states used

The project uses:

- experiment runs for tracking
- registry versions for release state
- aliases:
  - `candidate`
  - `champion`

### 8.3 Decision artifacts

Release decisions are materialized into:

- [`reports/run_summary.csv`](reports/run_summary.csv)
- [`reports/best_run.json`](reports/best_run.json)
- [`reports/promotion_decision.json`](reports/promotion_decision.json)

These artifacts make the decision diffable and reviewable outside the MLflow UI.

---

## 9. Pseudo-Live Replay Design

### 9.1 Why replay exists

The public TLC source is historical. There is no native production event stream in this project.

Replay exists to:

- simulate production-like writes
- feed Grafana with something realistic
- compare predictions and real observations on the same hidden window

### 9.2 Mechanism

The replay service:

- loads the holdout
- advances one hour at a time
- loads the MLflow `champion`
- writes predictions and actuals to PostgreSQL

It can run:

- on a schedule through `systemd`
- manually for one cycle
- in full backfill mode for the entire holdout

### 9.3 PostgreSQL tables involved

Main tables:

- `zone_predictions`
- `replay_state`

`zone_predictions` stores:

- prediction timestamp
- target hour
- zone metadata
- `predicted_trips`
- `actual_trips`
- `absolute_error`
- `model_version`
- `model_alias`

### 9.4 Execution modes

Supported execution modes:

- periodic timer-based replay
- manual immediate replay
- full-window backfill with stale-row pruning

The stale-row pruning is important to avoid showing replay data that no longer belongs to the current holdout window.

---

## 10. Dashboard and Observability

### 10.1 Grafana dashboards

Business dashboard:

- `TLC Demand Forecasting`

Operations dashboard:

- `TLC Operations`

Key business panels:

- `Predicted vs Actual`
- `Demand Geomap`
- `MAE for Selection`

Key operations panels:

- replay rows total
- latest batch rows
- freshness in minutes
- 24h MAE
- latest batch details

### 10.2 Time anchoring

The business dashboard queries are anchored on `MAX(target_hour)` in `zone_predictions`, not on wall clock `NOW()`.

This is critical because the replay is historical.

Without this design, the dashboard would show `No data` even when the replay data is correct.

### 10.2.b Visual interpretation of the main panel

`Predicted vs Actual` means:

- x-axis = time
- y-axis = trip volume
- one series = `predicted_trips`
- one series = `actual_trips`

If the selected zone becomes invalid for the current holdout window, the dashboard now falls back to `All` instead of returning an empty screen.

### 10.3 Alerts

Current provisioned alerts:

- `TLC Replay Freshness`
- `TLC Replay Coverage`
- `TLC Model MAE 24h`

Intent:

- detect replay stalls
- detect incomplete writes
- detect performance drift

---

## 11. AWS Infrastructure

### 11.1 Resources created

Main resources:

- 1 EC2 instance
- 1 Elastic IP
- 1 S3 bucket
- IAM role and instance profile
- Security Group rules
- optional GitHub OIDC integration

### 11.2 Network rules

Network principles:

- Grafana is public on `:3000`
- MLflow stays private on `localhost:5000`
- PostgreSQL stays private on `localhost:5432`
- SSH access is restricted by `allowed_cidr`
- GitHub deploys open a temporary SSH rule for the runner IP only during deployment

### 11.3 S3 storage

S3 is used for:

- `raw/`
- `features/`
- `holdout/`
- `mlflow-artifacts/`
- `reports/`

### 11.4 Sizing choice

The retained sizing is `m6i.large`.

Reason:

- smaller burstable instances such as `t3.small` became slow under the combined load of PostgreSQL, MLflow, Grafana, and replay
- CPU credits were exhausted in practice, which degraded UI responsiveness and MLflow access

---

## 12. Security and Secrets

### 12.1 Principles

Security principles:

- keep MLflow private
- do not commit infrastructure secrets
- use GitHub OIDC instead of static AWS keys for CD
- rotate the Grafana admin password

### 12.2 Critical secrets

Critical secrets include:

- `MLFLOW_DB_PASSWORD`
- `GRAFANA_ADMIN_PASSWORD`
- `EC2_SSH_PRIVATE_KEY`

They belong in:

- local `.env`
- GitHub Environment secrets

They do not belong in the repository.

### 12.3 MLflow access

MLflow is reached through an SSH tunnel:

```text
local 127.0.0.1:5000 -> SSH tunnel -> EC2 127.0.0.1:5000
```

This keeps the tracking server out of the public internet path.

### 12.4 GitHub OIDC

GitHub Actions uses AWS OIDC:

- no long-lived AWS access key in GitHub
- short-lived credentials per workflow run
- one shared GitHub -> AWS role managed through Terraform

---

## 13. CI/CD and Environments

### 13.1 Environments

The project maintains:

- `staging`
- `production`

### 13.2 CI

CI validates:

- Python syntax
- tests
- quality gates
- Terraform formatting and validation
- Ansible syntax

### 13.3 Staging CD

`staging`:

- deploys automatically on `push` to `main`
- uses the reusable deployment workflow
- provisions and configures the EC2 host end to end

### 13.4 Production CD

`production`:

- deploys from immutable `prod-v*` tags
- keeps `workflow_dispatch` as break-glass
- uses the same reusable deployment logic with production variables

### 13.5 Deployment logic

The deployment flow is:

1. GitHub OIDC into AWS
2. Terraform apply
3. temporary SSH rule for the runner IP
4. Ansible provisioning and application deployment
5. SSH rule cleanup

---

## 14. End-to-End Project Phases

### Phase A. Provision infrastructure

Inputs:

- Terraform configuration
- AWS credentials

Outputs:

- EC2 instance
- S3 bucket
- IAM and networking

### Phase B. Configure the machine

Inputs:

- inventory
- Ansible playbooks
- secrets

Outputs:

- PostgreSQL
- MLflow
- Grafana
- replay timer

### Phase C. Ingest and prepare data

Inputs:

- raw TLC files
- lookup table
- zone shapefile

Outputs:

- raw data in S3 and local storage
- processed features
- train dataset
- frozen holdout

### Phase D. Train and evaluate

Inputs:

- train
- holdout

Outputs:

- MLflow runs
- report artifacts
- best challenger identification

### Phase E. Promote

Inputs:

- evaluation metrics
- current champion state

Outputs:

- updated `candidate`
- updated `champion` if gates pass
- promotion decision artifact

### Phase F. Replay and observe

Inputs:

- holdout
- champion model

Outputs:

- PostgreSQL replay rows
- Grafana visualizations
- operational alerts

---

## 15. Success Criteria

The project is successful if:

1. infrastructure can be deployed from code
2. the pipeline produces valid train and holdout datasets
3. multiple models are compared against explicit baselines
4. promotion is blocked when gates fail
5. the replay writes predictions plus actuals into PostgreSQL
6. Grafana displays business and operational views
7. CI/CD works for `staging` and `production`

---

## 16. Known Limits and Accepted Risks

### Limits

- no true online data source
- no external model serving API
- no Kubernetes
- no dedicated feature store
- no enterprise-scale orchestration layer

### Risks

- public Grafana still requires careful credential hygiene
- a single EC2 host keeps the architecture simple but not highly available
- holdout-based replay is realistic for monitoring, but not a replacement for true future live inference

### Honest positioning

This is a strong portfolio and interview-grade MLOps system.

It is not pretending to be:

- a high-scale production platform
- a multi-team enterprise platform
- a real-time low-latency serving stack

---

## 17. Current Repository State

The current repository state includes:

- AWS deployment in `ca-central-1`
- `staging` and `production` workflows
- GitHub OIDC to AWS
- Grafana admin password rotation through secrets
- provisioned dashboards and alert rules
- `6` months of ingested data from `January` through `June 2024`
- `lightgbm` documented as the current champion
- registry version `4`
- replay aligned on the `June 24 -> June 30, 2024` holdout

---

## 18. Appendix: Role of the Databricks Notebooks

The notebooks under [`databricks/`](databricks/) are kept for:

- EDA
- quick feature prototyping
- demo-oriented notebook workflows

They are not part of the critical deployment path.
