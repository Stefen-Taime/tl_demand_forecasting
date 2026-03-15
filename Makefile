SHELL := /bin/bash

TF_DIR := terraform
ANSIBLE_DIR := ansible
PYTHON := python3
VENV := .venv
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python
TFVARS := terraform.tfvars
YEAR ?= 2024
MONTHS ?= 1 2 3
TAXI_TYPE ?= yellow

.PHONY: venv install-local py-check test quality-gates tf-init tf-plan tf-apply tf-output tf-validate ansible-collections ansible-syntax ansible-apply centroids ingest features train evaluate promote grafana-health replay-now replay-backfill destroy

venv:
	$(PYTHON) -m venv $(VENV)

install-local: venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements/local.txt

py-check:
	$(PYTHON) -m py_compile scripts/*.py prediction_service/*.py

test:
	$(PY) -m pytest -q

quality-gates:
	$(PY) scripts/check_quality.py

tf-init:
	terraform -chdir=$(TF_DIR) init

tf-plan:
	terraform -chdir=$(TF_DIR) plan -var-file=$(TFVARS)

tf-apply:
	terraform -chdir=$(TF_DIR) apply -var-file=$(TFVARS)

tf-output:
	terraform -chdir=$(TF_DIR) output

tf-validate:
	terraform -chdir=$(TF_DIR) fmt -check
	terraform -chdir=$(TF_DIR) init -backend=false -upgrade
	terraform -chdir=$(TF_DIR) validate

ansible-collections:
	cd $(ANSIBLE_DIR) && ../$(VENV)/bin/ansible-galaxy collection install -r collections/requirements.yml

ansible-syntax:
	mkdir -p /tmp/tlc-ansible-local
	cd $(ANSIBLE_DIR) && ANSIBLE_LOCAL_TEMP=/tmp/tlc-ansible-local ../$(VENV)/bin/ansible-playbook -i inventory.ini playbooks/site.yml --syntax-check -e "mlflow_db_password=dummy"

ansible-apply:
	: $${MLFLOW_DB_PASSWORD:?Set MLFLOW_DB_PASSWORD}
	cd $(ANSIBLE_DIR) && ../$(VENV)/bin/ansible-playbook -i inventory.ini playbooks/site.yml -e "mlflow_db_password=$$MLFLOW_DB_PASSWORD"

centroids:
	: $${S3_BUCKET:?Set S3_BUCKET}
	$(PY) scripts/build_zone_centroids.py --upload-s3 --s3-bucket "$$S3_BUCKET"

ingest:
	: $${S3_BUCKET:?Set S3_BUCKET}
	$(PY) scripts/ingest_tlc.py --taxi-type $(TAXI_TYPE) --year $(YEAR) --months $(MONTHS) --upload-s3 --s3-bucket "$$S3_BUCKET"

features:
	: $${S3_BUCKET:?Set S3_BUCKET}
	$(PY) scripts/build_features.py --upload-s3 --s3-bucket "$$S3_BUCKET"

train:
	: $${MLFLOW_TRACKING_URI:?Set MLFLOW_TRACKING_URI}
	$(PY) scripts/train_models.py

evaluate:
	: $${MLFLOW_TRACKING_URI:?Set MLFLOW_TRACKING_URI}
	$(PY) scripts/evaluate_models.py

promote:
	: $${MLFLOW_TRACKING_URI:?Set MLFLOW_TRACKING_URI}
	$(PY) scripts/promote_champion.py

grafana-health:
	: $${EC2_IP:?Set EC2_IP}
	curl http://$$EC2_IP:3000/api/health

replay-now:
	: $${EC2_IP:?Set EC2_IP}
	ssh ubuntu@$$EC2_IP 'sudo systemctl start tlc-replay.service && sudo journalctl -u tlc-replay.service -n 50 --no-pager'

replay-backfill:
	: $${EC2_IP:?Set EC2_IP}
	ssh ubuntu@$$EC2_IP "sudo bash -lc 'set -a && . /etc/tlc-mlops/replay.env && set +a && cd /opt/tlc-mlops/prediction_service && exec sudo -E -u ubuntu /opt/tlc-mlops/.venv/bin/python /opt/tlc-mlops/prediction_service/run_replay_cycle.py --until-wrap --prune-window'"

destroy:
	terraform -chdir=$(TF_DIR) destroy -var-file=$(TFVARS)
