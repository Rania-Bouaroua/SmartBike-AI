# Rapport Power BI — `rpt_smartbike`

Le fichier .pbix se construit dans Power BI (Service Fabric ou Desktop) à partir des tables Gold. Ce guide donne
le modèle, les mesures DAX ([`measures.dax`](measures.dax)) et la conception de chaque page.
**Chaque page répond à une question explicite** (exigence F du cahier des charges).

## 1. Connexion et modèle sémantique

* Dans le Lakehouse `lh_smartbike` → **New semantic model** `sm_smartbike` (mode **Direct Lake** : lit les fichiers Delta
  de OneLake sans import ni rafraîchissement planifié).
* Alternative hors Fabric (Power BI Desktop) : *Get data → Folder* sur `data/lakehouse/Tables/<table>/` puis *Combine & Transform* (fichiers Parquet produits localement), ou exécuter `python docs/export_for_powerbi.py` qui crée un fichier Parquet unique par table dans `data/powerbi/`.

| Table | Rôle | Relation |
|---|---|---|
| `gold_daily_kpis` | dimension date + faits journaliers | `date` 1 → * `gold_station_hourly[date]` |
| `gold_station_profile` | dimension station | `station_id` 1 → * `gold_station_hourly`, `gold_demand_forecast`, `gold_station_priority` |
| `gold_station_hourly` | faits station × heure | — |
| `gold_hourly_pattern` | heatmap jour × heure | autonome |
| `gold_weather_effect` | effet météo | autonome |
| `gold_station_priority` | interventions (notebook 04) | via `station_id` |
| `gold_demand_forecast`, `gold_ml_metrics` | prévisions & métriques ML | via `station_id` |
| `gold_data_quality` | contrôles qualité | autonome |
| `gold_station_daily` | faits station × jour (moyenne glissante 7 j, évolutions J-1 / S-1) | `station_id`, `date` |
| `gold_rebalancing_need` / `gold_rebalancing_daily` | besoin de rééquilibrage (notebook 05) | `station_id`, `date` |
| `gold_od_flows` | matrice origine → destination | `start_station_id` → station |
| `gold_station_volatility` | percentiles / volatilité de la demande | `station_id` 1 → 1 |
| `gold_usage_cube` | cube OLAP (sous-totaux `TOTAL`) | autonome |
| `gold_zone_hourly`, `gold_weekly_trend`, `gold_duration_distribution` | zones ~1 km, tendance hebdo, durées | autonomes |
| `gold_model_promotion_log` | journal champion / challenger (MLOps) | autonome |

Catégoriser `lat` / `lon` en *Latitude* / *Longitude* ; `station_name` en *Place*.

## 2. Pages

### Page 1 — « Vue d'ensemble » · *Quel est le niveau d'usage et comment évolue-t-il ?*
* **KPI (cartes)** : `Total trajets`, `Trajets / jour (moy.)`, `% abonnés`, `% vélos électriques`, `Durée moyenne`.
* **Courbe** : trajets par jour (axe 1) + température moyenne (axe 2).
* **Histogramme** : semaine vs week-end (`Écart week-end %`).
* **Slicers** : plage de dates, `is_weekend`.

### Page 2 — « Quand ? » · *Quelles heures concentrent le plus de trajets ?*
* **Matrice-heatmap** `gold_hourly_pattern` : lignes = `day_name` (trié par `dow`), colonnes = `hour`, valeur = `avg_trips_per_hour`, mise en forme conditionnelle (dégradé).
* Interprétation attendue : double pic 8 h et 17-18 h en semaine (trajets domicile-travail), pic unique l'après-midi le week-end.

### Page 3 — « Où ? » · *Quelles stations concentrent le plus de trajets, lesquelles se vident le matin ?*
* **Carte** (Azure Maps) : bulles `gold_station_profile`, taille = `departures`, couleur = `station_role`.
* **Barres** : Top 15 stations par départs.
* **Tableau** : stations `am_peak_risk = "risque vide"` avec `am_flow_pct_capacity`.
* **Slicers** : `station_role`, `am_peak_risk`.

### Page 4 — « Météo » · *La météo et le jour de la semaine modifient-ils les volumes ?*
* **Histogramme groupé** `gold_weather_effect` : `avg_trips_per_hour` par `rain_bucket`, légende `temp_bucket`, filtre `daypart = "jour 7h-19h"`.
* **Carte KPI** : `Baisse due à la pluie %`.
* **Nuage de points** : trajets/jour vs `total_precip_mm` (`gold_daily_kpis`).
* Note de limite : peu d'heures pluvieuses sur 3 mois → afficher le nombre d'heures (`hours`) dans l'info-bulle.

### Page 5 — « Interventions prioritaires » · *Quelles stations sont/vont être vides ou saturées, où intervenir d'abord ?*
* **Cartes** : `Stations vides maintenant`, `Stations saturées maintenant`, `Stations à risque (2 h)`, `Vélos à déplacer`.
* **Carte** : couleur = `risk` (rouge vide, violet saturée, gris ok).
* **Tableau trié par `priority_rank`** : station, vélos dispo, capacité, départs/arrivées prévus, `risk`, `action`, `bikes_to_move`.
* **Slicer** : `risk`, `action`. Horodatage du snapshot affiché (`snapshot_time_local`).

### Page 6 — « Fiabilité » · *Peut-on faire confiance aux données et au modèle ?*
* **Tableau** `gold_data_quality` (lignes rejetées par motif, % réconciliation des stations).
* **Barres** MAE modèle vs baseline (`gold_ml_metrics`), **courbe** réel vs prévu pour la station choisie (slicer station, `gold_demand_forecast`).

### Page 7 — « Rééquilibrage » · *Combien de vélos les camions doivent-ils déplacer, où et quand ?*
* **KPI** : `Déplacements forcés / jour`, `% stations nécessitant un camion`.
* **Courbe** : `gold_rebalancing_daily[forced_moves]` par jour (slicer semaine / week-end).
* **Tableau** : stations triées par nombre de jours avec camion, heure habituelle du stock minimum (`hour_of_min_stock`).
* Interprétation : le stock cumulé (somme glissante arrivées − départs depuis minuit) dépasse la capacité → aucune
  configuration du matin n'évite la rupture, il faut une intervention en cours de journée.

### Page 8 — « Flux » · *Quelles liaisons concentrent les trajets, à quelle vitesse ?*
* **Carte** (Azure Maps, couche *Path* ou visuel *Flow Map*) : `gold_od_flows` top 100.
* **Barres** : top 15 liaisons ; **nuage** `avg_dist_km` × `median_duration_min` (taille = `trips`).
* **Matrice** `gold_usage_cube` filtrée sur `grouping_level = 0` + ligne de totaux (`TOTAL`) : qui roule quand, sur quel vélo.

### Page 9 — « Tournée camion » · *Quels camions vont où, avec combien de vélos ?*
* **Carte** (Azure Maps, couche *Path*) : `gold_truck_routes` filtrée sur `method = "optimisé (OR-Tools)"`, une couleur
  par `truck`, ordre = `stop` ; info-bulle : station, `delta` (+ livrer / − retirer), `load_after`.
* **Cartes KPI** depuis `gold_truck_plan_kpis` : `[Vélos déplacés (optimisé)]`, `[Gain vs liste de priorité %]`,
  `[Urgence couverte]`, `[Km (optimisé)]`.
* **Tableau** feuille de route : truck, stop, station, action, vélos, `minutes_cum`.

### Page 10 — « Fiabilité des alertes » · *Quand le système annonce une rupture, a-t-il raison ?*
* **Barres groupées** `gold_alert_backtest` : précision / rappel / PR-AUC par `method`, filtre `event` (vide / pleine).
* **Courbe** `gold_alert_calibration` : `predicted` (x) vs `observed` (y), une série par `event`, + diagonale.
* **Texte** : horizon, nombre d'instants de test, prévalence (une alerte « au hasard » aurait une précision égale à
  la prévalence).

Thème : importer [`smartbike_theme.json`](smartbike_theme.json) (*Affichage → Thèmes → Parcourir*) pour des couleurs cohérentes
avec les figures du rapport et l'application Streamlit (rouge = vide, bleu foncé = saturée, vert = ok).

## 3. Tableau de bord temps réel (Fabric Real-Time Dashboard `rtd_smartbike_live`)
Tuiles = requêtes [`kql/02_queries.kql`](../kql/02_queries.kql) : Q1 (répartition des états), Q1b (liste), Q2 (série 15 min),
Q3 (stations qui se vident le plus vite), Q6 (qualité). Rafraîchissement automatique 30 s.
Le rapport Power BI (historique + priorités) et le dashboard RTD (live) sont **complémentaires** : liens croisés via un bouton.
