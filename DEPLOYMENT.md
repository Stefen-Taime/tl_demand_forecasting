# Deployment Runbook

Ordre d'execution recommande pour deployer le projet de bout en bout.

## 1. Prerequis

Il faut avoir localement:

- `terraform`
- `python3`
- `pip`
- des credentials AWS valides
- une key pair AWS existante

Verifier:

```bash
terraform version
python3 --version
aws sts get-caller-identity
```

## 2. Preparer Terraform

Copier le fichier d'exemple:

```bash
cp terraform/terraform.tfvars.example terraform/terraform.tfvars
cp terraform/backend.hcl.example terraform/backend.hcl
```

Puis remplir dans `terraform/terraform.tfvars`:

- `aws_region = "ca-central-1"` pour Montreal
- `instance_type = "m6i.large"` recommande pour eviter les limites CPU burst des `t3.small`
- `allowed_cidr`
- `key_pair_name`
- `enable_github_actions_oidc = true` si tu veux le CD GitHub
- `github_repository = "owner/repo"`
- `github_environments = ["staging", "production"]`

## 3. Provisionner AWS

```bash
terraform -chdir=terraform init
terraform -chdir=terraform init -backend-config=backend.hcl
terraform -chdir=terraform plan -var-file=terraform.tfvars
terraform -chdir=terraform apply -var-file=terraform.tfvars
```

Exporter les outputs utiles:

```bash
export EC2_IP=$(terraform -chdir=terraform output -raw server_ip)
export S3_BUCKET=$(terraform -chdir=terraform output -raw s3_bucket)
terraform -chdir=terraform output
```

## 4. Installer l'environnement Python local

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements/local.txt
export SSH_PRIVATE_KEY_PATH=~/.ssh/my-aws-key.pem
make inventory
```

Verifier que `ansible-playbook` est maintenant disponible:

```bash
.venv/bin/ansible-playbook --version
```

## 5. Configurer l'EC2

Installer les collections Ansible:

```bash
source .venv/bin/activate
cd ansible
../.venv/bin/ansible-galaxy collection install -r collections/requirements.yml
```

Configurer le serveur:

```bash
export MLFLOW_DB_PASSWORD='change-me-now'
export GRAFANA_ADMIN_PASSWORD='change-me-to-a-long-random-password'
../.venv/bin/ansible-playbook -i inventory.ini playbooks/site.yml \
  -e "mlflow_db_password=$MLFLOW_DB_PASSWORD" \
  -e "grafana_admin_password=$GRAFANA_ADMIN_PASSWORD"
cd ..
```

## 6. Ingestion des donnees TLC

Exemple minimal:

```bash
python scripts/build_zone_centroids.py \
  --upload-s3 \
  --s3-bucket "$S3_BUCKET"

python scripts/ingest_tlc.py \
  --year 2024 \
  --months 1 2 3 \
  --upload-s3 \
  --s3-bucket "$S3_BUCKET"
```

## 7. Construire les features et le holdout

```bash
python scripts/build_features.py \
  --upload-s3 \
  --s3-bucket "$S3_BUCKET"
```

Le build filtre aussi les pickups qui tombent hors de la plage temporelle declaree par les fichiers sources pour eliminer les dates aberrantes.

Fichiers attendus:

- `data/processed/features.parquet`
- `data/processed/train_features.parquet`
- `data/holdout/holdout_features.parquet`

## 8. Ouvrir le tunnel MLflow

Dans un **autre terminal**, lancer:

```bash
ssh -i "$SSH_PRIVATE_KEY_PATH" -N -L 5000:127.0.0.1:5000 ubuntu@$(terraform -chdir=terraform output -raw server_ip)
```
Quand le tunnel tourne, dans ton terminal principal:

```bash
source .venv/bin/activate
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
```

## 9. Entrainer les modeles

```bash
python scripts/train_models.py
python scripts/evaluate_models.py
python scripts/promote_champion.py
```

Notes:

- `train_models.py` loggue d'abord des baselines puis `lightgbm` et `xgboost`
- la validation est une `expanding-window CV` sur le train set + un `holdout` final gele
- `promote_champion.py` ne promeut que `lightgbm` ou `xgboost`
- la promotion est bloquee si le candidat ne bat pas la meilleure baseline, si `holdout_mase >= 1`, ou s'il regresse face au champion courant
- les sorties de decision sont ecrites dans `reports/run_summary.csv`, `reports/best_run.json` et `reports/promotion_decision.json`

## 10. Verifier les services AWS

```bash
curl http://$EC2_IP:3000/api/health
```

Verifier les services systemd:

```bash
ssh ubuntu@$EC2_IP 'systemctl status postgresql --no-pager'
ssh ubuntu@$EC2_IP 'systemctl status mlflow --no-pager'
ssh ubuntu@$EC2_IP 'systemctl status grafana-server --no-pager'
ssh ubuntu@$EC2_IP 'systemctl status tlc-replay.timer --no-pager'
```

## 10.b Executer les quality gates

```bash
make test
make quality-gates
make tf-validate
make ansible-syntax
```

Ces checks sont aussi reproduits dans [`.github/workflows/ci.yml`](/Users/stefen/tl_demand_forecasting/.github/workflows/ci.yml).

## 11. Lancer un cycle de replay manuel

Pour ne pas attendre l'heure suivante:

```bash
ssh ubuntu@$EC2_IP 'sudo systemctl start tlc-replay.service'
ssh ubuntu@$EC2_IP 'sudo journalctl -u tlc-replay.service -n 50 --no-pager'
```

Pour backfiller plusieurs heures d'un coup:

```bash
export EC2_IP=$(terraform -chdir=terraform output -raw server_ip)
make replay-backfill
```

Le backfill complet purge aussi les lignes de replay orphelines qui ne font plus partie du holdout courant.

## 12. Ouvrir Grafana

```bash
echo "http://$EC2_IP:3000"
```

Le dashboard provisionne est:

- `TLC Demand Forecasting`

Alertes provisionnees:

- `TLC Replay Freshness`
- `TLC Replay Coverage`
- `TLC Model MAE 24h`

## 13. Boucle de travail normale

Quand l'infra est deja en place:

```bash
source .venv/bin/activate
export EC2_IP=$(terraform -chdir=terraform output -raw server_ip)
export S3_BUCKET=$(terraform -chdir=terraform output -raw s3_bucket)
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
```

Puis:

1. modifier les scripts
2. reconstruire les features si necessaire
3. relancer le tunnel MLflow
4. relancer `train_models.py`
5. relancer `promote_champion.py`
6. lancer un `tlc-replay.service` manuel pour voir le resultat tout de suite

## 14. Databricks

Les notebooks Databricks sont optionnels:

- `databricks/01_eda.py`
- `databricks/02_feature_prototype.py`
- `databricks/03_sandbox_training.py`

Tu peux les importer dans Databricks pour:

- EDA
- proto feature engineering
- demo notebook

Mais le deploiement principal n'en depend pas.

## 15. Destruction

```bash
terraform -chdir=terraform destroy -var-file=terraform.tfvars
```

## 16. GitHub CD via OIDC

Les workflows de deploiement sont:

- [`.github/workflows/deploy-staging.yml`](/Users/stefen/tl_demand_forecasting/.github/workflows/deploy-staging.yml) pour `staging`
- [`.github/workflows/deploy.yml`](/Users/stefen/tl_demand_forecasting/.github/workflows/deploy.yml) pour `production`, declenche par tag `prod-v*`
- [`.github/workflows/deploy-reusable.yml`](/Users/stefen/tl_demand_forecasting/.github/workflows/deploy-reusable.yml) pour la logique commune

Variables GitHub Environment a definir pour `staging` et `production`:

- `AWS_DEPLOY_ROLE_ARN`
- `AWS_REGION`
- `PROJECT_NAME`
- `EC2_INSTANCE_TYPE`
- `ADMIN_ALLOWED_CIDR`
- `EC2_KEY_PAIR_NAME`
- `TF_STATE_BUCKET`
- `TF_STATE_KEY`
- `TF_LOCK_TABLE`

Secrets GitHub Environment a definir pour `staging` et `production`:

- `EC2_SSH_PRIVATE_KEY`
- `MLFLOW_DB_PASSWORD`
- `GRAFANA_ADMIN_PASSWORD`

Recommandation:

- `staging` deploye automatiquement sur `push` vers `main`
- `production` deploye depuis un tag immutable `prod-v*`
- `workflow_dispatch` reste disponible en break-glass pour `production`
- l'etat Terraform `production` gere le role OIDC GitHub -> AWS commun, `staging` le reutilise
- `TF_STATE_KEY` doit etre distinct par environnement, par exemple `terraform/state/staging.tfstate` et `terraform/state/production.tfstate`
- `PROJECT_NAME` doit aussi etre distinct, par exemple `tlc-mlops-staging` et `tlc-mlops`
- `GRAFANA_ADMIN_PASSWORD` doit etre renseigne avant le premier deploy pour eviter de laisser Grafana sur le mot de passe par defaut

Le workflow utilise `OIDC` pour AWS, puis ouvre une regle SSH temporaire uniquement pour l'IP du runner GitHub pendant le deploiement.

Limite GitHub actuelle:

- sur ce repo `private` avec le compte actuel en `GitHub Free`, les `required reviewers` d'environnement ne sont pas disponibles
- si tu veux une vraie approbation native avant `prod`, il faut soit passer le repo en `public`, soit passer sur un plan GitHub qui expose cette capacite pour ton cas d'usage

Release `production` recommande:

```bash
git tag prod-v2026.03.15.1
git push origin prod-v2026.03.15.1
```
