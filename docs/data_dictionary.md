# Dictionnaire de données

Classification : **P** public · **I** interne · **C** confidentiel (voir [`security/README_security.md`](../security/README_security.md)).

## Sources (Bronze)

### S1 — Citi Bike trip archives (CSV mensuels, `landing/citibike/trips/`)
Source : https://s3.amazonaws.com/tripdata (lien depuis citibikenyc.com/system-data) · ~5 M lignes/mois · horodatage **heure locale New York**.

| Champ | Type | Exemple | Description | Classe |
|---|---|---|---|---|
| `ride_id` | string | `2506859557764627159` | identifiant unique de la location | **C** |
| `rideable_type` | string | `classic_bike`, `electric_bike` | type de vélo | I |
| `started_at` / `ended_at` | timestamp | `2026-10-01 17:52:36.306` | début / fin (heure locale NY) | **C** (avec la position) |
| `start_station_name` / `end_station_name` | string | `W 24 St & 7 Ave` | nom de station | P |
| `start_station_id` / `end_station_id` | **string** | `6257.03` | = `short_name` GBFS (à lire en texte, pas en nombre) | P |
| `start_lat`, `start_lng`, `end_lat`, `end_lng` | double | `40.7448` | coordonnées (vides pour certains vélos électriques) | **C** |
| `member_casual` | string | `member` / `casual` | abonné annuel ou occasionnel | I |

### S2 — GBFS Citi Bike (API JSON, temps réel)
`station_information.json` (référentiel, ~2 500 stations) : `station_id` (UUID), `short_name`, `name`, `lat`, `lon`, `capacity`, `region_id` — **P**.
`station_status.json` (TTL ~60 s) : `station_id`, `num_bikes_available`, `num_ebikes_available`, `num_docks_available`,
`num_bikes_disabled`, `num_docks_disabled`, `is_installed`, `is_renting`, `is_returning`, `last_reported` (epoch) — **P**.

### S3 — Open-Meteo Historical Weather API (JSON)
Point 40.7306 N, -73.9866 W, fuseau `America/New_York`, pas horaire : `temperature_2m` (°C), `precipitation` (mm),
`rain` (mm), `wind_speed_10m` (km/h), `relative_humidity_2m` (%), `weather_code` (WMO) — **P**.

## Silver (Delta, nettoyé)

| Table | Grain | Colonnes ajoutées / règles |
|---|---|---|
| `silver_trips` | 1 trajet | `duration_min`, `start_hour`, `end_hour`, `trip_date`, `is_round_trip` ; dédoublonné sur `ride_id` ; rejet : horodatage illisible, station de départ/arrivée manquante, durée < 1 min ou > 24 h, coordonnées hors NYC |
| `silver_stations` | 1 station (`station_id` = short_name) | `gbfs_station_id`, `capacity`, `trip_events_month`, `match_status` ∈ {matched, trips_only, gbfs_only} |
| `silver_weather_hourly` | 1 heure | `weather_hour`, `temperature_c`, `precipitation_mm`, `rain_mm`, `wind_kmh`, `humidity_pct`, `weather_code`, `is_rainy` (≥ 0,2 mm) |

## Gold (Delta, prêt pour BI / ML)

| Table | Grain | Colonnes principales |
|---|---|---|
| `gold_station_hourly` | station × heure (grille complète, 0 si aucun trajet) | `departures`, `arrivals`, `net_flow`, `ebike_departures`, `member_departures`, `hour`, `dow` (1 = dimanche), `is_weekend`, météo |
| `gold_daily_kpis` | jour | `trips`, `avg_duration_min`, `member_share_pct`, `ebike_share_pct`, `active_stations`, `avg_temp_c`, `total_precip_mm` |
| `gold_station_profile` | station | `departures`, `arrivals`, `net_flow`, `avg_daily_departures`, `peak_departure_hour`, `am_peak_net_flow_per_day`, `am_flow_pct_capacity`, `station_role`, `am_peak_risk` |
| `gold_hourly_pattern` | jour de semaine × heure | `trips`, `member_trips`, `n_days`, `avg_trips_per_hour` |
| `gold_weather_effect` | pluie × température × moment × week-end | `hours`, `avg_trips_per_hour` |
| `gold_demand_forecast` | station × heure (période de test) | réel, `pred_*`, `baseline_*`, `pred_net_flow` |
| `gold_ml_metrics` | modèle | MAE / RMSE modèle et baseline, gain %, `run_id`, `model_uri` |
| `gold_station_priority` | station (snapshot) | `num_bikes_available`, `pred_departures`, `pred_arrivals`, `projected_bikes`, `risk`, `action`, `bikes_to_move`, `priority_score`, `priority_rank` |
| `gold_data_quality` | contrôle | `check`, `rows`, `pct_of_raw`, `table`, `checked_at` |

## Temps réel (Eventhouse `kqldb_smartbike`)
`StationStatus` : un événement par station et par changement (+ snapshot complet toutes les 15 min) — schéma dans
[`kql/01_create_tables.kql`](../kql/01_create_tables.kql). Vue matérialisée `LatestStationStatus` = dernier état par station.
Rétention 30 jours.
