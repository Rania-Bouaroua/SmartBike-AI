# Résultats (exécution locale sur les données réelles)

Période : **01/08/2026 → 31/10/2026** (92 jours). Généré par `python docs/make_results.py` à partir des tables Gold.

## 1. Volumétrie et qualité des données (notebook 01)

* Trajets bruts : **15 317 456** · valides après contrôles : **15 264 865** (99.66%).
* Réconciliation des identifiants : **99.15 %** des départs/arrivées rattachés à une station GBFS actuelle.
* Grille Gold station × heure : **5 495 712** lignes · 2489 stations actives.

| Contrôle | Lignes | % du brut |
|---|---:|---:|
| valid | 15 264 865 | 99.657 |
| missing_end_station | 42 394 | 0.277 |
| missing_start_station | 8 662 | 0.057 |
| started_outside_loaded_months | 1 454 | 0.009 |
| duration_gt_24h | 81 | 0.001 |
| duplicate_ride_id | 0 | 0.000 |
| station_matched (stations) | 2 460 | 99.15 % des trajets |
| station_gbfs_only (stations) | 59 | 0.00 % des trajets |
| station_trips_only (stations) | 109 | 0.85 % des trajets |

## 2. Usage (notebook 02)

* **15 264 865** trajets, soit **165 922** par jour en moyenne ; jour record : 211 757 le 18/09/2026.
* Jours ouvrés : 173 126 trajets/jour · week-end : 148 581 (-14.2%).
* Abonnés : **80.7 %** des trajets ; vélos électriques : **73.0 %** ; durée moyenne 13.1 min.
* Heures de pointe en semaine : **17 h** (17 700 trajets/h) — pic du matin à 8 h (12 948 trajets/h).

![](figures/fig1_daily_trips.png)
![](figures/fig2_heatmap_day_hour.png)

## 3. Stations

Sur un mois entier, départs ≈ arrivées presque partout (rééquilibrage par camions, allers-retours) : le déséquilibre est **intrajournalier**. Rôle calculé sur le pic du matin (7h-10h, jours ouvrés) :

| Rôle le matin | Stations |
|---|---:|
| equilibree | 1803 |
| source le matin (se vide) | 422 |
| puits le matin (se remplit) | 264 |

**179** stations perdent en moyenne plus de 30 % de leur capacité chaque matin de semaine (se videraient sans rééquilibrage) et **124** en gagnent plus de 30 % (saturation).

![](figures/fig3_top_stations.png)

## 4. Effet de la météo

Jours ouvrés 7h-19h : **10 875** trajets/h par temps sec contre **9 053** sous la pluie (**-17%**), sur 83 heures pluvieuses seulement — à confirmer sur une période plus longue.

![](figures/fig4_weather_effect.png)

## 5. Machine Learning (notebook 03)

Découpage chronologique : les 7 derniers jours servent de test. Baseline = moyenne historique de la station pour la même heure et le même type de jour.

| Cible | MAE baseline | MAE modèle | RMSE baseline | RMSE modèle | Erreur réseau/heure (WAPE) baseline → modèle |
|---|---:|---:|---:|---:|---:|
| arrivals | 1.376 | 1.312 | 2.853 | 2.779 | 10.3 % → **7.6 %** |
| departures | 1.339 | 1.296 | 2.801 | 2.743 | 10.3 % → **7.3 %** |

Lecture : au niveau d'une station et d'une heure (≈ 1-2 trajets en moyenne), l'aléa domine et le modèle ne gagne que 3.2 % de MAE. Sommé sur le réseau, où la météo et la tendance saisonnière jouent pleinement, l'erreur horaire baisse nettement.

![](figures/fig5_ml_network_test_week.png)

## 6. Priorités d'intervention (notebook 04, état live GBFS)

Snapshot : **17/11/2026 14:00** (heure de New York), horizon 2 h.

| État | Stations |
|---|---:|
| vide maintenant | 259 |
| risque vide | 236 |
| saturee maintenant | 197 |
| risque saturee | 36 |
| ok | 1742 |

Vélos à déplacer pour ramener les stations à risque à 50 % : **9 781**.

| Rang | Station | Vélos dispo / capacité | Départs prévus | Arrivées prévues | État | Action |
|---:|---|---:|---:|---:|---|---|
| 1 | Broadway & W 25 St | 105 / 109 | 69.9 | 62.2 | saturee maintenant | retirer des velos (-43) |
| 2 | W 21 St & 6 Ave | 63 / 74 | 82.0 | 82.1 | saturee maintenant | retirer des velos (-26) |
| 3 | Park Ave & E 42 St | 75 / 83 | 51.3 | 50.6 | saturee maintenant | retirer des velos (-32) |
| 4 | 8 Ave & W 31 St | 85 / 97 | 33.4 | 49.0 | risque saturee | retirer des velos (-49) |
| 5 | Broadway & E 19 St | 92 / 99 | 38.5 | 34.9 | saturee maintenant | retirer des velos (-38) |
| 6 | W 24 St & 7 Ave | 74 / 79 | 41.8 | 50.0 | risque saturee | retirer des velos (-39) |
| 7 | E 10 St & Ave A | 0 / 107 | 24.3 | 28.3 | vide maintenant | ajouter des velos (+50) |
| 8 | W Broadway & Spring St | 98 / 99 | 25.0 | 24.2 | saturee maintenant | retirer des velos (-47) |
| 9 | Forsyth St & Grand St | 90 / 93 | 32.0 | 34.4 | risque saturee | retirer des velos (-46) |
| 10 | University Pl & E 8 St | 74 / 79 | 32.8 | 31.5 | saturee maintenant | retirer des velos (-33) |

![](figures/fig6_priority_states.png)

## 7. Agrégations avancées (notebook 05)

* **Rééquilibrage** : en moyenne **1 380** vélos par jour doivent être déplacés par camion (jours ouvrés 1 572, week-end 919) ; **1.6 %** des stations en ont besoin un jour donné. Méthode : somme cumulée heure par heure de (arrivées − départs) depuis minuit ; si l'amplitude de ce stock dépasse la capacité, aucun niveau de remplissage du matin n'évite la rupture.
* Stations demandant le plus souvent un camion : Throop Ave & Myrtle Ave (75 j), 5 Ave & 37 St (72 j), W 50 St & 9 Ave (65 j), Dock 72 Way & Market St (63 j), North Moore St & Greenwich St (62 j).
* **Flux origine → destination** : 171 864 liaisons ≥ 20 trajets ; les 100 premières concentrent 1.1% de ces trajets. Liaison n°1 : 55 Ave & Center Blvd → Vernon Blvd & 50 Ave (3 058 trajets, 3 min médianes).
* **Volatilité** : le décile le plus volatil des stations a un coefficient de variation ≥ 2.11 (demande horaire très irrégulière, plus difficile à prévoir).
* **Tendance hebdomadaire** : de 166 461 à 163 164 trajets/jour entre la 2e et l'avant-dernière semaine complète.

![](figures/fig7_rebalancing_daily.png)
![](figures/fig8_top_od_flows.png)
![](figures/fig9_usage_cube.png)

## 8. MLOps : promotion du modèle

| Modèle | Version candidate | MAE candidate | Champion avant | MAE champion | Baseline | Promu |
|---|---:|---:|---:|---:|---:|---|
| smartbike_demand_model_departures | 2 | 1.2956 | 1 | 1.2976 | 1.3388 | oui |
| smartbike_demand_model_arrivals | 2 | 1.3119 | 1 | 1.3097 | 1.3755 | non |

## 9. Tournées des camions de rééquilibrage (OR-Tools)

Flotte : 3 camions de 30 vélos, tournée de 240 min, 80 stations candidates (les plus prioritaires), camions partis vides, 15 km/h, 4 min par arrêt + 15 s par vélo. Problème : collecte et livraison avec capacité et prix (une station non visitée « coûte » sa priorité), résolu par OR-Tools (recherche locale guidée, 15 s), comparé à la pratique « suivre la liste de priorité dans l'ordre ».

| Méthode | Arrêts | Vélos déplacés | Urgence couverte | Distance | Vélos / km |
|---|---:|---:|---:|---:|---:|
| greedy (liste de priorité) | 27 | 621 | 38% | 109 km | 5.7 |
| optimisé (OR-Tools) | 40 | 989 | 60% | 74 km | 13.4 |

Sur ce relevé, l'optimisation déplace **+59% de vélos** et couvre **60%** de l'urgence (contre 38%) en roulant 74 km au lieu de 109 km. Le gain vient de l'ordre des arrêts : enchaîner une station pleine puis une station vide voisine, au lieu de traverser la ville au rythme du classement.

## 10. Fiabilité des alertes : backtest sur l'historique réel

Vérité terrain : flux GBFS enregistré toutes les 5 min (`scripts/gbfs_history.py`). À chaque instant de départ, l'alerte est recalculée comme en production (état des stations, météo, modèles champion), puis comparée à ce qui s'est réellement passé dans les **1 h** suivantes. Stations déjà en rupture exclues ; seuil de probabilité réglé sur la première moitié des instants, évalué sur la seconde. Instants de test : 1 ; fenêtres station × horizon évaluées : 2 246.

| Alerte | Méthode | Alertes | Ruptures réelles | Précision | Rappel | F1 | PR-AUC |
|---|---|---:|---:|---:|---:|---:|---:|
| vide | stock déjà bas (sans prévision) | 422 | 151 | 32% | 91% | 0.48 | 0.47 |
| vide | prévision ponctuelle (ancienne règle) | 373 | 151 | 34% | 85% | 0.49 | 0.42 |
| vide | probabiliste (Monte Carlo) | 341 | 151 | 39% | 88% | 0.54 | 0.55 |
| pleine | stock déjà bas (sans prévision) | 354 | 136 | 36% | 93% | 0.51 | 0.58 |
| pleine | prévision ponctuelle (ancienne règle) | 103 | 136 | 50% | 38% | 0.43 | 0.41 |
| pleine | probabiliste (Monte Carlo) | 181 | 136 | 48% | 64% | 0.55 | 0.48 |

Lecture : la précision dit si l'opérateur peut faire confiance à une alerte, le rappel combien de ruptures réelles ont été vues à temps ; la PR-AUC mesure la qualité du classement des stations (hasard = prévalence, ici 6.9%).

![](figures/fig10_alert_backtest.png)

> Historique encore court au moment de la génération : ces chiffres sont à reconfirmer en relançant `scripts/backtest_alerts.py` après plusieurs jours d'enregistrement (le script, les tables et ce rapport se mettent à jour automatiquement).
