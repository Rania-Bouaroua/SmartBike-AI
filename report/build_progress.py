"""Builds the progress note shown to the professor (state of the project at a given date).

    python docs/make_results.py        # refresh numbers + figures first
    python report/build_progress.py    # -> report/SmartBike_AI_Etat_Avancement.docx (+ .pdf through Word on Windows)

Numbers are read from the Gold tables, never typed by hand.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import subprocess
import sys

import pandas as pd
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = pathlib.Path(__file__).resolve().parents[1]
T = ROOT / "data" / "lakehouse" / "Tables"
FIG = ROOT / "docs" / "figures"
OUT = ROOT / "report" / "SmartBike_AI_Etat_Avancement.docx"

# Fill in the team before printing; an empty list prints a blank line to complete by hand.
TEAM: list[str] = ["Rania BOUAROUA", "Salma BENMOUMA", "Oumaima GASSI", "Leonce Pharell Sandy THEUREAU MENDOME"]

NAVY, BLUE, GREY = RGBColor(0x16, 0x32, 0x4F), RGBColor(0x2A, 0x78, 0xD6), RGBColor(0x52, 0x51, 0x4E)
STATUS = {  # label, cell fill
    "fait": ("FAIT", "D7F0E5"),
    "pret": ("PRÊT À DÉPLOYER", "DCE9FA"),
    "afaire": ("À FAIRE", "FDEBD3"),
}
MODEL_LABELS = {"baseline": "Baseline (moyenne historique)", "ridge": "Ridge (linéaire)", "poisson_glm": "GLM de Poisson",
                "mlp": "Réseau de neurones (MLP)", "random_forest": "Random Forest", "extra_trees": "Extra Trees",
                "hgb_squared": "Gradient boosting (L2)", "hgb_poisson": "Gradient boosting (Poisson)"}


def t(name: str) -> pd.DataFrame:
    return pd.read_parquet(T / name)


def fmt(n: float) -> str:
    return f"{n:,.0f}".replace(",", " ")


# ------------------------------------------------------------------ document setup
doc = Document()
for s in doc.sections:
    s.left_margin = s.right_margin = Cm(1.8)
    s.top_margin = s.bottom_margin = Cm(1.5)
st = doc.styles["Normal"]
st.font.name, st.font.size = "Calibri", Pt(10)
st.element.rPr.rFonts.set(qn("w:eastAsia"), "Calibri")
st.paragraph_format.space_after = Pt(3)
for lvl, size in [(1, 15), (2, 12)]:
    h = doc.styles[f"Heading {lvl}"]
    h.font.name, h.font.size, h.font.color.rgb, h.font.bold = "Calibri", Pt(size), NAVY if lvl == 1 else BLUE, True
    h.paragraph_format.space_before, h.paragraph_format.space_after = Pt(10), Pt(4)


def shade(cell, hex_fill: str) -> None:
    tc = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    tc.append(shd)


def para(text: str = "", bold: bool = False, size: float | None = None, color=None, align=None, italic=False):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.bold, r.italic = bold, italic
    if size:
        r.font.size = Pt(size)
    if color:
        r.font.color.rgb = color
    if align is not None:
        p.alignment = align
    return p


def bullet(text: str, bold_prefix: str | None = None) -> None:
    p = doc.add_paragraph(style="List Bullet")
    if bold_prefix:
        p.add_run(bold_prefix).bold = True
    p.add_run(text)
    p.paragraph_format.space_after = Pt(1)


def table(header: list[str], rows: list[list[str]], widths: list[float], status_col: int | None = None,
          size: float = 8.5, bold_rows: set[int] | None = None):
    tb = doc.add_table(rows=1, cols=len(header))
    tb.style = "Table Grid"
    tb.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, h in enumerate(header):
        c = tb.rows[0].cells[i]
        c.text = ""
        r = c.paragraphs[0].add_run(h)
        r.bold, r.font.size, r.font.color.rgb = True, Pt(size), RGBColor(0xFF, 0xFF, 0xFF)
        shade(c, "16324F")
    for k, row in enumerate(rows):
        cells = tb.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = ""
            if status_col is not None and i == status_col:
                label, fill = STATUS[v]
                r = cells[i].paragraphs[0].add_run(label)
                r.bold = True
                shade(cells[i], fill)
            else:
                r = cells[i].paragraphs[0].add_run(v)
                r.bold = bool(bold_rows and k in bold_rows)
            r.font.size = Pt(size)
    for row in tb.rows:
        for i, w in enumerate(widths):
            row.cells[i].width = Cm(w)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return tb


def figure(name: str, width_cm: float, caption: str) -> None:
    path = FIG / name if not name.startswith("/") else pathlib.Path(name)
    if not path.exists():
        return
    doc.add_picture(str(path), width=Cm(width_cm))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    para(caption, italic=True, size=8.5, color=GREY, align=WD_ALIGN_PARAGRAPH.CENTER)


# ------------------------------------------------------------------ data
quality = t("gold_data_quality")
daily = t("gold_daily_kpis")
valid_row = quality.loc[quality.check == "valid"].iloc[0]
valid = int(valid_row["rows"])
# the table mixes trip-level checks (they partition the raw trips) and station counts
raw_trips = int(quality.loc[~quality.check.str.startswith("station_"), "rows"].sum())
stations = t("gold_station_profile")
n_stations = len(stations)
days = daily["date"].nunique()
cmp_ = t("gold_model_comparison") if (T / "gold_model_comparison").exists() else None
promo = t("gold_model_promotion_log") if (T / "gold_model_promotion_log").exists() else None
today = dt.date.today()

# ------------------------------------------------------------------ title block
logo = ROOT / "assets" / "logo_full.png"
if logo.exists():
    doc.add_picture(str(logo), width=Cm(7))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
para("SmartBike AI — État d'avancement du projet", bold=True, size=18, color=NAVY, align=WD_ALIGN_PARAGRAPH.CENTER)
para("Design of Cloud Data-Driven Applications · 2026-2027 · Pr. Ibrahim RAHHAL", size=10, color=GREY,
     align=WD_ALIGN_PARAGRAPH.CENTER)
para(f"Point au {today:%d/%m/%Y}", bold=True, size=10, color=BLUE, align=WD_ALIGN_PARAGRAPH.CENTER)
para("Membres du groupe : " + (", ".join(TEAM) if TEAM else "……………………………………………………………………………"),
     size=10, align=WD_ALIGN_PARAGRAPH.CENTER)

doc.add_heading("1. En bref", level=1)
para("Sujet : anticiper les stations de vélos partagés vides ou saturées (données réelles Citi Bike New York + météo) "
     "et aider l'opérateur à décider où intervenir.")
bullet(f" toute la chaîne de traitement tourne sur les vraies données : {fmt(valid)} trajets valides sur {days} jours "
       f"(août → octobre 2026), {n_stations} stations, médaillon Bronze → Silver → Gold, modèles ML, alertes, application.",
       "Réalisé :")
bullet(" le code des éléments cloud est écrit et testé (notebooks Fabric .ipynb, pipeline Data Factory, tables et "
       "requêtes KQL, scripts Azure de déploiement / nettoyage, Azure Policies, mesures DAX).",
       "Prêt à déployer :")
bullet(" déploiement dans notre tenant Azure / Fabric, construction du rapport Power BI (.pbix), démonstration "
       "et vidéo de secours.", "Reste à faire :")

# ------------------------------------------------------------------ requirements table
doc.add_heading("2. Avancement par exigence du cahier des charges", level=1)
rows = [
    ["A. Problème & sources", "3 sources hétérogènes : archives CSV Citi Bike, API JSON GBFS (temps réel), API "
     "Open-Meteo. Dictionnaire de données et classification des champs.", "fait"],
    ["B. Ingestion batch", "Scripts de téléchargement exécutés ; pipeline pl_smartbike_batch (Copy HTTP → ADLS + "
     "notebooks, paramètre month, mensuel) défini en JSON.", "pret"],
    ["C. Stockage", "ADLS Gen2 (landing) + Lakehouse OneLake (raccourci), couches raw/curated justifiées. "
     "Reproduit en local.", "pret"],
    ["D. Spark → Delta", f"Notebooks 01, 02, 05 exécutés : contrôles qualité ({valid / raw_trips:.2%} de lignes "
     "valides), réconciliation des stations, jointure météo, agrégats, fenêtres, CUBE, matrice O-D.", "fait"],
    ["E. Temps réel", "Collecteur GBFS (60 s) fonctionnel, historique enregistré ; tables Eventhouse + 7 requêtes "
     "KQL (dont fenêtres 15 min et glissantes) écrites. Eventstream à brancher.", "pret"],
    ["F. Power BI", "Plan de 10 pages (une question par page), mesures DAX, thème et données Gold exportés. "
     "Fichier .pbix à construire.", "afaire"],
    ["G. Sécurité & gouvernance", "3 personas RBAC, Key Vault, 3 Azure Policies (régions, tag, accès public), "
     "Purview, rétention, audit, budget : documentés et scriptés. Démonstration du refus 403 à faire.", "pret"],
    ["H. Ingénierie", "Diagramme d'architecture, README, dépôt Git, conventions de nommage, CI GitHub Actions, "
     "tests pytest, Docker, script de nettoyage.", "fait"],
    ["I. Machine Learning", "7 algorithmes comparés à une baseline, split chronologique, MLflow, promotion "
     "champion / challenger, prévisions en table Gold.", "fait"],
    ["Bonus", "API FastAPI + application Streamlit, alertes (qualité / opérationnelles / modèle), tournées camion "
     "optimisées (OR-Tools), backtest des alertes sur l'historique réel.", "fait"],
]
table(["Exigence", "Ce qui est réalisé", "Statut"], rows, [3.6, 11.0, 3.0], status_col=2)
para("FAIT = exécuté sur les vraies données · PRÊT À DÉPLOYER = code écrit et testé en local, reste à l'exécuter "
     "dans Azure / Fabric · À FAIRE = pas encore commencé.", italic=True, size=8.5, color=GREY)

# ------------------------------------------------------------------ architecture
doc.add_heading("3. Architecture cible", level=1)
figure(str(ROOT / "docs" / "architecture.png"), 17, "Chaîne Collect → Ingest → Store → Process → Analyze → "
       "Visualize → Secure & Govern.")

# ------------------------------------------------------------------ results
doc.add_heading("4. Premiers résultats sur les données réelles", level=1)
weekday = daily[pd.to_datetime(daily["date"]).dt.day_of_week < 5]
table(["Indicateur", "Valeur"], [
    ["Trajets bruts → valides", f"{fmt(raw_trips)} → {fmt(valid)}"],
    ["Période couverte", f"{days} jours (août → octobre 2026)"],
    ["Trajets par jour (moyenne / jours ouvrés)",
     f"{fmt(daily['trips'].mean())} / {fmt(weekday['trips'].mean())}" if "trips" in daily else "—"],
    ["Stations actives", fmt(n_stations)],
], [8.5, 8.0])
figure("fig1_daily_trips.png", 13.5, "Trajets par jour (notebook 02).")

if cmp_ is not None:
    doc.add_heading("Machine Learning : comparaison des modèles", level=2)
    para("Prévision du nombre de départs et d'arrivées par station et par heure. Les modèles sont comparés sur une "
         "semaine de validation ; la semaine de test ne sert qu'une fois, au modèle retenu. Critère : déviance de "
         "Poisson (plus bas = meilleur).")
    d = cmp_[cmp_.target == "departures"].sort_values("val_poisson_dev")
    rows_ml, sel = [], set()
    for k, r in enumerate(d.itertuples()):
        rows_ml.append([str(r.rank), MODEL_LABELS.get(r.model, r.model) + ("  ✓ retenu" if r.selected else ""),
                        f"{r.val_poisson_dev:.4f}", f"{r.val_mae:.3f}", f"{r.val_rmse:.3f}",
                        f"{r.val_dev_gain_vs_baseline_pct:+.1f} %"])
        if r.selected:
            sel.add(k)
    table(["Rang", "Modèle (cible : départs)", "Déviance", "MAE", "RMSE", "Gain vs baseline"], rows_ml,
          [1.3, 6.5, 2.3, 2.0, 2.0, 3.0], bold_rows=sel)
    figure("fig11_model_comparison.png", 16.5, "Gain de chaque algorithme sur la baseline (départs et arrivées).")

if promo is not None:
    lines = []
    for r in promo.itertuples():
        cible = "départs" if "departures" in r.model else "arrivées"
        lines.append(f"{cible} : {MODEL_LABELS.get(r.algorithm, r.algorithm)} "
                     f"{'promu champion' if r.promoted else 'non promu (le champion en place reste meilleur)'}")
    para("Promotion MLOps (MLflow, même semaine de test) — " + " ; ".join(lines) + ".")

figure("fig6_priority_states.png", 11, "État des stations sur un relevé GBFS réel et priorités d'intervention "
       "(notebook 04).")

# ------------------------------------------------------------------ deliverables + next steps
doc.add_page_break()
doc.add_heading("5. Livrables", level=1)
table(["Livrable", "État", "Statut"], [
    ["1. Proposition (2-3 pages)", "docs/proposition.md rédigée", "fait"],
    ["2. Schéma d'architecture", "docs/architecture.png", "fait"],
    ["3. Dépôt Git + README", "structure, code, tests, CI, instructions de reproduction", "fait"],
    ["4. Rapport technique (PDF)", "version provisoire générée à partir des résultats ; à compléter avec les "
     "captures du déploiement cloud", "pret"],
    ["5. Rapport Power BI", "plan, mesures DAX et données prêts ; .pbix à construire", "afaire"],
    ["6. Démo 10-15 min", "script de démonstration écrit (docs/demo_script.md) ; vidéo de secours à enregistrer",
     "afaire"],
    ["7. Inventaire des ressources", "modèle prêt (docs/cost_and_inventory.md), à remplir après déploiement", "pret"],
], [4.5, 10.0, 3.1], status_col=2)

doc.add_heading("6. Prochaines étapes", level=1)
for i, s in enumerate([
    "Azure : exécuter infra/deploy.ps1 (groupe de ressources, ADLS, Key Vault, Policies, rôles RBAC, budget).",
    "Fabric : créer l'espace de travail et le Lakehouse, importer les notebooks, lancer le pipeline batch.",
    "Temps réel : créer Eventstream + Eventhouse, lancer le collecteur GBFS, exécuter les requêtes KQL.",
    "Power BI : construire le rapport sur les tables Gold et le publier.",
    "Sécurité : démontrer le refus d'accès du compte Analyst (403) et une Azure Policy qui bloque.",
    "Démo : répétition, enregistrement de la vidéo de secours, inventaire et nettoyage des ressources.",
], 1):
    bullet(" " + s, f"{i}.")

# ------------------------------------------------------------------ footer + save
footer = doc.sections[0].footer.paragraphs[0]
footer.text = f"SmartBike AI · état d'avancement au {today:%d/%m/%Y}"
footer.runs[0].font.size, footer.runs[0].font.color.rgb = Pt(8), GREY
doc.save(OUT)
print(f"written {OUT}")

if sys.platform == "win32" and "--no-pdf" not in sys.argv:
    pdf = OUT.with_suffix(".pdf")
    ps = (f"$w = New-Object -ComObject Word.Application; $w.Visible = $false; "
          f"$d = $w.Documents.Open('{OUT}'); $d.SaveAs([ref]'{pdf}', [ref]17); $d.Close(); $w.Quit()")
    res = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=300)
    print(f"written {pdf}" if pdf.exists() else f"[warn] PDF export failed: {res.stderr[-400:]}")
