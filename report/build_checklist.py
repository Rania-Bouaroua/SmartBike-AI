"""Builds the task sheet of the project: what is done, what is left (python report/build_checklist.py).

-> report/SmartBike_AI_Fiche_Avancement.docx (+ .pdf through Word on Windows)
Update DONE / TODO by hand as the project moves on.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import subprocess
import sys

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "report" / "SmartBike_AI_Fiche_Avancement.docx"
TEAM = ["Rania BOUAROUA", "Salma BENMOUMA", "Oumaima GASSI", "Leonce Pharell Sandy THEUREAU MENDOME"]
NAVY, BLUE, GREY = RGBColor(0x16, 0x32, 0x4F), RGBColor(0x2A, 0x78, 0xD6), RGBColor(0x52, 0x51, 0x4E)

# (domain, task, detail)
DONE = [
    ("Données", "Collecte des 3 sources", "Trajets Citi Bike août → octobre 2026 (15 M), API GBFS temps réel, "
     "météo Open-Meteo"),
    ("Données", "Chaîne Bronze → Silver → Gold", "Notebooks 01, 02, 05 exécutés en local : contrôles qualité, "
     "réconciliation des stations, jointure météo, agrégats, fenêtres, CUBE, matrice O-D"),
    ("ML", "Prévision de la demande", "7 algorithmes comparés à une baseline, MLflow, Extra Trees promu champion"),
    ("ML", "Priorités et tournées", "Notebook 04 : risque vide / saturée, vélos à déplacer, tournées OR-Tools"),
    ("Bonus", "Application et API", "API FastAPI + application Streamlit, alertes, backtest des alertes"),
    ("Ingénierie", "Dépôt GitHub", "Code, README, CI GitHub Actions verte, 39 tests, Docker, schéma d'architecture"),
    ("Azure", "Ressources déployées", "Groupe rg-smartbike-dev (Germany West Central) : ADLS Gen2 sécurisé, "
     "Key Vault (RBAC), Log Analytics + journaux d'audit"),
    ("Azure", "Gouvernance", "3 Azure Policies (région, tag obligatoire, stockage non public) : refus testés"),
    ("Azure", "Rôles (moindre privilège)", "Rania = Admin, Salma = Data Engineer, Oumaima = Analyst (lecture seule)"),
    ("Fabric", "Workspace et éléments", "ws-smartbike-dev : Lakehouse lh_smartbike, environnement Spark "
     "env_smartbike publié, 5 notebooks déployés par l'API"),
    ("Fabric", "Données brutes dans le cloud", "167 fichiers (2,8 Go) chargés dans ADLS, visibles dans le "
     "Lakehouse via le raccourci « bronze »"),
    ("Rapport", "Suivi", "Note d'avancement (45 % → 65 %), captures du déploiement, rapport technique provisoire"),
]

# (domain, task, detail, priority)
TODO = [
    ("Fabric", "Exécuter les notebooks 01 → 05", "Vérifier les tables silver_* et gold_* dans le Lakehouse "
     "(en cours : nb_01)", "Haute"),
    ("Fabric", "Pipeline batch", "Créer pl_smartbike_batch (copie + notebooks) et le planifier", "Haute"),
    ("Temps réel", "Eventstream + Eventhouse", "Point d'entrée Eventstream, base KQL, chaîne de connexion dans "
     "le Key Vault", "Haute"),
    ("Temps réel", "Collecteur + requêtes KQL", "Lancer le collecteur GBFS au moins 24 h avant la démo, exécuter "
     "les 7 requêtes KQL, tableau temps réel et alerte", "Haute"),
    ("Power BI", "Rapport .pbix", "Construire les 10 pages sur les tables Gold (mesures DAX et thème prêts), "
     "publier dans le workspace", "Haute"),
    ("Sécurité", "Accès Fabric", "Ajouter Salma (Contributor) et Oumaima (Viewer) au workspace", "Moyenne"),
    ("Sécurité", "Démo du refus 403", "Compte d'Oumaima : accès refusé au stockage et aux secrets", "Moyenne"),
    ("Rapport", "Rapport technique final", "Ajouter les captures cloud, l'inventaire des ressources et les coûts",
     "Moyenne"),
    ("Démo", "Préparer la soutenance", "Répartition des 4 membres, répétition, vidéo de secours", "Moyenne"),
    ("Fin", "Nettoyage", "Après la soutenance : infra/cleanup.ps1 (suppression des ressources Azure)", "Basse"),
]
PRIO_FILL = {"Haute": "FDE2E1", "Moyenne": "FFF1C2", "Basse": "E3E8EF"}

doc = Document()
for s in doc.sections:
    s.left_margin = s.right_margin = Cm(1.6)
    s.top_margin = s.bottom_margin = Cm(1.4)
st = doc.styles["Normal"]
st.font.name, st.font.size = "Calibri", Pt(10)
st.element.rPr.rFonts.set(qn("w:eastAsia"), "Calibri")
st.paragraph_format.space_after = Pt(3)
h1 = doc.styles["Heading 1"]
h1.font.name, h1.font.size, h1.font.color.rgb = "Calibri", Pt(14), NAVY
h1.paragraph_format.space_before, h1.paragraph_format.space_after = Pt(10), Pt(4)


def shade(cell, fill: str) -> None:
    shd = OxmlElement("w:shd")
    for k, v in (("w:val", "clear"), ("w:color", "auto"), ("w:fill", fill)):
        shd.set(qn(k), v)
    cell._tc.get_or_add_tcPr().append(shd)


def para(text, bold=False, size=None, color=None, italic=False, center=False):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.bold, r.italic = bold, italic
    if size:
        r.font.size = Pt(size)
    if color:
        r.font.color.rgb = color
    if center:
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    return p


def table(header, rows, widths, fills=None):
    tb = doc.add_table(rows=1, cols=len(header))
    tb.style = "Table Grid"
    tb.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, h in enumerate(header):
        c = tb.rows[0].cells[i]
        r = c.paragraphs[0].add_run(h)
        r.bold, r.font.size, r.font.color.rgb = True, Pt(9), RGBColor(0xFF, 0xFF, 0xFF)
        shade(c, "16324F")
    for row in rows:
        cells = tb.add_row().cells
        for i, v in enumerate(row):
            r = cells[i].paragraphs[0].add_run(v)
            r.font.size = Pt(8.5)
            if i == 1:
                r.bold = True
            if fills and fills.get(i, {}).get(v):
                shade(cells[i], fills[i][v])
    for row in tb.rows:
        for i, w in enumerate(widths):
            row.cells[i].width = Cm(w)
    return tb


today = dt.date.today()
para("SmartBike AI — Fiche d'avancement", bold=True, size=18, color=NAVY, center=True)
para(f"Design of Cloud Data-Driven Applications · 2026-2027 · Pr. Ibrahim RAHHAL · au {today:%d/%m/%Y}",
     size=9.5, color=GREY, center=True)
para(", ".join(TEAM), size=9.5, center=True)
para(f"{len(DONE)} tâches terminées · {len(TODO)} tâches restantes "
     f"({sum(t[3] == 'Haute' for t in TODO)} prioritaires)", bold=True, size=11, color=BLUE, center=True)

doc.add_heading("1. Tâches terminées", level=1)
table(["✓", "Tâche", "Détail", "Domaine"], [["✓", t, d, dom] for dom, t, d in DONE], [0.8, 4.2, 10.4, 2.4],
      fills={0: {"✓": "D7F0E5"}})

doc.add_heading("2. Tâches à faire", level=1)
table(["#", "Tâche", "Détail", "Priorité", "Responsable"],
      [[str(i), t, d, p, ""] for i, (dom, t, d, p) in enumerate(TODO, 1)], [0.8, 4.0, 8.6, 1.8, 2.6],
      fills={3: PRIO_FILL})
para("Ordre conseillé : Fabric → temps réel → Power BI → sécurité → rapport et démo. "
     "La colonne « Responsable » est à remplir par le groupe.", italic=True, size=8.5, color=GREY)

footer = doc.sections[0].footer.paragraphs[0]
footer.text = f"SmartBike AI · fiche d'avancement au {today:%d/%m/%Y}"
footer.runs[0].font.size, footer.runs[0].font.color.rgb = Pt(8), GREY
doc.save(OUT)
print(f"written {OUT}")

if sys.platform == "win32" and "--no-pdf" not in sys.argv:
    pdf = OUT.with_suffix(".pdf")
    ps = (f"$w = New-Object -ComObject Word.Application; $w.Visible = $false; "
          f"$d = $w.Documents.Open('{OUT}'); $d.SaveAs([ref]'{pdf}', [ref]17); $d.Close(); $w.Quit()")
    res = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=300)
    print(f"written {pdf}" if pdf.exists() else f"[warn] PDF export failed: {res.stderr[-400:]}")
