# PRD v2 - TLC Demand Forecasting MLOps
**Projet**: TLC Demand Forecasting MLOps  
**Version**: v2.0  
**Date**: 14 mars 2026  
**Statut**: version recommandee pour une implementation reelle

---

## 1. Decision de conception

La version precedente etait ambitieuse, mais pas assez robuste sur trois points:

1. Elle dependait de Databricks Community Edition alors que Databricks est en train de basculer vers Free Edition, avec des limitations serverless et reseau qui rendent le chemin "Databricks Free -> MLflow prive sur EC2" fragile.
2. Elle exposait MLflow sur internet, alors que la doc MLflow recommande de ne pas exposer le serveur integre en direct.
3. Elle calculait un MAE avec des `actuals` simulees, ce qui ne tient pas en entretien si on te demande d'ou viennent les vraies observations.

### Decision retenue

La base du projet passe a **2 environnements seulement**:

- **ta machine locale**: Terraform, Ansible, ingestion, feature engineering, training, validation
- **AWS**: une EC2 pour MLflow + PostgreSQL + Grafana + timer de prediction, et un bucket S3 pour les donnees et les artifacts

Databricks Free Edition reste **optionnel**, uniquement pour exploration ou notebook demo. Il n'est plus dans le chemin critique.

---

## 2. Objectif du projet

Construire un projet MLOps portfolio qui montre:

- une infra reproductible avec Terraform + Ansible
- un pipeline de prevision de la demande taxi par `zone x heure`
- un suivi d'experiences et un Model Registry avec MLflow
- un dashboard Grafana qui affiche predictions, actuals et erreur
- un mode "pseudo-live" defensable: les donnees avancees dans le temps sont rejouees depuis un jeu historique, et les `actuals` viennent du truth set, pas d'une simulation aleatoire

### Hors scope

- serving temps reel a faible latence
- autoscaling complexe
- Kubernetes
- Databricks Free comme dependance obligatoire
- vraies observations temps reel TLC, qui ne sont pas publiees heure par heure en public

---

## 3. Architecture cible

```text
┌──────────────────────────────────────────────────────────────┐
│                      TA MACHINE LOCALE                       │
│                                                              │
│  terraform apply      -> cree EC2 + S3 + IAM + EIP          │
│  ansible-playbook     -> configure EC2                      │
│  scripts/train.py     -> entraine les modeles               │
│  ssh -L 5000:...      -> tunnel prive vers MLflow           │
│                                                              │
│  Pas de service 24/7 local                                  │
└────────────────────────────┬─────────────────────────────────┘
                             │ SSH + HTTPS
┌────────────────────────────▼─────────────────────────────────┐
│                        AWS EC2 t3.small                      │
│                                                              │
│  PostgreSQL                                                  │
│  ├── mlflow             -> metadata MLflow + registry        │
│  ├── predictions        -> prediction_hour, actuals, MAE     │
│  └── replay_state       -> curseur de demo                   │
│                                                              │
│  MLflow Tracking Server                                      │
│  ├── bind 127.0.0.1:5000                                     │
│  ├── backend store -> PostgreSQL                             │
│  └── artifact proxy -> S3                                    │
│                                                              │
│  Grafana                                                     │
│  ├── bind 0.0.0.0:3000                                       │
│  └── lit PostgreSQL                                          │
│                                                              │
│  systemd timer                                               │
│  └── run_replay_cycle.py -> prediction + reconciliation      │
└────────────────────────────┬─────────────────────────────────┘
                             │ IAM role EC2
┌────────────────────────────▼─────────────────────────────────┐
│                           AWS S3                             │
│                                                              │
│  raw/                  -> donnees TLC sources               │
│  features/             -> datasets agreges zone x heure     │
│  holdout/              -> truth set pour replay demo        │
│  mlflow-artifacts/     -> modèles, plots, artifacts         │
│  reports/              -> drift / evaluation reports        │
└──────────────────────────────────────────────────────────────┘
```

### Pourquoi cette architecture est meilleure

- Elle est deployable sans dependre des limites reseau de Databricks Free.
- Elle garde MLflow prive.
- Elle reste montrable en entretien.
- Elle evite de pretendre a du "live" quand la source TLC est batch.

---

## 4. Mode de demonstration

Le dashboard n'est pas un "live feed" TLC. Il fonctionne en **replay historique**.

### Regle produit

Toutes les heures, un timer fait avancer un curseur temporel:

1. charge l'heure `t` dans le jeu holdout
2. construit les features a partir de l'historique disponible avant `t`
3. charge le modele `champion`
4. ecrit les predictions pour `t`
5. recupere les vraies `actuals` de `t` depuis le holdout
6. calcule l'erreur et met a jour PostgreSQL

Ainsi:

- le dashboard bouge automatiquement
- le MAE est reel sur donnees historiques
- on ne ment pas sur l'origine des observations

### Positionnement entretien

La bonne formulation est:

> "Le dashboard est pseudo-live: il rejoue heure par heure une periode historique holdout. Les predictions sont produites par le modele champion, puis comparees aux observations reelles de cette meme heure."

---

## 5. Repartition des phases

| Phase | Environnement | Notes |
|---|---|---|
| 1. Provision infra | Local -> AWS | Terraform |
| 2. Configuration serveur | Local -> EC2 | Ansible |
| 3. Ingestion donnees TLC | Local | telechargement + upload S3 |
| 4. Feature engineering | Local | DuckDB / Polars / Pandas par lots |
| 5. Training et comparaison | Local + tunnel MLflow | MLflow sur EC2 |
| 6. Promotion champion | Local -> MLflow Registry | alias `champion` |
| 7. Replay monitoring | EC2 | timer systemd |
| 8. Dashboard | EC2 Grafana | lecture PostgreSQL |

### Remarque sur Databricks

Databricks Free Edition peut etre garde en **appendice optionnel** pour:

- exploration rapide
- notebook de demo
- visualisation ad hoc

Il n'est **pas** requis pour:

- l'entrainement principal
- le logging MLflow
- le dashboard
- l'execution recurrente

---

## 6. Structure de projet recommandee

```text
tlc-mlops/
|
├── terraform/
│   ├── main.tf
│   ├── variables.tf
│   ├── outputs.tf
│   ├── versions.tf
│   ├── inventory.tftpl
│   └── terraform.tfvars.example
|
├── ansible/
│   ├── inventory.ini
│   ├── collections/
│   │   └── requirements.yml
│   ├── group_vars/
│   │   └── all.yml
│   ├── playbooks/
│   │   ├── site.yml
│   │   ├── postgresql.yml
│   │   ├── mlflow.yml
│   │   ├── grafana.yml
│   │   └── prediction_timer.yml
│   └── templates/
│       ├── mlflow.service.j2
│       ├── grafana-datasource.yml.j2
│       ├── tlc-replay.service.j2
│       └── tlc-replay.timer.j2
|
├── data/
│   ├── raw/
│   ├── processed/
│   └── holdout/
|
├── notebooks/
│   └── exploration.ipynb
|
├── scripts/
│   ├── ingest_tlc.py
│   ├── build_features.py
│   ├── train_models.py
│   ├── evaluate_models.py
│   └── promote_champion.py
|
├── prediction_service/
│   ├── run_replay_cycle.py
│   ├── feature_builder.py
│   └── sql/
│       └── schema.sql
|
├── requirements/
│   ├── local.txt
│   └── ec2.txt
|
└── README.md
```

---

## 7. Infrastructure AWS

### Ressources creees

- 1 EC2 `t3.small`
- 1 Elastic IP
- 1 bucket S3
- 1 IAM role attachee a l'EC2 pour l'acces S3
- 1 security group

### Regles reseau

Ports exposes:

- `22/tcp` depuis ton IP uniquement
- `3000/tcp` depuis ton IP uniquement

Ports non exposes publiquement:

- `5000/tcp` MLflow
- `5432/tcp` PostgreSQL

### Regle de securite

MLflow ne doit pas etre public.  
L'acces se fait via **SSH tunnel**:

```bash
ssh -N -L 5000:127.0.0.1:5000 ubuntu@EC2_IP
```

Ensuite, depuis ta machine:

```bash
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
```

### Bucket S3

Le nom du bucket doit etre **globalement unique**.

Exemple:

```hcl
resource "random_id" "bucket_suffix" {
  byte_length = 4
}

resource "aws_s3_bucket" "mlops" {
  bucket = "${var.project_name}-${random_id.bucket_suffix.hex}"
}
```

### Bonnes pratiques Terraform a appliquer

- bloquer l'acces public S3
- activer le chiffrement S3
- activer IMDSv2 sur l'EC2
- ne pas faire passer de secrets dans le state Terraform
- ne pas mettre le mot de passe PostgreSQL dans `terraform.tfvars`

### Variables Terraform

```hcl
variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "project_name" {
  type    = string
  default = "tlc-mlops"
}

variable "instance_type" {
  type    = string
  default = "t3.small"
}

variable "allowed_cidr" {
  description = "Ton IP publique en /32"
  type        = string
}

variable "key_pair_name" {
  type = string
}
```

### Outputs Terraform utiles

```hcl
output "server_ip" {
  value = aws_eip.mlops_server.public_ip
}

output "grafana_url" {
  value = "http://${aws_eip.mlops_server.public_ip}:3000"
}

output "ssh_mlflow_tunnel" {
  value = "ssh -N -L 5000:127.0.0.1:5000 ubuntu@${aws_eip.mlops_server.public_ip}"
}

output "ssh_command" {
  value = "ssh ubuntu@${aws_eip.mlops_server.public_ip}"
}
```

---

## 8. Configuration EC2 avec Ansible

### Important

Les modules PostgreSQL ne viennent pas de `ansible.builtin`.  
Il faut installer la collection `community.postgresql`.

### `ansible/collections/requirements.yml`

```yaml
collections:
  - name: community.postgresql
  - name: ansible.posix
```

Installation:

```bash
ansible-galaxy collection install -r collections/requirements.yml
```

### Variables Ansible

Les secrets passent par variable d'environnement locale ou Ansible Vault, pas via Terraform.

Exemple:

```bash
export MLFLOW_DB_PASSWORD='change-me'
ansible-playbook -i inventory.ini playbooks/site.yml \
  -e "mlflow_db_password=$MLFLOW_DB_PASSWORD"
```

### `group_vars/all.yml`

```yaml
project_name: tlc-mlops
app_root: /opt/tlc-mlops
venv_path: /opt/tlc-mlops/.venv
mlflow_port: 5000
grafana_port: 3000
postgres_port: 5432

ec2_python_packages:
  - mlflow
  - boto3
  - psycopg2-binary
  - pandas
  - numpy
  - scikit-learn
  - lightgbm
  - xgboost
  - evidently
```

### Roles techniques de l'EC2

- PostgreSQL
- MLflow Tracking Server
- Grafana
- replay timer

### Choix de runtime

Le serveur EC2 **ne fait pas d'entrainement**.  
Il ne fait que:

- servir MLflow
- stocker metadata et predictions
- lancer la boucle de replay

### Contrainte modele en production

Pour simplifier l'inference sur EC2:

- **LightGBM** et **XGBoost** sont eligibles au label `champion`
- **Prophet** reste un modele de benchmark / comparaison offline

Ce choix reduit le risque de runtime casse cote inference.

---

## 9. PostgreSQL

### Bases

- `mlflow`
- `predictions`

### Schema `predictions`

La table doit etre idempotente. On veut des `UPSERT`, pas des doublons.

```sql
CREATE TABLE IF NOT EXISTS zone_predictions (
    id               BIGSERIAL PRIMARY KEY,
    target_hour      TIMESTAMP NOT NULL,
    generated_at     TIMESTAMP NOT NULL DEFAULT NOW(),
    zone_id          INTEGER NOT NULL,
    zone_name        VARCHAR(100) NOT NULL,
    borough          VARCHAR(50),
    latitude         DOUBLE PRECISION,
    longitude        DOUBLE PRECISION,
    predicted_trips  DOUBLE PRECISION NOT NULL,
    actual_trips     DOUBLE PRECISION,
    absolute_error   DOUBLE PRECISION,
    model_name       VARCHAR(100) NOT NULL,
    model_version    VARCHAR(50) NOT NULL,
    model_alias      VARCHAR(50) NOT NULL DEFAULT 'champion',
    replay_mode      BOOLEAN NOT NULL DEFAULT TRUE,
    status           VARCHAR(20) NOT NULL DEFAULT 'predicted',
    created_at       TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (target_hour, zone_id, model_alias)
);

CREATE INDEX IF NOT EXISTS idx_zone_predictions_hour
  ON zone_predictions(target_hour DESC);

CREATE INDEX IF NOT EXISTS idx_zone_predictions_zone
  ON zone_predictions(zone_id);

CREATE TABLE IF NOT EXISTS replay_state (
    id              SMALLINT PRIMARY KEY DEFAULT 1,
    current_hour    TIMESTAMP NOT NULL,
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
```

### Pourquoi cette table est meilleure

- `target_hour` est plus clair que `prediction_time`
- contrainte `UNIQUE` pour eviter les doublons du timer
- `status` permet de distinguer `predicted` et `reconciled`
- `replay_mode` explicite la nature demo

---

## 10. MLflow

### Mode de deploiement

MLflow tourne sur l'EC2, mais **uniquement en local sur la machine distante**:

```bash
mlflow server \
  --host 127.0.0.1 \
  --port 5000 \
  --backend-store-uri postgresql+psycopg2://mlflow:${MLFLOW_DB_PASSWORD}@localhost/mlflow \
  --artifacts-destination s3://<bucket>/mlflow-artifacts
```

### Pourquoi `--artifacts-destination`

On veut que **le serveur MLflow** accede a S3 et proxy les artifacts.  
Les clients n'ont pas a ecrire directement dans S3.

### Consequences

- training local loggue par HTTP vers MLflow
- le serveur EC2 stocke metadata en PostgreSQL
- le serveur EC2 ecrit les artifacts en S3 via son IAM role

### Service systemd

Exemple de template:

```ini
[Unit]
Description=MLflow Tracking Server
After=network.target postgresql.service

[Service]
User=ubuntu
WorkingDirectory=/opt/tlc-mlops
EnvironmentFile=/etc/tlc-mlops/mlflow.env
ExecStart={{ venv_path }}/bin/mlflow server \
  --host 127.0.0.1 \
  --port {{ mlflow_port }} \
  --backend-store-uri ${BACKEND_STORE_URI} \
  --artifacts-destination s3://{{ s3_bucket }}/mlflow-artifacts
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Contenu de `/etc/tlc-mlops/mlflow.env`:

```bash
BACKEND_STORE_URI=postgresql+psycopg2://mlflow:<mot_de_passe>@localhost/mlflow
```

### Workflow local

```bash
ssh -N -L 5000:127.0.0.1:5000 ubuntu@EC2_IP
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
python scripts/train_models.py
```

---

## 11. Ingestion et feature engineering

### Source

Jeu de donnees TLC public, stocke en local puis versionne dans S3.

### Pipeline recommande

1. `scripts/ingest_tlc.py`
2. `scripts/build_features.py`
3. upload du dataset features vers `s3://bucket/features/...`
4. split train / validation / holdout chronologique

### Outils recommandes

- DuckDB pour agreger rapidement du parquet
- Polars ou Pandas pour la preparation finale

### Pourquoi local est suffisant

Le volume est grand en brut, mais reste raisonnable en traitement par lots pour un portfolio, surtout si:

- tu agreges par mois
- tu ecris en parquet
- tu limites les colonnes
- tu travailles ensuite sur `zone x heure`

Si ta machine locale est trop juste, tu peux remplacer cette phase par un workspace Databricks **trial** ou **paye**, mais ce n'est pas la baseline du projet.

---

## 12. Training

### Modeles compares

- LightGBM
- XGBoost
- Prophet

### Regle produit

- `champion` ne peut pointer que vers LightGBM ou XGBoost
- Prophet reste en benchmark

### Strategie de validation

- split temporel strict
- walk-forward validation
- metriques: MAE, RMSE, MAPE si utile

### Workflow

1. charger les features
2. lancer les trainings
3. logger metrics et artifacts dans MLflow
4. comparer les runs
5. promouvoir le meilleur modele arbre en alias `champion`

---

## 13. Prediction service

### Principe

Le service `run_replay_cycle.py` remplace l'ancien `predict_hourly.py` qui simulait les features et les `actuals`.

### Comportement attendu

1. lire `replay_state.current_hour`
2. construire les features pour cette heure a partir de l'historique avant `current_hour`
3. charger `models:/tlc-demand-forecasting@champion`
4. predire pour toutes les zones suivies
5. lire les vraies `actuals` du holdout pour `current_hour`
6. faire un `UPSERT` dans `zone_predictions`
7. incrementer `replay_state.current_hour` d'une heure

### Ce que le service ne doit plus faire

- pas de `np.random.randint()` pour fabriquer des predictions
- pas de `actual_trips` simulees
- pas de duplication de lignes a chaque run

### Choix de scheduler

Utiliser **systemd timer**, pas cron.

Pourquoi:

- plus fiable
- plus simple a deboguer avec `journalctl`
- pas de probleme de shell `source`

### Exemple de timer

```ini
[Unit]
Description=Run TLC replay cycle hourly

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
```

---

## 14. Grafana

### Exposition

Grafana peut rester accessible sur `3000/tcp` depuis ton IP uniquement.

### Provisioning

La datasource PostgreSQL doit etre provisionnee par fichier YAML, pas par appel HTTP API ad hoc.

### Exemple de datasource provisionnee

```yaml
apiVersion: 1

datasources:
  - name: PostgreSQL-Predictions
    type: postgres
    access: proxy
    url: localhost:5432
    user: mlflow
    jsonData:
      database: predictions
      sslmode: disable
      postgresVersion: 1500
      timescaledb: false
    secureJsonData:
      password: $MLFLOW_DB_PASSWORD
```

### Panels recommandes

1. Geomap par `zone`
2. Time series `predicted vs actual`
3. Table MAE par zone
4. Stat cards: MAE global, zones actives, version champion, nombre de predictions

### Requetes SQL conseillees

#### Geomap

```sql
SELECT
    zone_name AS name,
    latitude,
    longitude,
    AVG(predicted_trips) AS predicted,
    AVG(actual_trips) AS actual,
    AVG(absolute_error) AS error,
    borough
FROM zone_predictions
WHERE target_hour >= NOW() - INTERVAL '24 hours'
GROUP BY zone_name, latitude, longitude, borough
ORDER BY predicted DESC;
```

#### Time series

```sql
SELECT
    target_hour AS time,
    predicted_trips,
    actual_trips
FROM zone_predictions
WHERE
    zone_name = '$zone'
    AND target_hour BETWEEN $__timeFrom() AND $__timeTo()
ORDER BY target_hour;
```

#### MAE par zone

```sql
SELECT
    zone_name,
    borough,
    ROUND(AVG(predicted_trips)::numeric, 1) AS avg_predicted,
    ROUND(AVG(actual_trips)::numeric, 1) AS avg_actual,
    ROUND(AVG(absolute_error)::numeric, 1) AS mae,
    COUNT(*) AS n_predictions,
    MAX(model_version) AS model_version
FROM zone_predictions
WHERE target_hour >= NOW() - INTERVAL '7 days'
GROUP BY zone_name, borough
ORDER BY mae ASC;
```

---

## 15. Commandes d'execution

### Terraform

```bash
cd terraform
terraform init
terraform plan -var-file=terraform.tfvars
terraform apply -var-file=terraform.tfvars
```

### Inventory genere par Terraform

L'inventory ne doit contenir que:

- IP
- utilisateur SSH
- chemin de cle
- nom du bucket

Il ne doit pas contenir le mot de passe PostgreSQL.

### Ansible

```bash
cd ansible
ansible-galaxy collection install -r collections/requirements.yml

export MLFLOW_DB_PASSWORD='change-me'
ansible-playbook -i inventory.ini playbooks/site.yml \
  -e "mlflow_db_password=$MLFLOW_DB_PASSWORD"
```

### Tunnel MLflow

```bash
ssh -N -L 5000:127.0.0.1:5000 ubuntu@EC2_IP
```

### Training local

```bash
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
python scripts/train_models.py
python scripts/promote_champion.py
```

### Verification

```bash
curl http://EC2_IP:3000/api/health
ssh ubuntu@EC2_IP 'systemctl status mlflow'
ssh ubuntu@EC2_IP 'systemctl status grafana-server'
ssh ubuntu@EC2_IP 'systemctl status tlc-replay.timer'
```

### Destruction

```bash
cd terraform
terraform destroy -var-file=terraform.tfvars
```

---

## 16. Ajustements obligatoires par rapport a l'ancienne version

### A supprimer

- dependance a Databricks Community Edition
- ouverture publique du port MLflow `5000`
- `predict_hourly.py` avec random features / random actuals
- secrets injectes depuis Terraform dans `inventory.ini`
- datasource Grafana creee via POST API `admin/admin`
- cron qui utilise `source`

### A garder

- Terraform + Ansible
- EC2 + S3 + PostgreSQL + Grafana + MLflow
- dashboard par zone
- registry `champion`
- role de demonstration "pseudo-live"

### A renommer

- `prediction_time` -> `target_hour`
- `predict_hourly.py` -> `run_replay_cycle.py`
- "live dashboard" -> "pseudo-live replay dashboard"

---

## 17. Appendice Databricks Free Edition

Databricks Free Edition peut rester un bonus si tu veux montrer un notebook cloud, mais sous ces regles:

1. pas dans le chemin critique
2. pas de dependance obligatoire pour MLflow
3. pas de dependance obligatoire pour l'acces S3
4. pas de promesse d'execution recurrente 24/7

### Usage acceptable

- EDA notebook
- visualisation rapide
- prototype de feature engineering

### Usage a eviter dans ce projet

- architecture "Databricks Free -> MLflow EC2 public"
- architecture "Databricks Free -> S3 custom storage" comme prerequis central
- architecture qui suppose un egress stable et controle alors que Free Edition reste serverless et limitee

---

## 18. Verdict

La bonne version du projet est:

- **2 environnements en baseline**
- **MLflow prive via tunnel SSH**
- **Grafana public mais IP allowliste**
- **replay historique avec vraies actuals**
- **Databricks Free seulement en option**

Cette version est:

- plus simple
- plus credible
- plus securisee
- plus facile a mettre en place
- plus solide en entretien

---

## 19. Sources de reference

- Databricks Free Edition: https://docs.databricks.com/aws/en/getting-started/free-edition
- Databricks Free Edition limitations: https://docs.databricks.com/aws/en/getting-started/free-edition-limitations
- CE migration: https://docs.databricks.com/aws/en/getting-started/ce-migration
- Serverless limitations: https://docs.databricks.com/aws/en/compute/serverless/limitations
- MLflow tracking server architecture: https://mlflow.org/docs/latest/self-hosting/architecture/tracking-server/
- MLflow security: https://mlflow.org/docs/latest/self-hosting/security/network/
- Ansible `community.postgresql`: https://docs.ansible.com/ansible/latest/collections/community/postgresql/
- Grafana provisioning: https://grafana.com/docs/grafana/latest/administration/provisioning/
