"""Builds the technical report (deliverable 4) from the repository documentation and the real results.

    python docs/make_results.py          # refresh numbers + figures first
    python report/build_report.py        # -> report/SmartBike_AI_Rapport_Technique.docx (+ .pdf if Word is installed)

The report follows the structure suggested in the course brief (11 sections). Its content is assembled from the same
Markdown files as the repository (single source of truth): editing a doc and re-running this script updates the report.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import re
import subprocess
import sys

from docx import Document
from docx.enum.section import WD_ORIENT  # noqa: F401
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "report" / "SmartBike_AI_Rapport_Technique.docx"
NAVY, BLUE, GREY = RGBColor(0x16, 0x32, 0x4F), RGBColor(0x2A, 0x78, 0xD6), RGBColor(0x52, 0x51, 0x4E)

doc = Document()
for s in doc.sections:
    s.left_margin = s.right_margin = Cm(2.0)
    s.top_margin = s.bottom_margin = Cm(1.8)
st = doc.styles["Normal"]
st.font.name, st.font.size = "Calibri", Pt(10)
st.element.rPr.rFonts.set(qn("w:eastAsia"), "Calibri")
for lvl, size in [(1, 16), (2, 13), (3, 11.5), (4, 10.5)]:
    h = doc.styles[f"Heading {lvl}"]
    h.font.name, h.font.size, h.font.color.rgb, h.font.bold = "Calibri", Pt(size), NAVY if lvl < 3 else BLUE, True


# ------------------------------------------------------------------ inline Markdown -> runs
INLINE = re.compile(r"(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*|\[[^\]]+\]\([^)]+\))")


def add_inline(par, text: str, size: float | None = None) -> None:
    for part in INLINE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            r = par.add_run(part[2:-2])
            r.bold = True
        elif part.startswith("`") and part.endswith("`"):
            r = par.add_run(part[1:-1])
            r.font.name = "Consolas"
            r.font.size = Pt((size or 10) - 1)
            r.font.color.rgb = RGBColor(0x18, 0x4F, 0x95)
        elif part.startswith("[") and "](" in part:
            r = par.add_run(part[1:part.index("](")])
            r.font.color.rgb = BLUE
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            r = par.add_run(part[1:-1])
            r.italic = True
        else:
            r = par.add_run(part)
        if size:
            r.font.size = Pt(size)


def shade(cell, hex_color: str) -> None:
    tc = cell._tc.get_or_add_tcPr()
    sh = OxmlElement("w:shd")
    sh.set(qn("w:val"), "clear")
    sh.set(qn("w:color"), "auto")
    sh.set(qn("w:fill"), hex_color)
    tc.append(sh)


def add_table(rows: list[list[str]]) -> None:
    ncol = max(len(r) for r in rows)
    t = doc.add_table(rows=len(rows), cols=ncol)
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    size = 8 if ncol >= 5 else 8.5
    for i, row in enumerate(rows):
        for j in range(ncol):
            cell = t.cell(i, j)
            cell.text = ""
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            add_inline(p, row[j] if j < len(row) else "", size)
            if i == 0:
                shade(cell, "16324F")
                for r in p.runs:
                    r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
                    r.bold = True
            elif i % 2 == 0:
                shade(cell, "EEF3F9")
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def add_image(path: pathlib.Path, width_cm: float = 16.5, caption: str | None = None) -> None:
    if not path.exists():
        doc.add_paragraph(f"[figure manquante : {path.name}]")
        return
    doc.add_picture(str(path), width=Cm(width_cm))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    if caption:
        c = doc.add_paragraph(caption)
        c.alignment = WD_ALIGN_PARAGRAPH.CENTER
        c.runs[0].italic = True
        c.runs[0].font.size = Pt(8.5)
        c.runs[0].font.color.rgb = GREY


# ------------------------------------------------------------------ block Markdown renderer
def render_md(path: pathlib.Path, shift: int = 1, skip_title: bool = True, drop_sections: tuple[str, ...] = ()) -> None:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    # with drop_sections, only the kept "##" sections are wanted: skip the file preamble too
    i, table, code, skipping = 0, [], None, bool(drop_sections)
    base = path.parent

    def flush_table():
        nonlocal table
        if table:
            rows = [[c.strip() for c in r.strip().strip("|").split("|")] for r in table
                    if not re.match(r"^\s*\|?\s*:?-{2,}", r)]
            add_table(rows)
            table = []

    while i < len(lines):
        ln = lines[i]
        i += 1
        if code is not None:
            if ln.strip().startswith("```"):
                p = doc.add_paragraph()
                r = p.add_run("\n".join(code))
                r.font.name, r.font.size = "Consolas", Pt(8)
                p.paragraph_format.left_indent = Cm(0.5)
                code = None
            else:
                code.append(ln)
            continue
        m = re.match(r"^(#{1,6})\s+(.*)", ln)
        if m:
            flush_table()
            level, title = len(m.group(1)), m.group(2).strip()
            if skip_title and level == 1:      # document title: keep the preamble state unchanged
                continue
            skipping = any(title.lower().startswith(d.lower()) for d in drop_sections)
            if skipping:
                continue
            doc.add_heading(re.sub(r"[*`]", "", title), level=min(level + shift - 1, 4))
            continue
        if skipping:
            continue
        if ln.strip().startswith("```"):
            flush_table()
            code = []
            continue
        if ln.strip().startswith("|"):
            table.append(ln)
            continue
        flush_table()
        img = re.match(r"^!\[[^\]]*\]\(([^)]+)\)", ln.strip())
        if img:
            add_image((base / img.group(1)).resolve())
            continue
        if not ln.strip() or ln.strip().startswith("<!--"):
            continue
        if ln.strip().startswith(">"):
            p = doc.add_paragraph()
            add_inline(p, ln.strip().lstrip("> "))
            p.paragraph_format.left_indent = Cm(0.6)
            for r in p.runs:
                r.font.color.rgb = GREY
            continue
        b = re.match(r"^(\s*)[*-]\s+(.*)", ln)
        n = re.match(r"^(\s*)\d+\.\s+(.*)", ln)
        if b or n:
            mm = b or n
            body = mm.group(2)
            while i < len(lines) and lines[i].startswith("  ") and not re.match(r"^\s*([*-]|\d+\.)\s", lines[i]) \
                    and not lines[i].strip().startswith("```"):
                body += " " + lines[i].strip()
                i += 1
            p = doc.add_paragraph(style="List Bullet" if b else "List Number")
            if len(mm.group(1)) >= 2:
                p.paragraph_format.left_indent = Cm(1.2)
            add_inline(p, body)
            p.paragraph_format.space_after = Pt(1)
            continue
        body = ln.strip()
        while i < len(lines) and lines[i].strip() and not re.match(r"^\s*(#|\||[*-]\s|\d+\.\s|```|!\[|>)", lines[i]):
            body += " " + lines[i].strip()
            i += 1
        p = doc.add_paragraph()
        add_inline(p, body)
    flush_table()


def para(text: str, italic: bool = False) -> None:
    p = doc.add_paragraph()
    add_inline(p, text)
    if italic:
        for r in p.runs:
            r.italic = True


def page_break() -> None:
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


# ------------------------------------------------------------------ cover
for _ in range(5):
    doc.add_paragraph()
p = doc.add_paragraph("DESIGN OF CLOUD DATA-DRIVEN APPLICATIONS · 2026/2027 · Pr. Ibrahim RAHHAL")
p.runs[0].font.size, p.runs[0].font.color.rgb, p.runs[0].bold = Pt(10), BLUE, True
p = doc.add_paragraph("SmartBike AI")
p.runs[0].font.size, p.runs[0].font.color.rgb, p.runs[0].bold = Pt(36), NAVY, True
p = doc.add_paragraph("Anticiper le manque de vélos et de places dans les stations de vélos partagés")
p.runs[0].font.size, p.runs[0].font.color.rgb = Pt(15), NAVY
p = doc.add_paragraph("Rapport technique — plateforme cloud de bout en bout sur Microsoft Azure et Microsoft Fabric, "
                      "à partir des données réelles de Citi Bike New York (août → octobre 2026)")
p.runs[0].font.size, p.runs[0].font.color.rgb = Pt(11), GREY
doc.add_paragraph()
add_table([["Élément", "Valeur"],
           ["Équipe", "à compléter : noms et responsabilités de chaque membre"],
           ["Plateforme", "Azure (ADLS Gen2, Key Vault, Policy, Log Analytics) · Fabric (Data Factory, Lakehouse, Spark, "
                          "Eventstream, Eventhouse/KQL, MLflow, Power BI)"],
           ["Dépôt", "SmartBike-AI (Git) — notebooks, scripts, KQL, pipeline, sécurité, application, tests, CI"],
           ["Date", dt.date.today().strftime("%d/%m/%Y")]])
page_break()

# ------------------------------------------------------------------ table of contents (Word field)
p = doc.add_paragraph("Sommaire")
p.runs[0].font.size, p.runs[0].font.color.rgb, p.runs[0].bold = Pt(16), NAVY, True
p = doc.add_paragraph()
r = p.add_run()
for tag, txt in [("begin", None), (None, 'TOC \\o "1-2" \\h \\z \\u'), ("separate", None)]:
    if tag:
        f = OxmlElement("w:fldChar")
        f.set(qn("w:fldCharType"), tag)
        r._r.append(f)
    else:
        it = OxmlElement("w:instrText")
        it.set(qn("xml:space"), "preserve")
        it.text = txt
        r._r.append(it)
r2 = p.add_run("(clic droit → Mettre à jour les champs pour afficher le sommaire)")
r2.italic = True
f = OxmlElement("w:fldChar")
f.set(qn("w:fldCharType"), "end")
r2._r.append(f)
page_break()

D = ROOT / "docs"
# 1 ------------------------------------------------------------------
doc.add_heading("1. Résumé exécutif et problème", level=1)
render_md(D / "proposition.md", shift=2, drop_sections=("2. Utilisateurs", "3. Sources", "4. Architecture",
                                                         "5. Choix", "6. Critères", "7. Risques", "8. Planning"))
results = (D / "results.md").read_text(encoding="utf-8")
first = re.search(r"Trajets bruts : \*\*([\d  ]+)\*\*.*?valides après contrôles : \*\*([\d  ]+)\*\*", results)
para("**Ce qui a été réalisé.** Une chaîne complète *Collecter → Ingérer → Stocker → Traiter → Analyser → Prévoir → "
     "Visualiser → Sécuriser* : 3 sources hétérogènes (archives CSV, API JSON temps réel, API météo), un pipeline batch "
     "rejouable, un Lakehouse en médaillon (Bronze/Silver/Gold, Delta), 5 notebooks Spark, un flux temps réel Eventstream → "
     "Eventhouse avec 11 requêtes KQL et 3 règles d'alerte, un modèle de prévision suivi dans MLflow avec promotion "
     "champion/challenger, un rapport Power BI de 8 pages, une API REST et une application opérateur, des contrôles de "
     "sécurité (RBAC 3 personas, Key Vault, 3 Azure Policies, classification Purview), une CI GitHub Actions et des tests."
     + (f" Le traitement porte sur **{first.group(1)}** trajets réels, dont **{first.group(2)}** valides." if first else ""))
# 2 ------------------------------------------------------------------
doc.add_heading("2. Exigences fonctionnelles et utilisateurs", level=1)
render_md(D / "proposition.md", shift=2, drop_sections=("1. Problème", "3. Sources", "4. Architecture", "5. Choix",
                                                         "7. Risques", "8. Planning"))
doc.add_heading("Questions auxquelles le tableau de bord répond", level=2)
for q in ["Quelles stations sont vides, presque vides ou proches de la saturation en ce moment ?",
          "Quelles stations et quels horaires concentrent le plus de trajets ?",
          "Où la demande prévue justifie-t-elle une intervention prioritaire, et combien de vélos déplacer ?",
          "La météo et le jour de la semaine modifient-ils les volumes observés ?",
          "Combien de vélos les camions doivent-ils déplacer chaque jour, et dans quelles stations ?"]:
    doc.add_paragraph(q, style="List Number")
# 3 ------------------------------------------------------------------
page_break()
doc.add_heading("3. Sources de données, qualité et dictionnaire", level=1)
render_md(D / "proposition.md", shift=2, drop_sections=("1. Problème", "2. Utilisateurs", "4. Architecture", "5. Choix",
                                                         "6. Critères", "7. Risques", "8. Planning"))
render_md(D / "data_dictionary.md", shift=2)
doc.add_heading("Résultats des contrôles qualité", level=2)
sec = results.split("## 1.")[1].split("## 2.")[0]
tmp = ROOT / "report" / "_tmp.md"
tmp.write_text("# x\n" + sec.split("\n", 1)[1], encoding="utf-8")
render_md(tmp, shift=2)
# 4 ------------------------------------------------------------------
page_break()
doc.add_heading("4. Architecture et choix technologiques", level=1)
add_image(D / "architecture.png", 17, "Figure 1 — Architecture de bout en bout (sources, ingestion, stockage, traitement, "
                                      "temps réel, restitution, sécurité transverse)")
render_md(D / "proposition.md", shift=2, drop_sections=("1. Problème", "2. Utilisateurs", "3. Sources", "6. Critères",
                                                         "7. Risques", "8. Planning"))
doc.add_heading("Pourquoi ce service et pas un autre ?", level=2)
demo = (D / "demo_script.md").read_text(encoding="utf-8")
qa = demo.split("## Questions probables du jury")[1]
tmp.write_text("# x\n" + qa.split("\n", 1)[1], encoding="utf-8")
render_md(tmp, shift=2)
render_md(D / "cost_and_inventory.md", shift=2, drop_sections=("Inventaire", "Principaux postes", "Si les exigences"))
# 5 ------------------------------------------------------------------
page_break()
doc.add_heading("5. Ingestion batch et traitement Spark", level=1)
render_md(ROOT / "pipeline" / "README.md", shift=2)
doc.add_heading("Notebooks Spark (médaillon)", level=2)
add_table([["Notebook", "Entrée → sortie", "Traitements principaux"],
           ["01 Bronze → Silver", "CSV, JSON GBFS, JSON météo → silver_trips, silver_stations, silver_weather_hourly, "
                                  "gold_data_quality", "schéma explicite (ID en texte), typage, dédoublonnage par "
                                                       "ride_id (fenêtre), 7 contrôles qualité motivés, réconciliation des "
                                                       "ID stations (short_name GBFS), explosion des tableaux météo"],
           ["02 Silver → Gold", "Silver → gold_station_hourly, gold_daily_kpis, gold_station_profile, "
                                "gold_hourly_pattern, gold_weather_effect",
            "grille complète station × heure (zéros explicites), jointure météo, KPI journaliers, rôle source/puits "
            "au pic du matin, heatmap, effet météo"],
           ["03 ML", "gold_station_hourly → gold_demand_forecast, gold_ml_metrics, encodages, journal de promotion",
            "split chronologique, encodages calculés sur l'entraînement seul, régression Poisson, MLflow, registre, "
            "champion/challenger"],
           ["04 Priorités", "GBFS live + prévision météo + modèle champion → gold_station_priority",
            "projection du stock à 2 h, risque, vélos à déplacer, score de priorité"],
           ["05 Agrégations avancées", "Silver/Gold → 9 tables Gold",
            "fenêtres glissantes 7 j, lag J-1/S-1, rangs, somme cumulée (rééquilibrage), CUBE + grouping_id, "
            "percentiles p50/p90/p99, ntile, matrice O-D + haversine, grille spatiale ~1 km, histogrammes cumulés"]])
for title, a, b in [("Usage", "## 2.", "## 3."), ("Stations", "## 3.", "## 4."), ("Effet de la météo", "## 4.", "## 5."),
                    ("Agrégations avancées (notebook 05)", "## 7.", "## 8.")]:
    if a in results:
        doc.add_heading(title, level=2)
        body = results.split(a, 1)[1]
        body = body.split(b, 1)[0] if b in body else body
        tmp.write_text("# x\n" + body.split("\n", 1)[1], encoding="utf-8")
        render_md(tmp, shift=2)
# 6 ------------------------------------------------------------------
page_break()
doc.add_heading("6. Architecture temps réel et analyse KQL", level=1)
para("Un collecteur Python (`scripts/gbfs_collector.py`) interroge l'API GBFS toutes les 60 secondes, n'envoie que les "
     "stations dont l'état a changé (÷8 événements) plus un instantané complet toutes les 15 minutes, et publie des "
     "événements JSON vers le *custom endpoint* d'un **Eventstream** (protocole Event Hub). La chaîne de connexion est lue "
     "dans **Azure Key Vault** avec `DefaultAzureCredential` — jamais dans le code. L'Eventstream route les événements vers "
     "la table `StationStatus` d'un **Eventhouse** (mapping JSON, rétention 30 jours, cache chaud 7 jours). Deux vues "
     "matérialisées (`LatestStationStatus`, `StationHourly`) servent le tableau de bord temps réel à faible coût.")
add_table([["Requête", "Question", "Technique KQL"],
           ["Q1 / Q1b", "Stations vides, presque vides, saturées maintenant", "vue matérialisée arg_max, case()"],
           ["Q2", "Santé du réseau par fenêtre de 15 min", "**fenêtre temporelle** bin(15m) + arg_max par fenêtre"],
           ["Q3", "Stations qui se vident le plus vite (minutes avant rupture)", "arg_min / arg_max, taux de vidage"],
           ["Q4", "Pics / creux anormaux d'activité", "make-series + series_decompose_anomalies"],
           ["Q5", "Taux de remplissage moyen, moyenne glissante 1 h", "prev() sur fenêtres de 15 min"],
           ["Q6", "Qualité du flux (stations muettes, hors service)", "datetime_diff, countif"],
           ["Q8", "Stations restées le plus longtemps indisponibles aujourd'hui", "fonction stockée, next()"],
           ["Q9", "Stations vides sur fenêtre glissante 30 min (pas 5 min)", "**fenêtre glissante** range + mv-expand"],
           ["Q10", "Prévision du nombre de stations vides à 6 h", "series_decompose_forecast"],
           ["Q11", "Départs live vs historique de la même heure", "prev() + lien avec la table Gold"],
           ["ALERT-1/2/3", "Station très utilisée vide ≥ 10 min · > 8 % du réseau en rupture · collecteur arrêté",
            "règles Activator → Teams / e-mail"]])
para("Fichiers : `kql/01_create_tables.kql` (table, mapping, politiques, vue matérialisée), `kql/02_queries.kql` "
     "(Q1-Q7), `kql/03_alerts.kql` (vue horaire, fonctions, Q8-Q11, alertes).")
# 7 ------------------------------------------------------------------
page_break()
doc.add_heading("7. Modèle Power BI, KPI et enseignements", level=1)
render_md(ROOT / "powerbi" / "README_powerbi.md", shift=2)
add_image(D / "figures" / "fig2_heatmap_day_hour.png", 16, "Figure — Heatmap jour × heure (page « Quand ? »)")
# 8 ------------------------------------------------------------------
page_break()
doc.add_heading("8. Sécurité et gouvernance", level=1)
render_md(ROOT / "security" / "README_security.md", shift=2, drop_sections=("7. Coûts",))
# 9 ------------------------------------------------------------------
page_break()
doc.add_heading("9. Machine Learning et extensions avancées", level=1)
for a, b in [("## 5.", "## 6."), ("## 6.", "## 7."), ("## 8.", "## 99.")]:
    if a in results:
        body = results.split(a, 1)[1]
        body = body.split(b, 1)[0] if b in body else body
        title, rest = body.split("\n", 1)
        doc.add_heading(title.strip(), level=2)
        tmp.write_text("# x\n" + rest, encoding="utf-8")
        render_md(tmp, shift=2)
doc.add_heading("Extensions (bonus)", level=2)
add_table([["Extension", "Réalisation", "Preuve"],
           ["MLOps", "registre MLflow, alias challenger → champion seulement si la MAE bat le champion et la baseline, "
                     "tags de validation, journal gold_model_promotion_log", "notebook 03 §5, onglet Modèle de l'app"],
           ["CI/CD", "GitHub Actions : lint (ruff), tests (pytest), validation JSON, détection de secrets, "
                     "notebooks Fabric synchronisés", ".github/workflows/ci.yml"],
           ["Application / API", "API REST FastAPI (KPI, priorités, station, live, prédiction, flux O-D, modèle) protégée "
                                 "par clé ; application opérateur Streamlit (carte des risques, tournée, rééquilibrage, "
                                 "flux, prévision « et si » météo)", "app/api.py, app/dashboard.py"],
           ["Surveillance / alertes", "6 règles (qualité, flux, opérationnel, dérive du modèle) → journal + webhook Teams, "
                                      "code retour pour les pipelines ; 3 règles Activator sur l'Eventhouse",
            "scripts/alert_monitor.py, kql/03_alerts.kql"],
           ["Gouvernance avancée", "policy-as-code (3 Azure Policies), classification personnalisée Purview "
                                   "SmartBike.GeoTrip, lignage, rétention", "security/"],
           ["Agrégations avancées", "fenêtres, CUBE, percentiles, O-D, spatial, rééquilibrage", "notebook 05"]])
# 10 -----------------------------------------------------------------
page_break()
doc.add_heading("10. Coûts, limites, passage à l'échelle et suite", level=1)
render_md(D / "cost_and_inventory.md", shift=2, drop_sections=("Convention de nommage",))
doc.add_heading("Limites assumées", level=2)
readme = (ROOT / "README.md").read_text(encoding="utf-8")
lim = readme.split("## Limites assumées")[1].split("\n## ")[0]
tmp.write_text("# x\n" + lim, encoding="utf-8")
render_md(tmp, shift=2)
doc.add_heading("Améliorations envisagées", level=2)
for s_ in ["Collecter l'historique GBFS plusieurs mois pour modéliser directement le stock (et non plus les flux).",
           "Corriger la demande censurée : estimer la demande perdue quand une station est vide.",
           "Météo par zone (plusieurs points) et calendrier d'événements (concerts, matchs, jours fériés).",
           "Optimiser les tournées de camions (problème de tournées de véhicules) à partir du score de priorité.",
           "Déplacer le score dans l'Eventhouse (modèle ONNX) pour une latence inférieure à la minute."]:
    doc.add_paragraph(s_, style="List Bullet")
# 11 -----------------------------------------------------------------
page_break()
doc.add_heading("11. Reproductibilité et nettoyage", level=1)
render_md(D / "reproducibility.md", shift=2)
tmp.unlink(missing_ok=True)

# footer with page numbers
for s in doc.sections:
    fp = s.footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = fp.add_run("SmartBike AI — rapport technique · page ")
    run.font.size, run.font.color.rgb = Pt(8), GREY
    r = fp.add_run()
    for tag, txt in [("begin", None), (None, "PAGE"), ("end", None)]:
        if tag:
            f = OxmlElement("w:fldChar")
            f.set(qn("w:fldCharType"), tag)
            r._r.append(f)
        else:
            it = OxmlElement("w:instrText")
            it.text = txt
            r._r.append(it)
    r.font.size = Pt(8)

doc.save(OUT)
print(f"written {OUT}")

# ------------------------------------------------------------------ PDF through Microsoft Word (Windows), TOC refreshed
if sys.platform == "win32" and "--no-pdf" not in sys.argv:
    pdf = OUT.with_suffix(".pdf")
    ps = (f"$w = New-Object -ComObject Word.Application; $w.Visible = $false; "
          f"$d = $w.Documents.Open('{OUT}'); $d.TablesOfContents | ForEach-Object {{ $_.Update() }}; "
          f"$d.Fields.Update() | Out-Null; $d.Save(); $d.SaveAs([ref]'{pdf}', [ref]17); $d.Close(); $w.Quit()")
    res = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=300)
    print(f"written {pdf}" if pdf.exists() else f"[warn] PDF export failed: {res.stderr[-400:]}")
