# Décisions d'architecture (ADR)

Chaque décision : contexte → décision → alternatives écartées → preuve. Les chiffres viennent des exécutions sur les
données réelles (voir `docs/results.md`, `docs/benchmark.md`).

## ADR-01 · Un package métier `smartbike/`, sans Spark ni I/O
**Contexte.** La logique de prévision des ruptures existait en trois copies (notebook 04, `app/common.py`, notebook 03
pour les variables du modèle) : impossible à tester, risque de divergence entre entraînement et service.
**Décision.** Un package Python pur (`features`, `risk`, `routing`, `evaluation`, `contracts`) utilisé par les notebooks,
l'API, l'application et le backtest. Dans Fabric, il est publié comme *wheel* dans l'Environment des notebooks.
**Écarté.** Garder la logique dans les notebooks (non testable) ; un micro-service de scoring (latence + exploitation
sans bénéfice à cette échelle).
**Preuve.** 38 tests, dont un test de parité exact de la simulation (ci-dessous) ; même prédiction via registre MLflow et via
l'image Docker.

## ADR-02 · Risque **probabiliste** plutôt que projection ponctuelle
**Contexte.** L'ancienne règle comparait le stock *attendu* dans 2 h à un seuil. Une station très active mais équilibrée
n'était jamais signalée, alors que son stock oscille fortement.
**Décision.** Les modèles de demande sont entraînés avec une perte de Poisson : ils prédisent un *taux*. On simule le
chemin du stock par pas de 10 min (départs et arrivées de Poisson, bornés par 0 et la capacité utilisable) et on estime
P(la station touche 0 / sa capacité dans l'horizon). 1 000 simulations × 2 500 stations < 1 s, graine fixe.
**Écarté.** Loi de Skellam en fin d'horizon (ignore le chemin : une station vide à 15 h puis réapprovisionnée par un
cycliste à 16 h compte pourtant comme une rupture) ; quantiles appris (pas de vérité terrain suffisante pour les caler).
**Preuve.** Test unitaire : sans arrivées, le résultat Monte Carlo égale la formule exacte P(Poisson(λH) ≥ stock) à 1 %
près. Backtest (section 10 des résultats) : meilleure PR-AUC et meilleur F1 que les deux autres méthodes ; P(vide) bien
calibrée.

## ADR-03 · Capacité **utilisable** = vélos disponibles + bornes libres
**Contexte.** Le premier backtest montrait P(pleine) mal calibrée : des stations annoncées à 20-35 % étaient pleines
dans 50-70 % des cas.
**Décision.** Borner la simulation par la capacité réellement utilisable, pas par la capacité nominale.
**Preuve.** Relevé du 17/11/2026 : 90 % des stations ont moins de bornes utilisables que leur capacité nominale (écart
médian 3 ; 5 724 vélos en panne bloquent des bornes, 470 bornes hors service) ; 92 % des stations pleines sont dans ce
cas. Après correction : PR-AUC « pleine » 0,48 → 0,64, Brier 0,047 → 0,031, seuil optimal 0,05 → 0,35.

## ADR-04 · Tournées des camions par optimisation (OR-Tools)
**Contexte.** La liste de priorité dit *quoi* réparer, pas *qui va où et dans quel ordre* ; la suivre dans l'ordre fait
traverser la ville et vide le camion trop tôt.
**Décision.** Problème de collecte-livraison à une marchandise avec capacité, durée de tournée et prix (une station non
visitée coûte sa priorité). OR-Tools, recherche locale guidée, limitée à 15 s. Routes ouvertes, camions partis vides.
**Écarté.** Programmation linéaire exacte (explose au-delà de ~30 stations) ; heuristique maison (moins bonne et à
maintenir).
**Preuve.** Relevé de 14 h : +59 % de vélos déplacés, 60 % de l'urgence couverte contre 38 %, 74 km contre 109 km. Tests :
charge toujours dans [0, Q], durée respectée, jamais pire que la liste de priorité.

## ADR-05 · Le service charge un **paquet de modèles exporté**, pas le registre
**Contexte.** Le registre MLflow (sqlite) stocke des chemins absolus de la machine d'entraînement
(`file:C:/Users/...`) : une image Docker ou un App Service ne peut pas résoudre `models:/…@champion`.
**Décision.** `scripts/export_models.py` copie la version *champion* au format skops (sérialisation sûre, un seul type
explicitement autorisé : `TreePredictor`) avec un `manifest.json` (version, métriques, variables). Le service lit
`SMARTBIKE_MODEL_DIR` ; chaque prédiction indique `serving:<modèle>/v<version>`.
**Preuve.** Prédictions identiques registre / paquet (47,82 = 47,82).

## ADR-06 · Contrats de données à l'entrée du service
**Décision.** Schémas pandera pour les tables servies (clés, bornes, libellés, règles croisées : l'action suit le signe
du nombre de vélos ; une station « ok » n'a pas de déplacement). Vérifiés une fois par version de table ; l'API répond
503 avec la raison plutôt que d'afficher des chiffres faux. `scripts/validate_contracts.py` sert de contrôle qualité.

## ADR-07 · Un processus par conteneur, `OMP_NUM_THREADS=1`
**Contexte.** Premier benchmark : `/health` à 110 ms, `/v1/predict` *plus lent* à 8 requêtes simultanées qu'une par une.
**Décisions et preuves** (mesurées dans le réseau Docker, `docs/benchmark.md`) :
* métadonnées de fichiers (dates des tables) mises en cache 10 s : chaque `stat()` sur un volume monté coûte des ms
  (`/health` 110 → 6 ms) ;
* un seul processus uvicorn par conteneur, montée en charge par réplicas : `--workers 2` ajoutait ~45 ms à chaque
  requête sur connexion réutilisée (52 ms contre 10 ms) ;
* `OMP_NUM_THREADS=1` : une prédiction d'une ligne ne gagne rien à OpenMP, et N requêtes × N cœurs de threads
  saturent le CPU ;
* encodages indexés par station (dictionnaire construit une fois par version) : `/v1/predict` 68 → 45 ms.

## ADR-08 · Backtest « walk-forward » sans regard vers le futur
**Décision.** À chaque instant t0 : état réel à t0, météo, modèles champion, alerte ; vérité = la station a-t-elle été
observée vide/pleine dans (t0, t0+H] ? Fenêtres refusées si l'enregistrement a un trou > 15 min ; stations déjà en rupture
exclues ; seuil réglé sur la 1re moitié des instants, mesuré sur la 2de.
**Preuve.** Un test a révélé que pandas 3 stocke les dates en µs alors que `Timestamp.value` est en ns : la détection des
trous ne marchait pas et aurait faussé tout le backtest. Corrigé (comparaison de durées) avant le premier calcul réel.

## ADR-09 · Sélection du modèle sur la déviance de Poisson, parmi 7 algorithmes
**Contexte.** Exigence du cours : au moins 4 modèles réels comparés sur les données réelles. L'ancien critère de
promotion (MAE) ne mesurait pas ce que consomme la simulation du risque (ADR-02) : un **taux** de Poisson, pas une valeur
ponctuelle. Une prévision proche de la médiane (souvent 0 ou 1 trajet) a une bonne MAE mais un taux mal calibré.
**Décision.** 7 algorithmes (Ridge, GLM de Poisson, MLP, Random Forest, Extra Trees, gradient boosting L2 et Poisson)
+ baseline, entraînés sur le même échantillon (1 M lignes) et jugés sur une semaine de **validation** distincte de la
semaine de **test** (qui ne sert qu'une fois, au modèle retenu). Critère : déviance de Poisson ; parmi les modèles à
moins de 1 % du meilleur, le plus léger. Promotion MLflow : candidat et champion ré-évalués sur la même semaine de test,
même critère, et le candidat doit battre la baseline.
**Résultat.** Extra Trees retenu pour les deux cibles (+4,4 % / +5,4 % de déviance vs baseline en validation) et promu
champion v5 (test : 1,427 contre 1,503 pour l'ancien gradient boosting en départs). Compromis assumé : sa MAE de test est
légèrement moins bonne (1,315 contre 1,296), car la MAE récompense les prévisions proches de la médiane ; Ridge et le MLP
ont une MAE correcte mais une déviance très mauvaise (taux mal calibrés).
