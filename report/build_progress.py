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
    "encours": ("EN COURS", "FFF1C2"),
    "pret": ("PRÊT", "DCE9FA"),
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

# ------------------------------------------------------------------ status of the project (2 pages, for the professor)
SHOTS = ROOT / "docs" / "screenshots"   # cloud screenshots (gitignored: they show e-mail / subscription id)
SCORE = {"fait": 1.0, "encours": 0.6, "pret": 0.4, "afaire": 0.0}
PREVIOUS = 45   # progress announced at the previous checkpoint (before the cloud deployment)

requirements = [
    ["A. Problème & sources", "3 sources hétérogènes : archives CSV Citi Bike, API JSON GBFS (temps réel), "
     "API Open-Meteo. Dictionnaire de données.", "fait"],
    ["B. Ingestion batch", "Données brutes chargées dans ADLS Gen2 (167 fichiers, 2,8 Go). Pipeline Data Factory "
     "défini en JSON, à créer dans Fabric.", "encours"],
    ["C. Stockage", "Déployé : ADLS Gen2 sécurisé (conteneur landing) + Lakehouse lh_smartbike relié par un "
     "raccourci « bronze » (zéro copie).", "fait"],
    ["D. Spark → Delta", f"Exécuté en local sur les vraies données ({valid / raw_trips:.2%} de lignes valides). "
     "Notebooks et environnement Spark déployés dans Fabric, exécution en cours.", "encours"],
    ["E. Temps réel", "Collecteur GBFS fonctionnel, 7 requêtes KQL écrites. Eventstream + Eventhouse à créer.",
     "pret"],
    ["F. Power BI", "Plan de 10 pages, mesures DAX, thème prêts. Fichier .pbix à construire.", "afaire"],
    ["G. Sécurité & gouvernance", "Déployé et testé : 3 Azure Policies (refus vérifiés), RBAC 3 personas, "
     "Key Vault RBAC, audit Log Analytics. Démo du refus 403 à enregistrer.", "encours"],
    ["H. Ingénierie", "Architecture, README, Git, CI GitHub Actions (verte), tests, Docker, scripts "
     "de déploiement / nettoyage.", "fait"],
    ["I. Machine Learning", "7 algorithmes comparés à une baseline, MLflow, champion / challenger.", "fait"],
    ["Bonus", "API FastAPI + app Streamlit, alertes, tournées optimisées (OR-Tools).", "fait"],
]
deliverables = [
    ["1. Proposition", "rédigée", "fait"],
    ["2. Schéma d'architecture", "docs/architecture.png", "fait"],
    ["3. Dépôt Git + README", "github.com/Rania-Bouaroua/SmartBike-AI", "fait"],
    ["4. Rapport technique", "version provisoire ; captures cloud en cours d'ajout", "pret"],
    ["5. Rapport Power BI", "à construire", "afaire"],
    ["6. Démo 10-15 min", "script écrit ; vidéo à enregistrer", "afaire"],
    ["7. Inventaire des ressources", "ressources Azure / Fabric réelles connues, à reporter", "pret"],
]
items = [r[2] for r in requirements] + [r[2] for r in deliverables]
progress = round(100 * sum(SCORE[s] for s in items) / len(items))

# ------------------------------------------------------------------ title block
logo = ROOT / "assets" / "logo_full.png"
if logo.exists():
    doc.add_picture(str(logo), width=Cm(5.5))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
para("SmartBike AI — État d'avancement", bold=True, size=18, color=NAVY, align=WD_ALIGN_PARAGRAPH.CENTER)
para("Design of Cloud Data-Driven Applications · 2026-2027 · Pr. Ibrahim RAHHAL", size=10, color=GREY,
     align=WD_ALIGN_PARAGRAPH.CENTER)
para(f"Point au {today:%d/%m/%Y} · " + ", ".join(TEAM), size=9.5, align=WD_ALIGN_PARAGRAPH.CENTER)

# progress bar: one row, the filled part proportional to the score
para(f"Avancement global : {PREVIOUS} % au point précédent → {progress} % aujourd'hui", bold=True, size=14,
     color=BLUE, align=WD_ALIGN_PARAGRAPH.CENTER)
bar = doc.add_table(rows=1, cols=2)
bar.alignment = WD_TABLE_ALIGNMENT.CENTER
for cell, w, fill in [(bar.rows[0].cells[0], 15 * progress / 100, "2A78D6"),
                      (bar.rows[0].cells[1], 15 * (100 - progress) / 100, "E3E8EF")]:
    cell.width = Cm(w)
    shade(cell, fill)
    cell.paragraphs[0].add_run(" ").font.size = Pt(4)
para("Calcul : exigences du cahier des charges + livrables ; FAIT = 1, EN COURS = 0,6, PRÊT = 0,4, À FAIRE = 0.",
     italic=True, size=8, color=GREY, align=WD_ALIGN_PARAGRAPH.CENTER)

doc.add_heading("1. En bref", level=1)
para("Sujet : anticiper les stations Citi Bike (New York) vides ou saturées à partir des trajets réels, du temps "
     "réel GBFS et de la météo, et indiquer à l'opérateur où intervenir.")
bullet(f" chaîne complète exécutée sur les vraies données : {fmt(valid)} trajets valides, {days} jours, "
       f"{n_stations} stations, médaillon Bronze → Silver → Gold, 7 modèles ML comparés, alertes, application.",
       "Données & ML :")
bullet(" Azure déployé (Germany West Central) : ADLS Gen2, Key Vault, Log Analytics, 3 Azure Policies testées, "
       "rôles Admin / Data Engineer / Analyst attribués. Fabric : workspace, Lakehouse, environnement Spark, "
       "5 notebooks, raccourci vers les données brutes.", "Cloud (nouveau) :")
bullet(" exécution des notebooks dans Fabric, temps réel (Eventstream / KQL), Power BI, démo et vidéo.",
       "Reste à faire :")

doc.add_heading("2. Avancement par exigence", level=1)
table(["Exigence", "Réalisé", "Statut"], requirements, [3.6, 11.2, 2.8], status_col=2, size=8)

# ------------------------------------------------------------------ cloud proof (screenshots)
shots = [("01_rg_ressources.png", "Ressources Azure (groupe rg-smartbike-dev)"),
         ("05_policies.png", "Azure Policies : 100 % conformes"),
         ("09_fabric_workspace.png", "Workspace Fabric déployé"),
         ("06_iam.png", "Rôles RBAC (moindre privilège)")]
shots = [(f, c) for f, c in shots if (SHOTS / f).exists()]
if shots:
    doc.add_heading("3. Preuves du déploiement cloud", level=1)
    grid = doc.add_table(rows=(len(shots) + 1) // 2, cols=2)
    grid.alignment = WD_TABLE_ALIGNMENT.CENTER
    for k, (f, cap) in enumerate(shots):
        cell = grid.rows[k // 2].cells[k % 2]
        cell.width = Cm(8.7)
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.add_run().add_picture(str(SHOTS / f), width=Cm(8.4))
        r = cell.add_paragraph().add_run(cap)
        r.italic, r.font.size, r.font.color.rgb = True, Pt(8), GREY
        cell.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER

doc.add_heading("4. Livrables", level=1)
table(["Livrable", "État", "Statut"], deliverables, [4.5, 10.3, 2.8], status_col=2, size=8)

doc.add_heading("5. Prochaines étapes", level=1)
for i, s in enumerate([
    "Exécuter les notebooks 01 → 05 dans Fabric et créer le pipeline batch planifié.",
    "Temps réel : Eventstream + Eventhouse, collecteur GBFS, requêtes KQL et alerte.",
    "Power BI : construire et publier le rapport sur les tables Gold.",
    "Sécurité : enregistrer le refus 403 du compte Analyst.",
    "Finaliser le rapport technique, répéter la démo, enregistrer la vidéo de secours.",
], 1):
    bullet(" " + s, f"{i}.")
para("FAIT = exécuté · EN COURS = déployé, en cours d'exécution · PRÊT = code écrit et testé, reste à déployer · "
     "À FAIRE = pas commencé.", italic=True, size=8, color=GREY)

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
