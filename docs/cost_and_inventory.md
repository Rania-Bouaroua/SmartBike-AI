# Inventaire des ressources, coûts et nettoyage

## Convention de nommage
`<type>-smartbike-<env>[-suffixe]` — préfixes : `rg` groupe de ressources, `st` stockage, `kv` Key Vault, `log` Log Analytics,
`ws` espace Fabric, `lh` Lakehouse, `pl` pipeline, `nb` notebook, `es` Eventstream, `eh` Eventhouse, `kqldb` base KQL,
`rtd` Real-Time dashboard, `sm` modèle sémantique, `rpt` rapport. Tags obligatoires : `project`, `env`, `owner`, `course`, `cost-center`.

## Inventaire (à compléter avec l'état réel le jour de la soutenance)

| Ressource | Nom | Service / SKU | Région | Rôle | État après démo |
|---|---|---|---|---|---|
| Groupe de ressources | `rg-smartbike-dev` | — | France Central | conteneur, tags | supprimé |
| Stockage ADLS Gen2 | `stsmartbikedev<sfx>` | StorageV2 Standard LRS, HNS | France Central | zone d'atterrissage (Bronze) | supprimé |
| Key Vault | `kv-smartbike-dev-<sfx>` | Standard, RBAC | France Central | secret Eventstream | supprimé (purge J+7) |
| Log Analytics | `log-smartbike-dev` | PerGB2018, rétention 30 j | France Central | audit | supprimé |
| Budget | `budget-smartbike-dev` | Cost Management | — | alerte 80 % de 20 € | supprimé |
| Azure Policy ×3 | `smartbike-*` | définitions personnalisées | abonnement / RG | gouvernance | supprimées |
| Capacité Fabric | essai (Trial) | ≈ F64 | région du tenant | calcul Fabric | expirée / en pause |
| Espace Fabric | `ws-smartbike-dev` | — | — | tous les items Fabric | conservé (lecture seule) |
| Lakehouse | `lh_smartbike` | OneLake / Delta | — | Silver + Gold | conservé |
| Pipeline | `pl_smartbike_batch` | Data Factory | — | ingestion batch | planification désactivée |
| Notebooks | `nb_01` … `nb_05` | Spark (pool de démarrage) | — | traitement + ML | — |
| Eventstream | `es_smartbike_status` | custom endpoint | — | ingestion temps réel | **supprimé** |
| Eventhouse | `eh_smartbike` / `kqldb_smartbike` | KQL | — | analyse temps réel | supprimé |
| Real-Time dashboard | `rtd_smartbike_live` | — | — | supervision | supprimé |
| Modèles ML | `smartbike_demand_model_*` | MLflow registry | — | prévision | conservés |
| Rapport | `rpt_smartbike` + `sm_smartbike` | Power BI Direct Lake | — | restitution | conservé |
| Collecteur | `gbfs_collector.py` | poste local / Container App | — | producteur d'événements | arrêté |

## Principaux postes de coût (estimation)

| Poste | Ordre de grandeur | Levier de maîtrise |
|---|---|---|
| **Capacité Fabric** | 0 € pendant l'essai ; une F2 payante ≈ 0,36 $/h (≈ 260 $/mois si allumée 24/7) | utiliser l'essai ; **mettre en pause** hors séances ; F2 suffit pour 3 mois de données |
| Eventstream + Eventhouse | consomment des CU en continu tant que le flux tourne | `--only-changes` (÷8 événements), snapshot complet 1×/15 min, rétention 30 j, cache chaud 7 j ; supprimer après la démo |
| Stockage ADLS | ~3 Go × ~0,02 €/Go/mois ≈ **< 0,10 €/mois** | tier Cool après 30 j, suppression à 365 j |
| Log Analytics | ingestion ~quelques Mo/jour → < 1 € | rétention 30 j, seulement 2 sources de logs |
| Key Vault | ~0,03 € / 10 000 opérations | négligeable |
| Spark | inclus dans les CU de la capacité | 3 mois au lieu de l'historique complet (2013→) ; *starter pool* ; arrêt de session |

**Budget** : 20 €/mois avec alerte à 80 % (`deploy.ps1` §7). Coût réel attendu pendant le projet : **≈ 0-2 €** (hors capacité Fabric payante).

## Si les exigences changent (question type du jury)
* **Volume ×100** (tout l'historique Citi Bike, ~200 M trajets) : partitionner `silver_trips` par `trip_date`, *OPTIMIZE / V-Order*, capacité ≥ F8, ingestion incrémentale (un mois par run, déjà paramétrée).
* **Latence < 1 min sur les priorités** : calculer le score dans l'Eventhouse (fonction KQL + modèle exporté ONNX) au lieu du notebook 04 planifié.
* **Sécurité renforcée** : *private endpoints*, *trusted workspace access*, CMK, étiquettes de sensibilité Purview propagées au rapport.
* **Coût minimal** : remplacer Eventstream/Eventhouse par un snapshot toutes les 5 min dans le Lakehouse (perte du vrai temps réel).
