"""Generates docs/architecture.png and docs/architecture.svg (python docs/make_architecture_diagram.py)."""
import pathlib

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUT = pathlib.Path(__file__).parent
fig, ax = plt.subplots(figsize=(17, 9.6))
ax.set_xlim(0, 170)
ax.set_ylim(0, 96)
ax.axis("off")

NL = chr(10)
C = {"src": "#E8F1FB", "ing": "#FFF4E0", "sto": "#E9F7EF", "proc": "#F3EAFB", "rt": "#FDEBEC",
     "ml": "#EAF4F4", "bi": "#FFF9DB", "sec": "#F2F2F2", "edge": "#3B3B3B"}


def zone(x, y, w, h, title, color):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.4,rounding_size=2",
                                fc=color, ec="#9AA0A6", lw=1.2, ls="--"))
    ax.text(x + 1.2, y + h - 2.2, title, fontsize=10.5, fontweight="bold", color="#202124", va="top")


def box(x, y, w, h, title, sub="", color="white"):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.3,rounding_size=1.2",
                                fc=color, ec="#5F6368", lw=1.1))
    # title + subtitle laid out as one block centred vertically (block height grows with the number of lines)
    n = sub.count(NL) + 1 if sub else 0
    block = 2.4 + (0.5 + 1.75 * n if sub else 0)
    top = y + h / 2 + block / 2
    ax.text(x + w / 2, top, title, ha="center", va="top", fontsize=9, fontweight="bold")
    if sub:
        ax.text(x + w / 2, top - 2.9, sub, ha="center", va="top", fontsize=7.3, color="#3C4043", linespacing=1.25)
    return (x, y, w, h)


def arrow(a, b, label="", style="-|>", color=C["edge"], rad=0.0, side=("r", "l"), dy=(0, 0)):
    def anchor(bx, s, d):
        x, y, w, h = bx
        return {"r": (x + w, y + h / 2 + d), "l": (x, y + h / 2 + d), "t": (x + w / 2 + d, y + h),
                "b": (x + w / 2 + d, y)}[s]
    p1, p2 = anchor(a, side[0], dy[0]), anchor(b, side[1], dy[1])
    ax.add_patch(FancyArrowPatch(p1, p2, arrowstyle=style, mutation_scale=13, color=color, lw=1.4,
                                 connectionstyle=f"arc3,rad={rad}"))
    if label:
        ax.text((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2 + 1.3, label, ha="center", fontsize=7, color="#1A73E8",
                style="italic", bbox=dict(fc="white", ec="none", pad=0.4))


ax.text(85, 94, "SmartBike AI — architecture de bout en bout (Azure + Microsoft Fabric)",
        ha="center", fontsize=15, fontweight="bold")
ax.text(85, 90.6, "Collecter → Ingérer → Stocker → Traiter → Analyser → Prévoir → Visualiser · sécurisé & gouverné",
        ha="center", fontsize=10, color="#5F6368")

# --- zones
zone(1, 18, 27, 69, "1. Sources", C["src"])
zone(31, 18, 25, 69, "2. Ingestion", C["ing"])
zone(59, 46, 52, 41, "3-4-5. Stockage & traitement batch (Fabric)", C["sto"])
zone(59, 18, 52, 25.5, "6. Temps réel (Fabric RTI)", C["rt"])
zone(114, 46, 26, 41, "7. Machine Learning", C["ml"])
zone(143, 18, 26, 69, "8. Consommation", C["bi"])
zone(1, 1, 168, 14, "9. Sécurité & gouvernance (transverse)", C["sec"])

# --- sources
s1 = box(3, 66, 23, 13, "Citi Bike trips", "archives CSV mensuelles\n~5 M trajets/mois (S3 public)")
s2 = box(3, 46, 23, 13, "Open-Meteo", "API REST JSON\nmétéo horaire NYC")
s3 = box(3, 24, 23, 15, "Citi Bike GBFS", "API JSON temps réel\nstation_status (~60 s)\nstation_information")

# --- ingestion
i1 = box(33, 58, 21, 20, "Fabric Data Factory", "pl_smartbike_batch\nCopy HTTP→ADLS (unzip)\nmensuel (J+8) + notebooks")
i2 = box(33, 24, 21, 17, "Collecteur Python", "gbfs_collector.py\npoll 60 s · deltas\n+ snapshot complet /15 min")

# --- storage / batch
st = box(61, 68, 22, 13, "ADLS Gen2", "stsmartbikedev · landing\n(Bronze physique, Cool 30 j)")
lh = box(87, 68, 22, 13, "Lakehouse", "lh_smartbike (OneLake)\nraccourci Files/bronze\nTables/ Delta")
sp = box(61, 49, 48, 15, "Apache Spark (notebooks 01-02 · 05)",
         "Bronze → Silver : typage, dédup, contrôles qualité,\nréconciliation des IDs de stations, météo horaire\n"
         "Silver → Gold : grille station×heure, KPI, profils (Delta)\n05 : fenêtres glissantes, CUBE, flux O-D, rééquilibrage")

# --- real time
es = box(61, 21, 15, 15, "Eventstream", "es_smartbike_status\ncustom endpoint")
eh = box(79, 21, 14, 15, "Eventhouse", "kqldb_smartbike\nStationStatus\n+ vue matérialisée")
kq = box(96, 21, 13, 15, "KQL", "stations vides\nfenêtres 15 min\nanomalies")

# --- ML
ml = box(116, 61, 22, 13, "Notebook 03 · MLflow",
         "7 algorithmes comparés\nExtra Trees · départs/arrivées\ndéviance Poisson vs baseline\nchampion / challenger")
pr = box(116, 47.5, 22, 11, "Notebook 04 · priorités",
         "état live + prévision météo\n→ risque vide/saturée\n→ vélos à déplacer\n→ API FastAPI + app Streamlit")

# --- consumption
bi = box(145, 62, 22, 18, "Power BI (Direct Lake)", "KPI, tendances, carte,\nheatmap, météo,\ninterventions prioritaires")
rtd = box(145, 40, 22, 14, "Real-Time Dashboard", "état du réseau\nrafraîchi 30 s")
act = box(145, 22, 22, 12, "Activator + alert_monitor", "station vide ≥ 10 min\nflux interrompu\nqualité · dérive modèle")

# --- arrows
arrow(s1, i1, "HTTP zip", dy=(0, 4))
arrow(s2, i1, "REST JSON", dy=(0, -4), rad=0.1)
arrow(s3, i1, "station_information", side=("r", "b"), rad=-0.25, dy=(4, 0))
arrow(s3, i2, "station_status", dy=(-3, 0))
arrow(i1, st, "Copy", dy=(5, 0))
arrow(st, lh, "shortcut\n(zéro copie)")
arrow(lh, sp, side=("b", "t"), dy=(0, 13))
arrow(i1, sp, "déclenche", side=("r", "l"), dy=(-6, 0))
arrow(i2, es, "AMQP/TLS\nJSON events")
arrow(es, eh)
arrow(eh, kq)
arrow(sp, ml, "gold_station_hourly", side=("r", "l"), dy=(5, 0))
arrow(ml, pr, "modèle enregistré", side=("b", "t"))
arrow(pr, bi, "gold_station_priority", side=("r", "l"), dy=(0, -5))
arrow(lh, bi, "tables Gold (Delta) · Direct Lake", side=("r", "l"), dy=(5, 6))
arrow(kq, rtd, "requêtes KQL", side=("r", "l"), dy=(4, 0), rad=-0.15)
arrow(kq, act, "règle Activator", side=("r", "l"), dy=(-4, 0))

# --- security strip
items = ["Entra ID + RBAC : Admin / Data Engineer / Analyst (moindre privilège)",
         "Key Vault : chaîne Eventstream, aucun secret dans Git, pas de clé de stockage",
         "Azure Policy : régions UE, tag obligatoire, stockage non public · TLS 1.2",
         "Purview : classification localisation/ride_id, lignage · rétention 30 j (KQL), 365 j (ADLS)",
         "Log Analytics : audit Key Vault/Storage · budget 20 € + alerte · cleanup.ps1"]
for k, t in enumerate(items):
    ax.text(3 + (k % 2) * 84, 9.3 - (k // 2) * 3.3, "• " + t, fontsize=7.8, va="center")

for ext in ("png", "svg"):
    fig.savefig(OUT / f"architecture.{ext}", dpi=160, bbox_inches="tight")
print("written", OUT / "architecture.png")
