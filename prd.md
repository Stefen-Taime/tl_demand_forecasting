# PRD v3 - TLC Demand Forecasting MLOps

**Projet**: TLC Demand Forecasting MLOps
**Version**: v3.0
**Date**: 15 mars 2026
**Statut**: aligne sur l'implementation reelle du repository

---

## 0. Resume executif

Ce projet construit une chaine MLOps complete pour prevoir la demande TLC par `zone x heure`, evaluer plusieurs modeles, promouvoir un `champion`, puis exposer les predictions et les erreurs dans Grafana.

Le systeme n'essaie pas de simuler un vrai temps reel TLC, car la source publique ne le permet pas. A la place, il utilise un `replay historique`:

- un holdout final est reserve apres le training
- un service sur EC2 rejoue ce holdout heure par heure
- le modele `champion` produit les predictions
- les vraies observations du holdout servent d'`actuals`
- Grafana affiche predictions, erreurs et tendances

Le projet est "prod-like" car il contient:

- une infra AWS versionnee
- une separation `staging` / `production`
- du CI/CD GitHub
- des quality gates
- un vrai protocole de validation temporelle
- une promotion modele sous conditions
- des alertes d'exploitation

---

## 1. Probleme et objectif

### Probleme

Le projet doit montrer comment transformer des donnees de trajets taxis brutes en un systeme MLOps lisible et defendable.

Sans cadre MLOps, on obtient souvent:

- des notebooks impossibles a rejouer
- des metriques fragiles
- des modeles sans gouvernance
- aucun lien entre le training et l'observabilite

### Objectif

Construire un systeme qui couvre tout le cycle:

1. ingerer les donnees TLC brutes
2. produire un dataset `zone x heure`
3. entrainer plusieurs candidats
4. les comparer a des baselines
5. promouvoir un `champion` si et seulement si les garde-fous passent
6. faire tourner un replay historique
7. afficher le resultat dans Grafana

### Resultat attendu

A la fin, un observateur doit pouvoir comprendre:

- d'ou viennent les donnees
- comment les features sont construites
- quels modeles ont ete compares
- pourquoi le champion a ete promu
- comment le dashboard est alimente
- comment l'infra est deployee et securisee

---

## 2. Scope et non-goals

### In scope

- provisionnement AWS avec Terraform
- configuration EC2 avec Ansible
- stockage S3
- tracking et registry avec MLflow
- predictions et monitoring dans PostgreSQL + Grafana
- CI GitHub Actions
- CD `staging` et `production`
- validation temporelle et holdout final
- quality gates et alertes

### Hors scope

- vrai streaming TLC temps reel
- serving HTTP de predictions a faible latence
- Kubernetes
- feature store dedie
- online learning
- orchestration type Airflow ou Dagster
- experimentation distribuee a grande echelle

### Role de Databricks

Databricks n'est pas une dependance critique.

Il reste optionnel pour:

- EDA
- demonstration notebook
- prototypage rapide

Le chemin de production ne depend pas de Databricks.

---

## 3. Utilisateurs et cas d'usage

### Utilisateur principal

Un recruteur technique, un hiring manager ou une equipe data/ML qui veut evaluer la maturite du projet.

### Cas d'usage principaux

1. Lire le pipeline de bout en bout
2. Rejouer l'entrainement
3. Voir un modele promu selon des criteres explicites
4. Ouvrir Grafana et comprendre les erreurs
5. Deployer l'infra en `staging` puis `production`

---

## 4. Architecture cible

### 4.1 Vue systeme

```text
                               +-----------------------+
                               |      GitHub           |
                               |  source + Actions     |
                               +-----------+-----------+
                                           |
                                           | OIDC + CI/CD
                                           v
 +----------------------+        +-----------------------------+
 | Machine locale       |        | AWS environment            |
 |                      |        | staging ou production      |
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
                         lit zone_predictions et expose la vue metier
```

### 4.2 Vue data lineage

```text
Fichiers TLC bruts
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
 Grafana dashboard + alert rules
```

### 4.3 Pourquoi cette architecture

Cette architecture a ete retenue car elle:

- isole le training et l'exploitation
- garde MLflow prive
- versionne l'infra
- permet un `staging` et un `production` realistes
- evite de raconter une fausse histoire de streaming

---

## 5. Composants et responsabilites

| Composant | Fichiers | Ce qu'il fait | Pourquoi il existe |
| --- | --- | --- | --- |
| Terraform | [`terraform/main.tf`](/Users/stefen/tl_demand_forecasting/terraform/main.tf) | cree EC2, EIP, S3, IAM, SG, OIDC | rendre l'infra reproductible |
| Ansible base | [`site.yml`](/Users/stefen/tl_demand_forecasting/ansible/playbooks/site.yml) | installe le socle Ubuntu | eviter toute config manuelle |
| Ansible PostgreSQL | [`postgresql.yml`](/Users/stefen/tl_demand_forecasting/ansible/playbooks/postgresql.yml) | cree db/users/schema | servir de backend MLflow et de db predictions |
| Ansible MLflow | [`mlflow.yml`](/Users/stefen/tl_demand_forecasting/ansible/playbooks/mlflow.yml) | installe MLflow et le service systemd | suivre et registrer les modeles |
| Ansible Grafana | [`grafana.yml`](/Users/stefen/tl_demand_forecasting/ansible/playbooks/grafana.yml) | installe Grafana, datasource, dashboard, alertes | donner une lecture metier |
| Ansible replay | [`prediction_timer.yml`](/Users/stefen/tl_demand_forecasting/ansible/playbooks/prediction_timer.yml) | deploie le service replay + timer | simuler un flux de production |
| Ingestion | [`ingest_tlc.py`](/Users/stefen/tl_demand_forecasting/scripts/ingest_tlc.py) | telecharge les parquets TLC | figer la source brute |
| Geographie | [`build_zone_centroids.py`](/Users/stefen/tl_demand_forecasting/scripts/build_zone_centroids.py) | calcule les centroides de zones | alimenter le Geomap |
| Feature engineering | [`build_features.py`](/Users/stefen/tl_demand_forecasting/scripts/build_features.py) | construit le dataset modele | transformer la donnee brute en probleme supervise |
| Training | [`train_models.py`](/Users/stefen/tl_demand_forecasting/scripts/train_models.py) | entraine baselines et challengers | comparer proprement les candidats |
| Evaluation | [`evaluate_models.py`](/Users/stefen/tl_demand_forecasting/scripts/evaluate_models.py) | exporte des rapports lisibles | piloter quality gates et review |
| Promotion | [`promote_champion.py`](/Users/stefen/tl_demand_forecasting/scripts/promote_champion.py) | met a jour `candidate` / `champion` | gouverner la release modele |
| Quality gates | [`check_quality.py`](/Users/stefen/tl_demand_forecasting/scripts/check_quality.py) | impose des seuils versionnes | eviter les regressions silencieuses |
| Replay service | [`run_replay_cycle.py`](/Users/stefen/tl_demand_forecasting/prediction_service/run_replay_cycle.py) | genere predictions + actuals | fermer la boucle monitoring |

---

## 6. Contrat de donnees

### 6.1 Source brute

Source principale:

- fichiers TLC mensuels `yellow_tripdata_YYYY-MM.parquet`

Sources annexes:

- `taxi_zone_lookup.csv`
- shapefile `taxi_zones.zip`

### 6.2 Grain d'analyse

Le grain du projet est:

- `zone_id`
- `target_hour`

La cible est:

- `target_trips`

Interpretation metier:

- pour chaque `zone_id`
- a chaque `target_hour`
- on predit le nombre de trajets attendus sur cette heure

Le systeme ne predit donc pas:

- un trajet individuel
- une destination
- un prix
- un temps d'attente

### 6.3 Colonnes metadata

Le dataset modele contient notamment:

- `target_hour`
- `zone_id`
- `zone_name`
- `borough`
- `latitude`
- `longitude`
- `target_trips`

### 6.4 Features

Features calendaires:

- `hour_of_day`
- `day_of_week`
- `day_of_month`
- `month`
- `is_weekend`
- `hour_sin`
- `hour_cos`
- `dow_sin`
- `dow_cos`

Features d'historique:

- `lag_1h`
- `lag_2h`
- `lag_24h`
- `lag_168h`

Features de tendance:

- `rolling_mean_6h`
- `rolling_mean_24h`
- `rolling_std_24h`
- `trend_ratio`

### 6.5 Regles de qualite data

Le pipeline applique deja plusieurs garde-fous:

- rejet des datasets vides
- rejet des `target_hour` nuls
- rejet des targets negatives
- rejet des doublons `zone x heure`
- verification d'absence de fuite entre train et holdout
- filtrage des timestamps hors plage deduite des fichiers sources

### 6.6 Fenetre actuellement ingeree et evaluee

A la date du `15 mars 2026`, le repository et la prod s'appuient sur:

- `yellow_tripdata_2024-01.parquet`
- `yellow_tripdata_2024-02.parquet`
- `yellow_tripdata_2024-03.parquet`
- `yellow_tripdata_2024-04.parquet`
- `yellow_tripdata_2024-05.parquet`
- `yellow_tripdata_2024-06.parquet`

Le dataset complet couvre:

- `1 janvier 2024 00:00:00` -> `30 juin 2024 23:00:00`

Le split reel est:

- `train`: `1 janvier 2024 00:00:00` -> `23 juin 2024 23:00:00`
- `holdout`: `24 juin 2024 00:00:00` -> `30 juin 2024 23:00:00`

Donc:

- l'entrainement se fait sur `1 janvier -> 23 juin`
- l'evaluation finale et le replay portent sur `24 juin -> 30 juin`

---

## 7. Conception ML

### 7.1 Pourquoi un probleme tabulaire

Le projet traite un forecasting agrege et non une sequence multiserie deep learning.

Raisons:

- la granularite `zone x heure` se prete bien aux arbres de gradient boosting
- le cout d'exploitation reste faible
- la lecture du modele est plus simple pour un projet portfolio

### 7.2 Baselines obligatoires

Baselines implementees:

- `seasonal_naive_24h`
- `seasonal_naive_168h`
- `rolling_mean_24h`

Raison:

- une baseline indique si la complexite d'un modele est justifiee
- `lag_24h` et `lag_168h` sont des references naturelles sur une serie horaire

### 7.3 Modeles challengers

Modeles actuellement supportes:

- LightGBM
- XGBoost

Hyperparametres actuels:

`LightGBM`

- `n_estimators=500`
- `learning_rate=0.05`
- `num_leaves=63`
- `subsample=0.8`
- `colsample_bytree=0.8`
- `random_state=42`

`XGBoost`

- `n_estimators=400`
- `learning_rate=0.05`
- `max_depth=8`
- `subsample=0.8`
- `colsample_bytree=0.8`
- `objective=reg:squarederror`
- `random_state=42`

Raisons du choix:

- tres performants sur tabulaire
- robustes avec des features heterogenes
- faciles a logger et a recharger dans MLflow

### 7.4 Strategie de validation

Le projet utilise un protocole a deux etages:

1. `expanding-window CV` sur le train
2. `holdout` final gele sur 7 jours

Raison:

- le CV mesure la stabilite et la generalisation temporelle
- le holdout final sert de verite terrain pour la promotion

### 7.4.b Semantique exacte des predictions et des actuals

Pour une ligne `zone x heure`:

- `predicted_trips` = sortie du modele champion
- `actual_trips` = valeur reelle `target_trips` issue du holdout
- `absolute_error` = ecart absolu entre les deux

Le replay n'invente donc pas les `actual_trips`.

Le service [`run_replay_cycle.py`](/Users/stefen/tl_demand_forecasting/prediction_service/run_replay_cycle.py):

1. lit une heure du holdout
2. applique le modele champion
3. recopie la vraie cible holdout en `actual_trips`
4. calcule l'erreur
5. ecrit le tout dans PostgreSQL

### 7.5 Metriques

Metriques suivies:

- `MAE`
- `RMSE`
- `MASE`

Raison:

- `MAE` parle bien metier
- `RMSE` sanctionne davantage les grosses erreurs
- `MASE` permet de se comparer a une reference naive

### 7.6 Quality gates

Les quality gates versionnees imposent:

- modeles autorises: `lightgbm`, `xgboost`
- `holdout_mae <= 8.0`
- `holdout_mase <= 1.0`
- `cv_mae_std <= 1.0`
- amelioration holdout vs meilleure baseline `>= 10%`
- au moins 3 baselines
- promotion approuvee
- toutes les gates de promotion a `true`

### 7.7 Etat actuel du modele

Snapshot courant des rapports:

- champion: `lightgbm`
- `holdout_mae = 6.4256`
- `holdout_rmse = 14.3644`
- `holdout_mase = 0.4161`
- gain vs meilleure baseline: `+46.34%`
- version registry approuvee: `4`

Interpretation:

- le systeme bat nettement les baselines
- la performance est suffisante selon les gates en place
- le champion a aussi battu l'ancien champion sur holdout

Sanity-check production au `15 mars 2026`:

- lignes evaluees: `20574`
- fenetre rejouee: `2024-06-24 00:00:00` -> `2024-06-30 23:00:00`
- moyenne `predicted_trips`: `39.27`
- moyenne `actual_trips`: `39.38`
- biais moyen: `-0.11`
- total predit: `807883`
- total reel: `810193`
- correlation `predicted vs actual`: `0.9817`

Lecture de ce sanity-check:

- le modele suit bien la dynamique globale
- il y a une legere sous-prediction
- certaines journees et certaines heures de pointe restent plus dures a bien predire

---

## 8. Promotion et gouvernance modele

### 8.1 Regles de promotion

Un challenger ne devient `champion` que si:

- il bat la meilleure baseline
- il a `holdout_mase < 1`
- il ne regresse pas face au `champion` courant

### 8.2 Etats MLflow utilises

MLflow Registry utilise deux aliases:

- `candidate`
- `champion`

Le flux est:

1. enregistrer le challenger
2. lui donner l'alias `candidate`
3. n'affecter `champion` que si toutes les gates passent

### 8.3 Artefacts de decision

Les artefacts exportes sont:

- [`reports/run_summary.csv`](/Users/stefen/tl_demand_forecasting/reports/run_summary.csv)
- [`reports/best_run.json`](/Users/stefen/tl_demand_forecasting/reports/best_run.json)
- [`reports/promotion_decision.json`](/Users/stefen/tl_demand_forecasting/reports/promotion_decision.json)
- [`reports/quality_gate_report.json`](/Users/stefen/tl_demand_forecasting/reports/quality_gate_report.json)

Ces fichiers forment la trace de decision versionnee.

---

## 9. Conception du replay pseudo-live

### 9.1 Pourquoi le replay existe

La source publique TLC est batch. Il serait trompeur de parler de "temps reel".

Le replay permet de dire quelque chose de vrai:

> le systeme rejoue heure par heure une periode holdout historique, predit avec le modele champion, puis compare aux observations reelles de cette meme heure.

### 9.2 Mecanisme

Le service:

- charge le holdout
- lit l'heure courante dans `replay_state`
- isole toutes les lignes de cette heure
- predit avec `models:/...@champion`
- calcule `absolute_error`
- ecrit le resultat dans `zone_predictions`
- passe a l'heure suivante

### 9.3 Tables Postgres impliquees

`zone_predictions`

- stocke predictions, actuals, erreur, version modele, alias

`replay_state`

- stocke le curseur de progression du replay

### 9.4 Modes d'execution

Deux modes pratiques:

- un cycle unique via le timer systemd
- un backfill complet via `--until-wrap --prune-window`

Raison du prune:

- eviter d'afficher des donnees de replay qui ne correspondent plus a la fenetre holdout courante

---

## 10. Dashboard et observabilite

### 10.1 Dashboard Grafana

Les dashboards provisionnes automatiquement sont:

- `TLC Demand Forecasting`
- `TLC Operations`

Le dashboard metier contient:

- un `geomap`
- une serie temporelle `predicted vs actual`
- une table MAE
- une stat MAE

Le dashboard operations contient:

- la fraicheur du replay
- la taille du dernier batch
- la `MAE 24h`
- la couverture par heure rejouee
- l'etat du curseur `replay_state`

### 10.2 Particularite temporelle

Les requetes du dashboard sont ancrees sur `MAX(target_hour)` disponible dans `zone_predictions`.

Raison:

- le replay porte sur une plage historique
- si le dashboard etait ancre sur `NOW()`, il afficherait `No data`

### 10.2.b Interpretation visuelle du panel principal

Dans `Predicted vs Actual`:

- l'axe du bas represente les heures rejouees
- l'axe de gauche represente le nombre de trajets
- avec `Zone = All`, Grafana peut afficher des valeurs abregees en milliers

Exemples:

- `1.25` sur l'axe vertical signifie environ `1250 trajets`
- `2.75` signifie environ `2750 trajets`

Si les labels de date visibles tombent surtout a `00:00`, cela ne veut pas dire que la prediction est journaliere. Les donnees restent bien horaires; c'est seulement l'etiquetage automatique de l'axe sur une fenetre longue.

### 10.3 Alertes

Alertes provisionnees:

- `TLC Replay Freshness`
- `TLC Replay Coverage`
- `TLC Model MAE 24h`

Seuils actuels:

- fraicheur replay > 90 minutes
- batch recent < 100 lignes
- `MAE 24h > 12`

---

## 11. Infrastructure AWS

### 11.1 Ressources creees

Pour chaque environnement:

- 1 EC2 Ubuntu 24.04 `m6i.large`
- 1 Elastic IP
- 1 bucket S3 chiffre et versionne
- 1 IAM role EC2
- 1 instance profile
- 1 security group
- 1 role IAM OIDC GitHub optionnel

### 11.2 Regles reseau

Expose publiquement:

- `22/tcp` pour SSH depuis le CIDR admin
- `3000/tcp` pour Grafana depuis le CIDR admin

Non exposes publiquement:

- `5000/tcp` MLflow
- `5432/tcp` PostgreSQL

### 11.3 Stockage S3

Buckets / prefixes utilises:

- `raw/`
- `features/`
- `holdout/`
- `mlflow-artifacts/`
- `reports/`

### 11.4 Choix de sizing

Le sizing `m6i.large` a ete retenu car des tailles plus petites de type burstable devenaient lentes avec:

- PostgreSQL
- MLflow
- Grafana
- replay service

sur une seule machine.

---

## 12. Securite et secrets

### 12.1 Principes

- aucun credential AWS long dans GitHub
- MLflow reste en `127.0.0.1`
- Grafana a un mot de passe admin gere par secret
- les fichiers sensibles locaux sont ignores du versioning

### 12.2 Secrets critiques

Secrets utilises:

- `EC2_SSH_PRIVATE_KEY`
- `MLFLOW_DB_PASSWORD`
- `GRAFANA_ADMIN_PASSWORD`

### 12.3 Acces MLflow

MLflow est accessible via tunnel SSH:

```bash
ssh -N -L 5000:127.0.0.1:5000 ubuntu@EC2_IP
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
```

Raison:

- pas d'exposition publique du serveur MLflow
- surface d'attaque reduite

### 12.4 GitHub OIDC

GitHub Actions assume un role IAM AWS via OIDC.

Raison:

- supprimer les AWS access keys statiques dans GitHub
- garder un chemin de deploiement plus propre

---

## 13. CI/CD et environnements

### 13.1 Environnements

Deux environnements logiques:

- `staging`
- `production`

Chaque environnement a:

- son `PROJECT_NAME`
- son `TF_STATE_KEY`
- ses secrets GitHub Environment
- son infra separee

### 13.2 CI

Le workflow `CI` valide:

- la compilation Python
- les tests `pytest`
- les quality gates
- `terraform fmt` et `terraform validate`
- la syntaxe des playbooks Ansible

### 13.3 CD staging

Declenchement:

- `push` vers `main`

Role:

- deployer automatiquement la stack d'integration

### 13.4 CD production

Declenchement:

- tag `prod-v*`
- `workflow_dispatch` garde en break-glass

Role:

- deployer une release immutable de production

### 13.5 Logique de deploy

Le workflow reutilisable:

1. assume le role AWS via OIDC
2. lance Terraform
3. recupere les outputs
4. ouvre temporairement SSH pour l'IP du runner
5. rend l'inventory Ansible
6. applique les playbooks
7. verifie les services
8. referme la fenetre SSH

---

## 14. Phases projet de bout en bout

### Phase A. Provisionner l'infra

Entree:

- variables Terraform

Sortie:

- EC2
- S3
- IAM
- SG
- EIP

### Phase B. Configurer la machine

Entree:

- EC2 fraiche
- secrets applicatifs

Sortie:

- PostgreSQL pret
- MLflow pret
- Grafana pret
- replay timer pret

### Phase C. Ingerer et preparer les donnees

Entree:

- fichiers TLC publics

Sortie:

- `data/raw/*`
- `data/processed/train_features.parquet`
- `data/holdout/holdout_features.parquet`

### Phase D. Entrainer et evaluer

Entree:

- train
- holdout

Sortie:

- runs MLflow
- rapports d'evaluation

### Phase E. Promouvoir

Entree:

- runs MLflow
- champion courant

Sortie:

- alias `candidate`
- alias `champion` eventuellement mis a jour

### Phase F. Rejouer et observer

Entree:

- holdout
- modele champion

Sortie:

- `zone_predictions`
- dashboard Grafana
- alertes exploitation

---

## 15. Critere de succes

Le projet est considere reussi si:

1. l'infra peut etre recreee sans etapes manuelles cachees
2. les donnees TLC peuvent etre re-ingerees
3. le pipeline produit train + holdout valides
4. au moins une baseline et deux challengers sont compares
5. le champion est promu selon des gates explicites
6. le replay alimente automatiquement PostgreSQL
7. Grafana affiche predictions, actuals et erreur
8. la CI et le CD passent sur `staging` et `production`

---

## 16. Limites et risques assumes

### Limites

- pas de vraie source online
- pas d'orchestrateur de jobs externe
- pas de serving HTTP temps reel
- pas de rollback multi-version automatise au-dela des aliases MLflow

### Risques

- la couverture geographique depend de la presence des centroides
- une seule EC2 concentre plusieurs roles applicatifs
- la qualite des predictions depend de la periode TLC choisie

### Positionnement honnete

Ce projet n'est pas une plateforme data entreprise complete.

En revanche, c'est une implementation propre et defendable d'une chaine MLOps compacte, avec des decisions techniques explicites et une vraie observabilite metier.

---

## 17. Etat actuel du repository

Le repository actuel implemente effectivement:

- Terraform en `ca-central-1`
- Ansible pour PostgreSQL, MLflow, Grafana et replay
- quality gates versionnees
- CI GitHub Actions
- CD `staging` et `production`
- OIDC GitHub vers AWS
- rotation du mot de passe Grafana par secret
- dashboard et alertes Grafana provisionnes

Le modele champion documente au moment de cette version du PRD est:

- `lightgbm`
- registry version `4`
- approuve par les quality gates

---

## 18. Appendice: role des notebooks Databricks

Les notebooks dans [`databricks/`](/Users/stefen/tl_demand_forecasting/databricks) sont gardes pour:

- EDA
- prototypage
- demo notebook

Ils ne remplacent pas les scripts de reference du projet et ne doivent pas etre consideres comme la source de verite du pipeline.
