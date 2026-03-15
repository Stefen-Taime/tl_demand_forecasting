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

### 3. Provisionner AWS

```bash
cd terraform
cp backend.hcl.example backend.hcl
terraform init -backend-config=backend.hcl
terraform apply -var-file=terraform.tfvars
```

Le backend Terraform recommande est `S3 + DynamoDB lock`, pas un `tfstate` local.

### 4. Configurer l'EC2

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements/local.txt
export SSH_PRIVATE_KEY_PATH=~/.ssh/my-aws-key.pem
make inventory

cd ansible
../.venv/bin/ansible-galaxy collection install -r collections/requirements.yml

export MLFLOW_DB_PASSWORD='change-me'
export GRAFANA_ADMIN_PASSWORD='change-me-to-a-long-random-password'
../.venv/bin/ansible-playbook -i inventory.ini playbooks/site.yml \
  -e "mlflow_db_password=$MLFLOW_DB_PASSWORD" \
  -e "grafana_admin_password=$GRAFANA_ADMIN_PASSWORD"
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
ssh -i "$SSH_PRIVATE_KEY_PATH" -N -L 5000:127.0.0.1:5000 ubuntu@EC2_IP
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

Le CD GitHub est separe entre:

- [`.github/workflows/deploy-staging.yml`](/Users/stefen/tl_demand_forecasting/.github/workflows/deploy-staging.yml): deploiement `staging` automatique sur `push` vers `main`
- [`.github/workflows/deploy.yml`](/Users/stefen/tl_demand_forecasting/.github/workflows/deploy.yml): deploiement `production` sur tag `prod-v*`, avec `workflow_dispatch` garde-fou en mode break-glass
- [`.github/workflows/deploy-reusable.yml`](/Users/stefen/tl_demand_forecasting/.github/workflows/deploy-reusable.yml): logique commune de deploiement

Le tout est pilote par OIDC GitHub -> AWS, sans credentials AWS longs dans GitHub.

Pre-requis GitHub:

- des environnements GitHub `staging` et `production`
- une variable `AWS_DEPLOY_ROLE_ARN`
- des variables `AWS_REGION`, `PROJECT_NAME`, `EC2_INSTANCE_TYPE`, `ADMIN_ALLOWED_CIDR`, `EC2_KEY_PAIR_NAME`
- des variables `TF_STATE_BUCKET`, `TF_STATE_KEY`, `TF_LOCK_TABLE`
- des secrets `EC2_SSH_PRIVATE_KEY`, `MLFLOW_DB_PASSWORD` et `GRAFANA_ADMIN_PASSWORD`

Bon usage:

- `staging` sert de cible d'integration et peut etre plus petit ou moins couteux
- `production` se deploie a partir d'un tag immutable `prod-v*`
- `workflow_dispatch` sur `production` reste disponible en break-glass
- le role OIDC AWS commun est gere par l'etat Terraform `production`, puis reutilise par `staging`
- chaque environnement a son `TF_STATE_KEY`, son `PROJECT_NAME` et donc son infra separee
- `GRAFANA_ADMIN_PASSWORD` doit etre un secret fort et non la valeur par defaut

Contrainte GitHub actuelle:

- ce depot est `private` et le compte GitHub actuel est sur `GitHub Free`
- dans cette configuration, les `required reviewers` d'environnement ne sont pas disponibles d'apres la doc GitHub
- donc la protection `prod` repose ici sur un flux de release par tag immutable plutot que sur une approbation native d'environnement

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
