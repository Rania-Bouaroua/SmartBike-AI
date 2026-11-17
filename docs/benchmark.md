# Benchmark de l'API

Cible : `http://api:8000` · client : Python 3.14.10 / httpx (connexions réutilisées), même machine (x86_64, 8 cœurs logiques). 400 requêtes par scénario (rebalancing : 100), après échauffement (20 requêtes par scénario + 5 s).

| Endpoint | Concurrence | p50 (ms) | p95 (ms) | p99 (ms) | Débit (req/s) | Erreurs |
|---|---:|---:|---:|---:|---:|---:|
| GET /health (liveness) | 1 | 9.4 | 12.4 | 32.8 | 92 | 0 |
| GET /health (liveness) | 8 | 92.4 | 157.5 | 173.9 | 77 | 0 |
| GET /health (liveness) | 32 | 324.6 | 1505.1 | 2428.4 | 62 | 0 |
| GET /v1/priority?limit=50 | 1 | 19.4 | 26.7 | 35.9 | 49 | 0 |
| GET /v1/priority?limit=50 | 8 | 165.0 | 299.3 | 454.4 | 45 | 0 |
| GET /v1/priority?limit=50 | 32 | 610.4 | 1262.7 | 1982.0 | 46 | 0 |
| GET /v1/predict (1 station-hour) | 1 | 54.5 | 87.2 | 117.0 | 17 | 0 |
| GET /v1/predict (1 station-hour) | 8 | 1266.5 | 1513.5 | 1633.6 | 6 | 0 |
| GET /v1/predict (1 station-hour) | 32 | 5749.5 | 7781.4 | 8776.4 | 5 | 0 |
| GET /v1/rebalancing-plan (cached) | 1 | 12.4 | 17.0 | 34.3 | 76 | 0 |
| GET /v1/rebalancing-plan (cached) | 8 | 112.7 | 245.1 | 355.2 | 58 | 0 |
| GET /v1/rebalancing-plan (cached) | 32 | 423.5 | 1663.8 | 1840.0 | 50 | 0 |
