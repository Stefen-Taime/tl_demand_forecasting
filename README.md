# TLC Demand Forecasting MLOps

Infrastructure et pipeline MLOps portfolio pour prevoir la demande TLC par `zone x heure`.

Le projet est organise autour de:

- Terraform pour provisionner AWS
- Ansible pour configurer l'EC2
- MLflow pour le tracking et le registry
- PostgreSQL pour les metadata de prediction
- Grafana pour le dashboard
- un service de replay historique pour alimenter le monitoring
- des notebooks Databricks optionnels pour l'EDA et le prototypage
- un protocole de validation "prod-like" avec baselines, backtesting temporel et holdout gele
- une CI GitHub Actions pour les tests Python, les checks infra et les quality gates

Le runbook de deploiement exact est dans [`DEPLOYMENT.md`](/Users/stefen/tl_demand_forecasting/DEPLOYMENT.md).

## Architecture

- `terraform/`: infra AWS
- `ansible/`: configuration EC2
- `scripts/`: ingestion, features, training, evaluation, promotion
- `prediction_service/`: service de replay et schema SQL
- `databricks/`: notebooks optionnels
- `requirements/`: dependances locales et EC2

## Demarrage rapide

### 1. Prerequis

- Python 3.11+
- Terraform 1.5+
- Ansible 2.15+
- un compte AWS avec une key pair existante

### 2. Variables Terraform

Copier [`terraform/terraform.tfvars.example`](/Users/stefen/tl_demand_forecasting/terraform/terraform.tfvars.example) vers `terraform/terraform.tfvars` et remplir:

- `aws_region = "ca-central-1"` pour Montreal
- `instance_type = "m6i.large"` recommande pour faire tourner `MLflow + PostgreSQL + Grafana + replay` sur une seule EC2
- `allowed_cidr`
- `key_pair_name`
- `ssh_private_key_path`

### 3. Provisionner AWS

```bash
cd terraform
terraform init
terraform apply -var-file=terraform.tfvars
```

### 4. Configurer l'EC2

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements/local.txt

cd ansible
../.venv/bin/ansible-galaxy collection install -r collections/requirements.yml

export MLFLOW_DB_PASSWORD='change-me'
../.venv/bin/ansible-playbook -i inventory.ini playbooks/site.yml \
  -e "mlflow_db_password=$MLFLOW_DB_PASSWORD"
cd ..
```

### 5. Preparer les donnees

```bash
python scripts/build_zone_centroids.py --upload-s3 --s3-bucket "$S3_BUCKET"
python scripts/ingest_tlc.py --year 2024 --months 1 2 3 --upload-s3
python scripts/build_features.py
```

`build_features.py` filtre les pickups hors de la plage temporelle declaree par les noms de fichiers TLC pour eviter de laisser passer des timestamps aberrants.

### 6. Ouvrir un tunnel MLflow

```bash
ssh -N -L 5000:127.0.0.1:5000 ubuntu@EC2_IP
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
```

### 7. Entrainer et promouvoir un modele

```bash
python scripts/train_models.py
python scripts/evaluate_models.py
python scripts/promote_champion.py
```

Ce que fait maintenant ce pipeline:

- `train_models.py` loggue des baselines (`seasonal_naive_24h`, `seasonal_naive_168h`, `rolling_mean_24h`) puis entraine `lightgbm` et `xgboost`
- la validation est une `expanding-window CV` sur plusieurs folds temporels
- la decision principale se fait sur un `holdout` final gele
- `promote_champion.py` ne promeut un candidat que s'il bat la meilleure baseline, garde `MASE < 1`, et ne regresse pas face au champion courant

Artefacts utiles:

- [`reports/run_summary.csv`](/Users/stefen/tl_demand_forecasting/reports/run_summary.csv)
- [`reports/best_run.json`](/Users/stefen/tl_demand_forecasting/reports/best_run.json)
- [`reports/promotion_decision.json`](/Users/stefen/tl_demand_forecasting/reports/promotion_decision.json)
- [`reports/quality_gate_report.json`](/Users/stefen/tl_demand_forecasting/reports/quality_gate_report.json)

### 7.b Valider la qualite comme en prod

```bash
make test
make quality-gates
make tf-validate
make ansible-syntax
```

La meme logique est versionnee dans [`.github/workflows/ci.yml`](/Users/stefen/tl_demand_forecasting/.github/workflows/ci.yml).

## GitHub CD

Le deploiement production est pilote par OIDC GitHub -> AWS dans [`.github/workflows/deploy.yml`](/Users/stefen/tl_demand_forecasting/.github/workflows/deploy.yml).

Pre-requis GitHub:

- un environnement GitHub `production`
- une variable `AWS_DEPLOY_ROLE_ARN`
- des variables `AWS_REGION`, `PROJECT_NAME`, `EC2_INSTANCE_TYPE`, `ADMIN_ALLOWED_CIDR`, `EC2_KEY_PAIR_NAME`
- des secrets `EC2_SSH_PRIVATE_KEY` et `MLFLOW_DB_PASSWORD`

Le workflow:

- assume un role AWS court-terme via OIDC
- fait `terraform init/plan/apply`
- ouvre temporairement SSH pour l'IP du runner GitHub
- rejoue `ansible/playbooks/site.yml`
- referme la regle SSH temporaire en fin de job

### 8. Verifier Grafana et le replay timer

```bash
curl http://EC2_IP:3000/api/health
ssh ubuntu@EC2_IP 'systemctl status tlc-replay.timer'
```

Pour remplir le monitoring plus vite apres un premier entrainement, tu peux aussi lancer plusieurs cycles de replay d'un coup:

```bash
export EC2_IP=...
make replay-backfill
```

Ce backfill complet nettoie aussi les anciennes lignes de replay qui sortent de la fenetre holdout courante avant de recharger les predictions.

Grafana provisionne aussi des alertes sur:

- la fraicheur du replay
- la couverture du dernier batch
- la derive MAE sur 24h

## Databricks

Les notebooks dans [`databricks/`](/Users/stefen/tl_demand_forecasting/databricks) sont optionnels. Ils servent a:

- explorer les donnees
- prototyper du feature engineering
- montrer une version notebook du projet

Ils ne sont pas dans le chemin critique du deploiement.
