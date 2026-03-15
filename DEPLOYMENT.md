# Deployment Runbook

Recommended execution order to deploy the project end to end.

## 1. Prerequisites

You need the following locally:

- `terraform`
- `python3`
- `pip`
- valid AWS credentials
- an existing AWS key pair

Verify:

```bash
terraform version
python3 --version
aws sts get-caller-identity
```

## 2. Prepare Terraform

Copy the example files:

```bash
cp terraform/terraform.tfvars.example terraform/terraform.tfvars
cp terraform/backend.hcl.example terraform/backend.hcl
```

Then fill `terraform/terraform.tfvars` with at least:

- `aws_region = "ca-central-1"` for Montreal
- `instance_type = "m6i.large"` to avoid the burst-credit limits that made smaller `t3` instances slow
- `allowed_cidr`
- `key_pair_name`
- `enable_github_actions_oidc = true` if you want GitHub CD
- `github_repository = "owner/repo"`
- `github_environments = ["staging", "production"]`

## 3. Provision AWS

```bash
terraform -chdir=terraform init
terraform -chdir=terraform init -backend-config=backend.hcl
terraform -chdir=terraform plan -var-file=terraform.tfvars
terraform -chdir=terraform apply -var-file=terraform.tfvars
```

Export the useful outputs:

```bash
export EC2_IP=$(terraform -chdir=terraform output -raw server_ip)
export S3_BUCKET=$(terraform -chdir=terraform output -raw s3_bucket)
terraform -chdir=terraform output
```

## 4. Install the local Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements/local.txt
export SSH_PRIVATE_KEY_PATH=~/.ssh/my-aws-key.pem
make inventory
```

Verify that Ansible is available:

```bash
.venv/bin/ansible-playbook --version
```

## 5. Configure the EC2 instance

Install the Ansible collections:

```bash
source .venv/bin/activate
cd ansible
../.venv/bin/ansible-galaxy collection install -r collections/requirements.yml
```

Configure the server:

```bash
export MLFLOW_DB_PASSWORD='change-me-now'
export GRAFANA_ADMIN_PASSWORD='change-me-to-a-long-random-password'
../.venv/bin/ansible-playbook -i inventory.ini playbooks/site.yml \
  -e "mlflow_db_password=$MLFLOW_DB_PASSWORD" \
  -e "grafana_admin_password=$GRAFANA_ADMIN_PASSWORD"
cd ..
```

## 6. Ingest TLC data

Current project data window:

```bash
python scripts/build_zone_centroids.py \
  --upload-s3 \
  --s3-bucket "$S3_BUCKET"

python scripts/ingest_tlc.py \
  --year 2024 \
  --months 1 2 3 4 5 6 \
  --upload-s3 \
  --s3-bucket "$S3_BUCKET"
```

If you want a smaller first run, you can start with fewer months and extend later.

## 7. Build features and holdout

```bash
python scripts/build_features.py \
  --upload-s3 \
  --s3-bucket "$S3_BUCKET"
```

The build also filters pickups that fall outside the time range implied by the raw monthly files, which removes bad timestamps.

Expected outputs:

- `data/processed/features.parquet`
- `data/processed/train_features.parquet`
- `data/holdout/holdout_features.parquet`

## 8. Open the MLflow tunnel

In a **separate terminal**, run:

```bash
ssh -i "$SSH_PRIVATE_KEY_PATH" -N -L 5000:127.0.0.1:5000 ubuntu@$(terraform -chdir=terraform output -raw server_ip)
```

When the tunnel is up, in your main terminal:

```bash
source .venv/bin/activate
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
```

## 9. Train the models

```bash
python scripts/train_models.py
python scripts/evaluate_models.py
python scripts/promote_champion.py
```

Notes:

- `train_models.py` logs baselines first, then `lightgbm` and `xgboost`
- validation is expanding-window CV on the training set plus one frozen final holdout
- `promote_champion.py` only promotes `lightgbm` or `xgboost`
- promotion is blocked if the challenger does not beat the best baseline, if `holdout_mase >= 1`, or if it regresses versus the current champion
- decision artifacts are written into `reports/run_summary.csv`, `reports/best_run.json`, and `reports/promotion_decision.json`

## 10. Verify AWS services

```bash
curl http://$EC2_IP:3000/api/health
```

Verify the systemd services:

```bash
ssh ubuntu@$EC2_IP 'systemctl status postgresql --no-pager'
ssh ubuntu@$EC2_IP 'systemctl status mlflow --no-pager'
ssh ubuntu@$EC2_IP 'systemctl status grafana-server --no-pager'
ssh ubuntu@$EC2_IP 'systemctl status tlc-replay.timer --no-pager'
```

## 10.b Run the quality gates

```bash
make test
make quality-gates
make tf-validate
make ansible-syntax
```

These checks are also reproduced in [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

## 11. Run one manual replay cycle

If you do not want to wait for the next scheduled timer tick:

```bash
ssh ubuntu@$EC2_IP 'sudo systemctl start tlc-replay.service'
ssh ubuntu@$EC2_IP 'sudo journalctl -u tlc-replay.service -n 50 --no-pager'
```

To backfill the full holdout window:

```bash
export EC2_IP=$(terraform -chdir=terraform output -raw server_ip)
make replay-backfill
```

The full backfill also prunes stale replay rows that no longer belong to the current holdout.

## 12. Open Grafana

```bash
echo "http://$EC2_IP:3000"
```

Provisioned dashboards:

- `TLC Demand Forecasting`
- `TLC Operations`

Provisioned alerts:

- `TLC Replay Freshness`
- `TLC Replay Coverage`
- `TLC Model MAE 24h`

## 13. Normal working loop

Once infrastructure is already in place:

```bash
source .venv/bin/activate
export EC2_IP=$(terraform -chdir=terraform output -raw server_ip)
export S3_BUCKET=$(terraform -chdir=terraform output -raw s3_bucket)
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
```

Then the normal iteration cycle is:

1. change scripts or parameters
2. rebuild features if needed
3. reopen the MLflow tunnel
4. rerun `train_models.py`
5. rerun `promote_champion.py`
6. run `tlc-replay.service` manually to see the result immediately

## 14. Databricks

The Databricks notebooks are optional:

- `databricks/01_eda.py`
- `databricks/02_feature_prototype.py`
- `databricks/03_sandbox_training.py`

You can import them into Databricks for:

- EDA
- feature engineering prototypes
- notebook demo work

The main deployment path does not depend on them.

## 15. Destroy everything

```bash
terraform -chdir=terraform destroy -var-file=terraform.tfvars
```

## 16. GitHub CD via OIDC

Deployment workflows:

- [`.github/workflows/deploy-staging.yml`](.github/workflows/deploy-staging.yml) for `staging`
- [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) for `production`, triggered by `prod-v*` tags
- [`.github/workflows/deploy-reusable.yml`](.github/workflows/deploy-reusable.yml) for shared logic

GitHub Environment variables to define for both `staging` and `production`:

- `AWS_DEPLOY_ROLE_ARN`
- `AWS_REGION`
- `PROJECT_NAME`
- `EC2_INSTANCE_TYPE`
- `ADMIN_ALLOWED_CIDR`
- `EC2_KEY_PAIR_NAME`
- `TF_STATE_BUCKET`
- `TF_STATE_KEY`
- `TF_LOCK_TABLE`

GitHub Environment secrets to define for both `staging` and `production`:

- `EC2_SSH_PRIVATE_KEY`
- `MLFLOW_DB_PASSWORD`
- `GRAFANA_ADMIN_PASSWORD`

Recommended setup:

- `staging` deploys automatically on `push` to `main`
- `production` deploys from an immutable `prod-v*` tag
- `workflow_dispatch` remains available as break-glass for production
- the `production` Terraform state manages the shared GitHub -> AWS OIDC role, and `staging` reuses it
- `TF_STATE_KEY` must be distinct per environment, for example `terraform/state/staging.tfstate` and `terraform/state/production.tfstate`
- `PROJECT_NAME` should also be distinct, for example `tlc-mlops-staging` and `tlc-mlops`
- `GRAFANA_ADMIN_PASSWORD` must be set before the first deployment so Grafana never stays on a default password

The workflow uses OIDC for AWS authentication, then opens a temporary SSH rule only for the GitHub runner IP during deployment.

Current GitHub limitation:

- on a `private` repo with the current `GitHub Free` plan, native environment `required reviewers` are not available
- if you want native approval before production, either make the repo `public` or move to a GitHub plan that exposes that feature for your setup

Recommended production release flow:

```bash
git tag prod-v2026.03.15.1
git push origin prod-v2026.03.15.1
```
