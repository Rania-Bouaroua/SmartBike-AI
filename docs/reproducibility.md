# Reproduire SmartBike AI de A à Z

Durée : ~2 h la première fois. Ordre identique à la démo.

## 0. Prérequis
* Compte Microsoft Entra de l'école + abonnement Azure (Azure for Students suffit) + **Fabric** (essai gratuit 60 jours : *app.fabric.microsoft.com → Account manager → Start trial*).
* Poste : Python 3.11+, Git, Azure CLI (`az login`).
```bash
git clone <repo> && cd SmartBike-AI
pip install -r requirements.txt
```

## 1. Local (sans cloud) — vérifier que tout fonctionne
```bash
python scripts/01_download_trips.py --months 202608 202609 202610   # ~3 Go de CSV
python scripts/02_download_weather.py --start 2026-08-01 --end 2026-10-31
python scripts/03_gbfs_snapshot.py
python notebooks/01_bronze_to_silver.py      # Spark local, ~10 min
python notebooks/02_silver_to_gold.py
python notebooks/03_ml_demand_forecast.py    # MLflow local -> mlruns.db
python notebooks/04_station_priority.py
python notebooks/05_advanced_aggregations.py   # fenêtres, CUBE, O-D, rééquilibrage
python scripts/alert_monitor.py --live         # alertes qualité / opérationnelles / modèle
python docs/make_results.py                     # docs/results.md + figures
python scripts/gbfs_collector.py --dry-run --iterations 3 --only-changes
pytest -q tests
```
Les tables sont écrites dans `data/lakehouse/Tables/<table>/` (un dossier de fichiers Parquet par table, ouvrable dans Power BI Desktop).
Voir les expériences : `mlflow ui --backend-store-uri sqlite:///mlruns.db`.

## 2. Ressources Azure (stockage opérationnel, secrets, gouvernance)
```powershell
./infra/deploy.ps1 -Suffix <4 lettres> -EngineerUpn <ingénieur> -AnalystUpn <analyste> -BudgetEmail <email>
```
Crée : `rg-smartbike-dev` (tags), 3 Azure Policies, ADLS Gen2 `stsmartbikedev<suffix>` (conteneur `landing`, cycle de vie),
Key Vault `kv-smartbike-dev-<suffix>`, rôles RBAC des 3 personas, Log Analytics + diagnostics, budget.

## 3. Fabric — espace de travail et Lakehouse
1. Créer l'espace **`ws-smartbike-dev`** sur la capacité d'essai. *Manage access* : Admin = toi, Contributor = Data Engineer, **Viewer = Analyst**.
2. Créer le Lakehouse **`lh_smartbike`**.
3. `Files` → **New shortcut → Azure Data Lake Storage Gen2** → URL `https://stsmartbikedev<suffix>.dfs.core.windows.net`,
   authentification *Organizational account* → choisir `landing` → nommer le raccourci **`bronze`**.
   (Variante simple sans Azure : *Upload folder* de `data/raw/*` vers `Files/bronze/`.)

## 4. Ingestion batch — pipeline `pl_smartbike_batch`
1. Importer les 5 notebooks : *Workspace → Import → Notebook* → `notebooks/fabric/*.ipynb` ; dans chacun, *Add lakehouse* → `lh_smartbike` (défaut).
2. Créer le pipeline `pl_smartbike_batch` en suivant [`pipeline/pl_smartbike_batch.json`](../pipeline/pl_smartbike_batch.json) :
   3 *Copy data* (HTTP → ADLS, la copie Citi Bike avec compression **ZipDeflate**) puis *Notebook* 01 → 02 → (05 ∥ If 03), chemin d'échec → e-mail.
   Connexions : `conn_http_citibike_tripdata` (Anonymous, `https://s3.amazonaws.com/tripdata/`), `conn_http_open_meteo_archive`
   (`https://archive-api.open-meteo.com/`), `conn_http_gbfs_citibike` (`https://gbfs.citibikenyc.com/`), `conn_adls_stsmartbikedev` (OAuth).
3. Paramètre `month` = `202608`, *Run* ; puis `202609`, `202610`. Planification : mensuelle, le 8 à 06:00.
4. Capture pour le rapport : vue *Monitor* du run réussi + tables `silver_*`/`gold_*` dans le Lakehouse.

## 5. Temps réel — Eventstream → Eventhouse → KQL
1. Créer l'**Eventhouse** `eh_smartbike` (crée la base KQL `kqldb_smartbike`) ; exécuter [`kql/01_create_tables.kql`](../kql/01_create_tables.kql).
2. Créer l'**Eventstream** `es_smartbike_status` → *Add source* → **Custom endpoint** → copier la *connection string* (onglet *Keys*, Event Hub).
3. La stocker : `az keyvault secret set --vault-name kv-smartbike-dev-<suffix> -n eventstream-conn --value "<...>"`.
   Si la chaîne ne contient pas `EntityPath=`, définir aussi la variable `EVENTSTREAM_NAME` (nom de l'event hub affiché).
4. *Add destination* → **Eventhouse** → `kqldb_smartbike` / table `StationStatus`, format JSON, mapping `StationStatusMapping` → *Publish*.
5. Lancer le collecteur (laisser tourner pendant toute la période de démo) :
   ```bash
   python scripts/gbfs_collector.py --keyvault kv-smartbike-dev-<suffix> --only-changes
   ```
6. Exécuter les requêtes de [`kql/02_queries.kql`](../kql/02_queries.kql) dans un *KQL Queryset* ; épingler Q1, Q2, Q3 sur un
   **Real-Time Dashboard** `rtd_smartbike_live` (auto-refresh 30 s).
7. (Bonus) Exécuter la section A de [`kql/03_alerts.kql`](../kql/03_alerts.kql) (vue matérialisée horaire + fonctions), puis
   **Activator** : *Set alert* sur ALERT-1 (station très utilisée vide ≥ 10 min → Teams), ALERT-2 (> 8 % du réseau vide/saturé),
   ALERT-3 (collecteur arrêté depuis 5 min → e-mail au Data Engineer).

## 6. Machine Learning
Exécuter `nb_03_ml_demand_forecast` : MLflow est intégré à Fabric → l'expérience `smartbike-demand-forecast` et les modèles
`smartbike_demand_model_departures` / `_arrivals` apparaissent dans l'espace. Puis `nb_04_station_priority`
(planifiable toutes les 30 min via un 2ᵉ pipeline `pl_smartbike_priority`).
Le notebook 03 se termine par une **promotion champion / challenger** : la nouvelle version ne reçoit l'alias `champion`
(utilisé par le notebook 04 et l'API) que si sa MAE bat celle du champion actuel et la baseline (`gold_model_promotion_log`).

## 7. Power BI
Suivre [`powerbi/README_powerbi.md`](../powerbi/README_powerbi.md) (modèle Direct Lake + [`measures.dax`](../powerbi/measures.dax)).

## 8. Application (bonus) — API + application opérateur
```bash
uvicorn app.api:app --port 8000          # API REST, documentation interactive : http://localhost:8000/docs
streamlit run app/dashboard.py           # application opérateur : http://localhost:8501
```
Avec `SMARTBIKE_API_KEY` défini, l'API exige l'en-tête `X-API-Key` (en Azure : paramètre d'App Service = référence Key Vault).
Déploiement possible : Azure App Service (Linux, B1) ou Azure Container Apps ; identité managée avec *Key Vault Secrets User*.

## 9. Nettoyage après la soutenance
```powershell
./infra/cleanup.ps1 -Suffix <suffix>
```
+ arrêter le collecteur, supprimer l'Eventstream, mettre en pause / laisser expirer la capacité Fabric. Vérifier :
`az resource list --tag project=smartbike-ai -o table` → vide.
