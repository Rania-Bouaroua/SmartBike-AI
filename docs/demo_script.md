# Script de démonstration (12 min) — SmartBike AI

> Avant la soutenance (J-1) : lancer le collecteur au moins **24 h avant** (historique KQL), exécuter le pipeline,
> vérifier le rapport, enregistrer une **vidéo de secours** de chaque étape (exigence « fallback »).
> Répartir la parole : chaque membre présente la partie qu'il a implémentée (notation individuelle).

| # | Min | Qui | Écran | Ce qu'on dit / montre |
|---|---|---|---|---|
| 0 | 0:00 | Membre 1 | `docs/architecture.png` | Problème quotidien (station vide / pleine), 3 publics, chaîne Collect → … → Govern. |
| 1 | 1:30 | Membre 1 | Lakehouse `Files/bronze` + un CSV + GBFS JSON | **Données sources** : 3 sources hétérogènes (CSV archive, API JSON live, API météo). Piège : `start_station_id` = `short_name` GBFS, doit être lu en texte. |
| 2 | 3:00 | Membre 2 | Pipeline `pl_smartbike_batch` → *Run* (paramètre `month`) puis Monitor | **Ingestion batch** répétable, ZipDeflate à la volée, planification mensuelle, chemin d'échec. (Montrer un run déjà terminé si le run dure trop.) |
| 3 | 4:30 | Membre 2 | `nb_01` / `nb_02` / `nb_05` exécutés, table `gold_data_quality`, aperçu `gold_station_hourly`, `gold_usage_cube` | **Spark** : 15,9 M trajets → contrôles qualité (rejets motivés), 99,7 % de réconciliation des stations, grille station×heure en Delta. |
| 4 | 6:00 | Membre 3 | Terminal collecteur + Eventstream (graphe des événements entrants) + KQL Queryset | **Temps réel** : événements qui arrivent ; exécuter **Q1** (stations vides maintenant) et **Q2** (fenêtres de 15 min) ; Q3 (stations qui se vident le plus vite) ; Q9 (fenêtre glissante 30 min) ; montrer une alerte Activator. |
| 5 | 8:00 | Membre 4 | Rapport Power BI | **Question 1** : « Quelles stations et quels horaires concentrent le plus de trajets ? » → pages Quand/Où. **Question 2** : « Où intervenir maintenant ? » → page Interventions prioritaires (filtre `risk`). Bonus : effet de la pluie. |
| 6 | 10:00 | Membre 3 | MLflow (expérience, métriques, alias `champion`) | Modèle vs baseline : MAE/RMSE, découpage chronologique, limites ; **promotion champion/challenger** (`gold_model_promotion_log`). |
| 6b | 10:30 | Membre 4 | Application Streamlit (`streamlit run app/dashboard.py`) + `http://localhost:8000/docs` | **Bonus** : carte des stations à risque, onglet *Rééquilibrage* (somme cumulée → vélos à déplacer par camion), prévision « et si » (curseur pluie), appel API `/v1/predict`. |
| 7 | 10:45 | Membre 1 | Compte Analyst : `az storage blob list ... --auth-mode login` → **403** ; Azure Policy qui refuse une région hors UE | **Contrôle de sécurité démontré** + 1 compromis d'architecture : *raccourci OneLake vers ADLS (zéro copie, données restent dans notre abonnement) vs copie dans OneLake (plus simple, double stockage)*. |
| 8 | 11:30 | Tous | `docs/cost_and_inventory.md` | Limites (demande perdue non observable, 3 mois, météo d'un seul point), coût (essai Fabric, Eventstream = principal poste), nettoyage `cleanup.ps1`, améliorations. |

## Questions probables du jury et éléments de réponse
* **Pourquoi Eventhouse et pas seulement le Lakehouse ?** Requêtes temporelles sur des données qui arrivent chaque minute (ingestion en continu, `bin()`, `arg_max`, détection d'anomalies natives) ; le Lakehouse/Delta est optimisé pour le batch.
* **Pourquoi ADLS + raccourci plutôt qu'un upload direct ?** Stockage opérationnel sous notre contrôle (RBAC, Policy, cycle de vie, logs), indépendant de la capacité Fabric ; zéro duplication.
* **Pourquoi pas Azure SQL / Cosmos DB ?** Données en fichiers (CSV) et documents JSON volumineux, accès analytique par scans → data lake ; pas de transactions ni de lectures par clé à faible latence. Cosmos DB aurait du sens pour servir l'état live à une appli mobile.
* **Pourquoi pas de lags dans le modèle ?** Les archives de trajets sont publiées mensuellement : un lag « trajets de l'heure précédente » ne serait pas disponible en production.
* **Le modèle mesure-t-il la demande réelle ?** Non : seulement les locations réalisées ; une station vide ne génère pas de trajet (demande censurée) → prévisions prudentes, documenté.
* **Si la latence doit passer sous la minute ?** Déplacer le score dans KQL (fonction + modèle ONNX) et déclencher Activator.
* **Comment calculez-vous le besoin de rééquilibrage ?** Somme cumulée (fenêtre `unboundedPreceding → currentRow`) de arrivées − départs heure par heure depuis minuit ; l'amplitude max − min de ce stock est le nombre de places nécessaires ; au-delà de la capacité, un camion doit intervenir (`gold_rebalancing_need`).
* **À quoi sert le CUBE ?** Une seule passe Spark calcule tous les sous-totaux (type de jour × moment × usager × vélo) ; `grouping_id` indique le niveau d'agrégation, Power BI filtre dessus.
* **Comment éviter de mettre en production un modèle moins bon ?** Alias MLflow `champion` attribué seulement si la MAE bat celle du champion et la baseline ; sinon la version reste `challenger` avec le tag `validation_status = rejected`, et l'alerte ML-1 se déclenche.
