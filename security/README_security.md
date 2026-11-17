# Sécurité & gouvernance — SmartBike AI

> Principe : la sécurité est visible **dans l'architecture** (frontières, identités, flux), pas ajoutée à la fin.
> Légende : ✅ implémenté dans le dépôt / démontrable · 📄 conçu et documenté (non déployé faute de droits/licence).

## 1. Identités et rôles (Microsoft Entra ID + RBAC, moindre privilège)

| Persona | Azure (RBAC) | Fabric (rôle d'espace de travail `ws-smartbike-dev`) | Ce qu'il **ne peut pas** faire |
|---|---|---|---|
| **Admin** (1 membre du groupe) | Owner sur `rg-smartbike-dev`, *Key Vault Secrets Officer* | Admin | — (compte nominatif, MFA obligatoire) |
| **Data Engineer** | *Storage Blob Data Contributor* sur le compte ADLS, *Key Vault Secrets User* | Contributor | Modifier les rôles, supprimer le RG, lire les secrets d'autres coffres |
| **Analyst / Reader** | *Reader* sur le RG (métadonnées uniquement) | Viewer + accès au rapport Power BI via une **app** | Lire la zone brute ADLS, lire les secrets, modifier notebooks/pipelines, voir `silver_trips` (lecture des seules tables Gold via le modèle sémantique) |
| **Identité du collecteur** (managed identity si VM/Container App, sinon compte de service) | *Key Vault Secrets User* sur le seul coffre | aucun | Tout le reste |

✅ Script : [`infra/deploy.ps1`](../infra/deploy.ps1) §5 crée ces attributions.
✅ **Démo (restriction de permission)** : se connecter avec le compte *Analyst* →
`az storage blob list --account-name stsmartbikedev<suffix> -c landing --auth-mode login` renvoie **403 AuthorizationPermissionMismatch**,
alors que le même appel avec le compte *Data Engineer* fonctionne. Dans Fabric, l'Analyst (Viewer) ne voit pas le bouton « Exécuter » des notebooks.

Option avancée : *Row-Level Security* dans le modèle sémantique Power BI (rôle « Opérateur Manhattan » filtré sur `region_id`) et **OneLake data access roles** pour cacher le dossier `Files/bronze` aux lecteurs.

## 2. Secrets et clés — aucun secret dans le code ✅

* La chaîne de connexion de l'Eventstream (*custom endpoint*, protocole Event Hub) est stockée dans **Azure Key Vault** (`eventstream-conn`).
* Le collecteur la lit à l'exécution avec `DefaultAzureCredential` (identité connectée / managed identity) — voir `get_secret()` dans [`scripts/gbfs_collector.py`](../scripts/gbfs_collector.py).
* Le compte de stockage a `allowSharedKeyAccess = false` : **aucune clé de compte** n'est utilisable, uniquement Entra ID.
* `.gitignore` exclut `.env`, `*.pem`, `local.settings.json`, `mlruns.db`. `config/settings.example.env` ne contient que des noms de variables.
* Key Vault en mode RBAC, *soft delete* + *purge protection*, journaux `AuditEvent` envoyés à Log Analytics.

## 3. Classification des données (Microsoft Purview) 📄

| Donnée | Champs | Classification | Justification / traitement |
|---|---|---|---|
| Trajets Citi Bike | `ride_id` | **Confidentiel – pseudonyme** | identifiant unique de location ; jamais exposé dans Power BI (seulement des agrégats) |
| Trajets Citi Bike | `start/end_lat`, `start/end_lng`, `started_at`, `ended_at` | **Confidentiel – donnée de localisation** | combinée à des horaires, une suite de trajets peut ré-identifier une personne (domicile/travail). Les tables Gold sont agrégées par station × heure |
| Trajets | `member_casual`, `rideable_type` | Interne | catégories, pas de PII |
| GBFS stations, météo | tous | **Public** | données ouvertes |
| Tables Gold | agrégats | Interne / Business | publiables aux analystes |

Purview : enregistrer le compte ADLS + l'espace Fabric comme sources, lancer un **scan** ; créer une **classification personnalisée** `SmartBike.GeoTrip` (règle : colonnes `*_lat`, `*_lng` dans `silver_trips`) et appliquer l'étiquette de sensibilité *Confidentiel* au Lakehouse ; le **lignage** Purview/Fabric montre `landing → silver_trips → gold_station_hourly → rapport`.

## 4. Politiques et localisation ✅ (policy-as-code)

| Politique ([`security/policies`](policies)) | Effet | Pourquoi |
|---|---|---|
| `allowed_locations.json` | **deny** hors `francecentral`, `westeurope` | résidence des données dans l'UE |
| `require_tag.json` | **deny** si le tag `project` est absent | suivi des coûts + nettoyage par tag |
| `deny_storage_public_access.json` | **deny** stockage public / clés partagées / TLS < 1.2 | aucune exposition anonyme |

Démo : `az storage account create ... -l eastus` dans le RG → refusé par *SmartBike - Allowed locations*.

**Conservation** : événements temps réel bruts gardés **30 jours** (politique de rétention KQL), cache chaud 7 jours ; fichiers bruts ADLS → tier *Cool* après 30 j, suppression après 365 j ([`infra/lifecycle_policy.json`](../infra/lifecycle_policy.json)).

## 5. Chiffrement et réseau

* **Au repos** : chiffrement Microsoft-managed keys (AES-256) pour ADLS, OneLake, Eventhouse ; option CMK dans Key Vault si exigé.
* **En transit** : HTTPS/TLS 1.2 minimum imposé (`--https-only`, `--min-tls-version TLS1_2`), AMQP over TLS pour Eventstream.
* **Endpoints publics** : les sources (Citi Bike, GBFS, Open-Meteo) sont publiques par nature. Le compte de stockage reste en endpoint public **mais sans accès anonyme ni clé** ; en production on ajouterait un *private endpoint* + *trusted workspace access* Fabric. Choix assumé pour un projet étudiant (coût et complexité réseau).

## 6. Surveillance et audit

| Événement à journaliser | Où | Alerte utile en production |
|---|---|---|
| Lecture de secret Key Vault | `AuditEvent` → Log Analytics | lecture par une identité inconnue |
| Écriture / suppression dans `landing` | `StorageWrite/Delete` | suppression massive de blobs |
| Échec du pipeline batch | Monitoring hub Fabric | e-mail/Teams (activité `Notify_On_Failure`) |
| Flux temps réel interrompu | KQL : aucune donnée depuis 10 min | **Activator** : « pas d'événement depuis 10 min » |
| Station vide > 15 min (métier) | KQL Q1 / Activator | notification à l'opérateur |
| Changement de rôle / d'accès | Journal d'activité Entra / Azure | ajout d'un Owner |
| Qualité des données dégradée (< 98 % de trajets valides, < 99 % de stations réconciliées) | `gold_data_quality` | ✅ `scripts/alert_monitor.py` (DQ-1, DQ-2) → webhook Teams, code retour 2 |
| Modèle moins bon que la baseline (dérive) | `gold_ml_metrics` | ✅ `scripts/alert_monitor.py` (ML-1) + promotion champion/challenger bloquée |
| Appel de l'API sans clé valide | journaux App Service | ✅ `app/api.py` répond 401 (clé lue dans l'environnement / Key Vault, comparée en temps constant) |

Règles temps réel prêtes pour **Activator** : [`kql/03_alerts.kql`](../kql/03_alerts.kql) (ALERT-1 station très utilisée vide ≥ 10 min,
ALERT-2 > 8 % du réseau vide/saturé, ALERT-3 collecteur arrêté).

Requête Log Analytics d'exemple :
```kusto
AzureDiagnostics
| where ResourceProvider == "MICROSOFT.KEYVAULT" and OperationName == "SecretGet"
| summarize reads = count() by identity_claim_upn_s, bin(TimeGenerated, 1h)
```

## 7. Coûts et nettoyage
Voir [`docs/cost_and_inventory.md`](../docs/cost_and_inventory.md) et [`infra/cleanup.ps1`](../infra/cleanup.ps1).
