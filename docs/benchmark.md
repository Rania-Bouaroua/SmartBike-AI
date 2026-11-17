# Benchmark de l'API

Cible : `http://api:8000` · client : Python 3.14.10 / httpx (connexions réutilisées), même machine (x86_64, 8 cœurs logiques). 400 requêtes par scénario (rebalancing : 100), après échauffement (20 requêtes par scénario + 5 s).

| Endpoint | Concurrence | p50 (ms) | p95 (ms) | p99 (ms) | Débit (req/s) | Erreurs |
|---|---:|---:|---:|---:|---:|---:|
| GET /health (liveness) | 1 | 5.1 | 7.3 | 8.4 | 185 | 0 |
| GET /health (liveness) | 8 | 109.0 | 166.1 | 374.0 | 67 | 0 |
| GET /health (liveness) | 32 | 290.3 | 1185.3 | 2119.8 | 68 | 0 |
| GET /v1/priority?limit=50 | 1 | 16.5 | 18.8 | 20.4 | 60 | 0 |
| GET /v1/priority?limit=50 | 8 | 167.5 | 284.7 | 480.2 | 45 | 0 |
| GET /v1/priority?limit=50 | 32 | 717.5 | 2653.4 | 4051.5 | 31 | 0 |
| GET /v1/predict (1 station-hour) | 1 | 58.8 | 97.3 | 171.6 | 16 | 0 |
| GET /v1/predict (1 station-hour) | 8 | 804.1 | 1108.7 | 1360.6 | 10 | 0 |
| GET /v1/predict (1 station-hour) | 32 | 3709.7 | 5503.6 | 5677.5 | 8 | 0 |
| GET /v1/rebalancing-plan (cached) | 1 | 11.3 | 14.6 | 36.3 | 84 | 0 |
| GET /v1/rebalancing-plan (cached) | 8 | 84.2 | 147.8 | 308.5 | 78 | 0 |
| GET /v1/rebalancing-plan (cached) | 32 | 422.9 | 1703.2 | 1948.7 | 48 | 0 |

**Environnement.** Image `smartbike-ai` (1 processus uvicorn, `OMP_NUM_THREADS=1`) sur Docker Desktop / WSL2, Windows,
8 cœurs partagés avec d'autres conteneurs actifs (un autre projet : Trino, Superset, Postgres…), client dans le même
réseau Docker. Ce n'est pas un environnement de production : les chiffres servent à **comparer** et à **diagnostiquer**,
pas à garantir un SLA. Relancer : voir l'en-tête de `scripts/benchmark_api.py`.

## Lecture
* **Un appelant à la fois** (concurrence 1) : `/health` 5 ms, liste de priorité 17 ms, plan camion (en cache) 11 ms,
  prédiction 59 ms. La prédiction est dominée par les 2 modèles (≈ 14 ms chacun : plusieurs centaines d'arbres).
* **Sous charge**, un processus Python exécute un calcul à la fois (GIL) : le débit des endpoints de calcul plafonne
  (≈ 10 prédictions/s) et la latence augmente avec la file d'attente. C'est attendu ; la réponse est **horizontale**.
* **Montée en charge par réplicas, mesurée** : 3 conteneurs API derrière le même nom DNS, 32 appelants, 400 prédictions :

| `/v1/predict`, concurrence 32 | p50 | p95 | Débit |
|---|---:|---:|---:|
| 1 réplica | 3 056 ms | 3 994 ms | 10,2 req/s |
| 3 réplicas | 888 ms | 3 116 ms | 22,6 req/s (× 2,2) |

  (pas × 3 : répartition DNS inégale et 8 cœurs partagés). En Azure : instances d'App Service / réplicas Container Apps
  derrière leur équilibreur.

## Problèmes trouvés par le benchmark et corrigés
| Symptôme | Cause (vérifiée par expérience isolée) | Correction | Effet |
|---|---|---|---|
| `/health` 110 ms dans le conteneur | chaque requête relisait les dates des fichiers (`stat()`) sur un volume monté | cache de métadonnées 10 s | 110 → 6 ms |
| +45 ms sur chaque requête en connexion réutilisée | `uvicorn --workers 2` (52 ms vs 10 ms avec 1 processus, même code) | 1 processus par conteneur + réplicas | supprimé |
| `/v1/predict` 68 ms, moitié en jointures | jointure d'une ligne contre des tables de 120 000 lignes d'encodages | index par station construit une fois par version | 68 → 45 ms (hôte) |
| prédiction **plus lente** à 8 appelants qu'à 1 | OpenMP : 8 requêtes × 8 threads pour prédire une ligne | `OMP_NUM_THREADS=1` | 6 → 10 req/s |
| mesures depuis Windows ×10 trop lentes | redirection de ports de Docker Desktop (~55 ms/requête) | mesurer depuis le réseau Docker | méthode |

Pistes suivantes si la charge l'exigeait : prédire par lots (une requête pour N stations/heures), exporter les modèles en
ONNX (prédiction ~10× plus rapide), mettre les réponses de `/v1/priority` en cache par version de table.
