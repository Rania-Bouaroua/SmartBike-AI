# Proposition de projet — SmartBike AI

**Cours** : Design of Cloud Data-Driven Applications — 2026/2027 — Pr. Ibrahim RAHHAL
**Équipe** : *(noms et rôles à compléter : ex. Membre 1 architecture & sécurité, Membre 2 batch & Spark, Membre 3 temps réel & ML, Membre 4 Power BI)*
**Plateforme** : Microsoft Azure (ADLS Gen2, Key Vault, Azure Policy, Log Analytics) + Microsoft Fabric (Data Factory, Lakehouse, Spark, Eventstream, Eventhouse/KQL, MLflow, Power BI)

## 1. Problème
Dans un système de vélos partagés, un usager trouve régulièrement sa station **vide** au moment de partir, ou **pleine** au moment
de rendre son vélo. L'exploitant doit savoir **où, quand et pourquoi** ces situations se produisent pour **prioriser le rééquilibrage**
(camions qui déplacent des vélos). SmartBike AI fournit une vue live, une analyse historique et une prévision à court terme pour
transformer ces données en liste d'interventions.

## 2. Utilisateurs et décisions
| Utilisateur | Question | Décision |
|---|---|---|
| Usager | Quelles stations ont des vélos / places maintenant ? | choisir une station alternative |
| Exploitant | Quelles stations seront vides/saturées dans 2 h ? | ordre de passage des camions, nombre de vélos à déplacer |
| Analyste mobilité | Quand, où et par quel temps la demande est-elle forte ? | dimensionner stations et flotte |

## 3. Sources de données (hétérogènes, publiques, réelles)
| Source | Format / accès | Volume | Usage |
|---|---|---|---|
| Citi Bike NYC — historique des trajets | CSV mensuels zippés (HTTP) | ~5 M trajets/mois ; 3 mois (août-oct. 2026) | batch, Spark, ML |
| Citi Bike GBFS — `station_status`, `station_information` | API JSON, mise à jour ~60 s | ~2 500 stations | temps réel, référentiel |
| Open-Meteo — archive et prévision | API REST JSON | 1 point horaire | enrichissement, variables ML |

Champs sensibles : `ride_id` et coordonnées/horaires de trajets (données de localisation) → classés *Confidentiel*, jamais exposés
non agrégés.

## 4. Architecture prévue
Voir `docs/architecture.png`. Batch : Data Factory copie les archives dans ADLS Gen2 (zone d'atterrissage) → raccourci OneLake →
Lakehouse (Bronze/Silver/Gold, Delta) → notebooks Spark. Temps réel : collecteur Python (GBFS 60 s) → Eventstream → Eventhouse
→ KQL → Real-Time Dashboard / Activator. ML : régression des départs/arrivées par station et heure (MLflow). Restitution : Power BI
en Direct Lake. Sécurité transverse : Entra ID/RBAC (3 personas), Key Vault, Azure Policy, Purview, journaux, budget.

## 5. Choix de services (justification courte)
* **ADLS Gen2 + Lakehouse** : données fichiers (CSV/JSON) volumineuses et lues par scans → data lake plutôt que SQL/Cosmos DB.
* **Spark** : 15 M+ lignes, jointures et agrégats distribués, écriture Delta.
* **Eventstream/Eventhouse** : ingestion continue et requêtes temporelles (fenêtres, `arg_max`, anomalies) que le Lakehouse fait mal.
* **Power BI Direct Lake** : lecture directe des tables Delta, sans copie ni rafraîchissement planifié.

## 6. Critères de réussite (démo)
Importation batch rejouable, table Delta Gold produite par Spark, arrivée d'événements live, ≥ 2 requêtes KQL dont une fenêtre
temporelle, rapport Power BI interactif répondant à ≥ 2 questions métier, prédiction évaluée contre une baseline, ≥ 1 contrôle de
sécurité réellement appliqué (refus d'accès de l'Analyst au stockage brut ; Azure Policy de localisation).

## 7. Risques
| Risque | Mesure |
|---|---|
| Capacité / coût Fabric | essai Fabric, 3 mois de données, pause de la capacité, budget Azure 20 € avec alerte |
| Identifiants de stations différents entre trajets et GBFS | réconciliation par `short_name`, taux mesuré et publié |
| Historique live inexistant | collecteur lancé dès le début, rétention 30 j |
| Demande censurée (station vide = pas de trajet) | limite affichée ; prévisions présentées comme « locations attendues » |
| ML peu performant au niveau station-heure | baseline explicite, MAE/RMSE, évaluation aussi au niveau réseau |

## 8. Planning
S1 validation + faisabilité · S2 socle batch (ADLS, pipeline, Spark, Gold) · S3 temps réel (collecteur, Eventstream, KQL) ·
S4 ML + Power BI · S5 sécurité appliquée, coûts, README, répétition de la démo.
