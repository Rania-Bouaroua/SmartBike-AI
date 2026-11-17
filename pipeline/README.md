# Pipeline batch `pl_smartbike_batch`

[`pl_smartbike_batch.json`](pl_smartbike_batch.json) décrit le pipeline au format JSON Data Factory/Fabric. C'est un **modèle de
référence** : les identifiants de connexions, notebooks et espace de travail sont propres à chaque tenant, donc on recrée le
pipeline dans l'éditeur Fabric en suivant ce fichier (≈ 10 min), puis on peut exporter la version réelle (*View → Edit JSON code*)
et la remplacer ici.

| Étape | Activité | Source → destination | Remarque |
|---|---|---|---|
| 1 | `Copy_Trips_Zip_to_Landing` | HTTP `s3.amazonaws.com/tripdata/<month>-citibike-tripdata.zip` → ADLS `landing/citibike/trips/` | décompression **ZipDeflate** à la volée, 2 reprises |
| 2 | `Copy_Weather_API_to_Landing` | Open-Meteo archive (JSON) → `landing/weather/` | paramètres `weather_start/end` |
| 3 | `Copy_GBFS_StationInfo_to_Landing` | GBFS `station_information.json` → `landing/gbfs/` | référentiel stations horodaté |
| 4 | `NB_01_Bronze_to_Silver` | après 1-3 réussies | Spark |
| 5 | `NB_02_Silver_to_Gold` | | Spark |
| 5b | `NB_05_Advanced_Aggregations` | après 5 (en parallèle de 6) | Spark : fenêtres, CUBE, O-D, rééquilibrage |
| 6 | `If_Retrain_Model` → `NB_03` | si `retrain_model = true` | MLflow |
| ✗ | `Notify_On_Failure` | si l'étape 5 échoue | e-mail/Teams |

**Déclencheur** : mensuel, le 8 à 06:00 (Citi Bike publie le mois précédent en début de mois).
**Répétabilité** : le paramètre `month` permet de rejouer n'importe quel mois ; les notebooks écrivent en `overwrite`
(idempotents : relancer donne le même résultat).
**Pipeline 2 (optionnel)** `pl_smartbike_priority` : `NB_04_Station_Priority` toutes les 30 min de 7 h à 21 h (heure de NY).
