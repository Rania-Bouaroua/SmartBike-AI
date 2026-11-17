"""Builds docs/results.md and docs/figures/*.png from the Gold tables (run after the 4 notebooks).

Usage: python docs/make_results.py
Figures are static report figures (and a fallback if the live demo fails); the interactive version is Power BI.
"""
import pathlib

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
T = ROOT / "data" / "lakehouse" / "Tables"
FIG = ROOT / "docs" / "figures"
FIG.mkdir(parents=True, exist_ok=True)

# Validated reference palette (light mode) - categorical slots in fixed order, status colors reserved.
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
CRITICAL, WARNING, SERIOUS, MUTED_FILL = "#d03b3b", "#fab219", "#ec835a", "#c3c2b7"
INK, INK2, MUTED, GRID, AXIS, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb"
SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": AXIS, "axes.labelcolor": INK2, "xtick.color": MUTED, "ytick.color": MUTED,
    "text.color": INK, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "axes.spines.top": False, "axes.spines.right": False, "axes.titleweight": "bold",
    "axes.titlesize": 12, "axes.titlelocation": "left", "font.size": 9.5, "lines.linewidth": 2,
})


def t(name):
    return pd.read_parquet(T / name)


def save(fig, name):
    fig.savefig(FIG / name, dpi=150, bbox_inches="tight")
    plt.close(fig)


def fmt(n):
    return f"{n:,.0f}".replace(",", " ")


daily = t("gold_daily_kpis").sort_values("date")
daily["date"] = pd.to_datetime(daily["date"])
profile = t("gold_station_profile")
pattern = t("gold_hourly_pattern")
weather = t("gold_weather_effect")
dq = t("gold_data_quality")
metrics = t("gold_ml_metrics")
forecast = t("gold_demand_forecast")
priority = t("gold_station_priority")
hourly = t("gold_station_hourly")

# ---------- Fig 1: daily trips (and temperature as its own panel - never a dual axis) ----------
fig, (a1, a2) = plt.subplots(2, 1, figsize=(10, 5.4), sharex=True, gridspec_kw={"height_ratios": [2.2, 1]})
a1.plot(daily.date, daily.trips / 1000, color=BLUE)
we = daily[daily.is_weekend]
a1.scatter(we.date, we.trips / 1000, s=22, color=BLUE, edgecolor=SURFACE, linewidth=1.5, zorder=3, label="week-end")
a1.set_title("Trajets par jour (milliers)")
a1.legend(frameon=False, loc="lower left")
top = daily.loc[daily.trips.idxmax()]
a1.annotate(f"max {top.trips / 1000:.0f} k\n{top.date:%d/%m}", (top.date, top.trips / 1000),
            xytext=(8, -4), textcoords="offset points", fontsize=8, color=INK2)
a2.plot(daily.date, daily.avg_temp_c, color=ORANGE)
a2.bar(daily.date, daily.total_precip_mm, color=SEQ[2], width=0.8, label="pluie (mm/jour)")
a2.set_title("Température moyenne (°C) et pluie (mm/jour)", fontsize=10)
a2.legend(frameon=False, loc="upper right", fontsize=8)
a2.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
save(fig, "fig1_daily_trips.png")

# ---------- Fig 2: heatmap day x hour (sequential, one hue) ----------
order = [2, 3, 4, 5, 6, 7, 1]
names = {1: "dim", 2: "lun", 3: "mar", 4: "mer", 5: "jeu", 6: "ven", 7: "sam"}
mat = pattern.pivot_table(index="dow", columns="hour", values="avg_trips_per_hour").reindex(order)
fig, ax = plt.subplots(figsize=(10, 3.4))
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

im = ax.imshow(mat.values, aspect="auto", cmap=LinearSegmentedColormap.from_list("seq", SEQ))
ax.set_yticks(range(7), [names[d] for d in order])
ax.set_xticks(range(0, 24, 2), [f"{h}h" for h in range(0, 24, 2)])
ax.grid(False)
ax.set_title("Trajets moyens par heure — jour de semaine × heure")
cb = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.01)
cb.outline.set_visible(False)
save(fig, "fig2_heatmap_day_hour.png")

# ---------- Fig 3: top 15 stations ----------
top15 = profile.nlargest(15, "departures").iloc[::-1]
fig, ax = plt.subplots(figsize=(8, 5))
ax.barh(top15.station_name, top15.departures / 1000, color=BLUE, height=0.7)
for y, v in enumerate(top15.departures / 1000):
    ax.text(v + 0.2, y, f"{v:.1f} k", va="center", fontsize=8, color=INK2)
ax.set_title("Top 15 stations par nombre de départs (milliers, 3 mois)")
ax.grid(axis="y", visible=False)
save(fig, "fig3_top_stations.png")

# ---------- Fig 4: weather effect (weekday daytime) ----------
w = weather[(weather.daypart == "jour 7h-19h") & (~weather.is_weekend.astype(bool))]
w = w.assign(tot=w.avg_trips_per_hour * w.hours).groupby("rain_bucket")[["tot", "hours"]].sum()
w["avg"] = w.tot / w.hours
fig, ax = plt.subplots(figsize=(6.5, 3.6))
bars = ax.bar([b[3:] for b in w.index], w.avg, color=[BLUE, SEQ[2], SEQ[5]][:len(w)], width=0.6)
for b, (avg, hrs) in zip(bars, zip(w.avg, w.hours)):
    ax.text(b.get_x() + b.get_width() / 2, avg, f"{avg:,.0f}\n({hrs:.0f} h)".replace(",", " "),
            ha="center", va="bottom", fontsize=8, color=INK2)
ax.set_title("Trajets par heure selon la pluie — jours ouvrés, 7h-19h")
ax.set_ylim(0, w.avg.max() * 1.25)
ax.grid(axis="x", visible=False)
save(fig, "fig4_weather_effect.png")

# ---------- Fig 5: ML at network level on the test week ----------
forecast["hour_ts"] = pd.to_datetime(forecast["hour_ts"])
sysf = forecast.groupby("hour_ts")[["departures", "pred_departures", "baseline_departures"]].sum()
fig, ax = plt.subplots(figsize=(10, 3.8))
ax.xaxis.set_major_formatter(mdates.DateFormatter("%a %d/%m"))
ax.plot(sysf.index, sysf.departures, color=BLUE, label="réel")
ax.plot(sysf.index, sysf.pred_departures, color=ORANGE, label="modèle (météo + tendance)")
ax.plot(sysf.index, sysf.baseline_departures, color=AQUA, linestyle=(0, (4, 2)), label="baseline (moyenne historique)")
ax.set_title("Départs par heure sur tout le réseau — semaine de test (jamais vue à l'entraînement)")
ax.legend(frameon=False, ncol=3, loc="upper left")
save(fig, "fig5_ml_network_test_week.png")

# ---------- Fig 6: current risk states (status colors + text labels) ----------
states = ["vide maintenant", "risque vide", "saturee maintenant", "risque saturee", "ok"]
colors = [CRITICAL, WARNING, SERIOUS, "#f5c2a8", MUTED_FILL]
counts = priority.risk.value_counts().reindex(states).fillna(0)
fig, ax = plt.subplots(figsize=(7.5, 3.4))
bars = ax.bar(states, counts, color=colors, width=0.65)
for b, c in zip(bars, counts):
    ax.text(b.get_x() + b.get_width() / 2, c, fmt(c), ha="center", va="bottom", fontsize=9, color=INK2)
snap = pd.to_datetime(priority.snapshot_time_local.iloc[0])
ax.set_title(f"État des stations à {snap:%d/%m %Hh} (NY) et projection à {int(priority.horizon_hours.iloc[0])} h")
ax.grid(axis="x", visible=False)
save(fig, "fig6_priority_states.png")

# ---------- Fig 7-9: advanced aggregations (notebook 05) ----------
rebal_d = t("gold_rebalancing_daily").sort_values("date")
rebal_d["date"] = pd.to_datetime(rebal_d["date"])
rebal = t("gold_rebalancing_need")
od = t("gold_od_flows").sort_values("trips", ascending=False)
cube = t("gold_usage_cube")
weekly = t("gold_weekly_trend").sort_values("week_start")
vol = t("gold_station_volatility")
promo = t("gold_model_promotion_log") if (T / "gold_model_promotion_log").exists() else None

fig, ax = plt.subplots(figsize=(10, 3.4))
ax.bar(rebal_d.date, rebal_d.forced_moves, color=[MUTED_FILL if w_ else BLUE for w_ in rebal_d.is_weekend], width=0.8)
ax.set_title("Vélos à déplacer par camion chaque jour (stock cumulé > capacité) — gris = week-end")
ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
ax.grid(axis="x", visible=False)
save(fig, "fig7_rebalancing_daily.png")

top_od = od.head(12).iloc[::-1]
fig, ax = plt.subplots(figsize=(9, 4.8))
labels = [f"{o[:28]} → {d[:28]}" for o, d in zip(top_od.origin_name.fillna("?"), top_od.destination_name.fillna("?"))]
ax.barh(labels, top_od.trips, color=ORANGE, height=0.7)
for y, (v, mn) in enumerate(zip(top_od.trips, top_od.median_duration_min)):
    ax.text(v, y, f" {fmt(v)} · {mn:.0f} min", va="center", fontsize=8, color=INK2)
ax.set_title("Liaisons origine → destination les plus fréquentes (3 mois)")
ax.set_xlim(0, top_od.trips.max() * 1.25)
ax.grid(axis="y", visible=False)
save(fig, "fig8_top_od_flows.png")

c0 = cube[(cube.grouping_level == 0)]
mat_c = c0.groupby(["daypart", "member_casual"]).trips.sum().unstack().sort_index()
fig, ax = plt.subplots(figsize=(7.5, 3.4))
x = np.arange(len(mat_c))
for i, (col, colr) in enumerate(zip(mat_c.columns, [ORANGE, BLUE])):
    ax.bar(x + (i - 0.5) * 0.38, mat_c[col] / 1e6, width=0.38, color=colr, label=col)
ax.set_xticks(x, [i[3:] for i in mat_c.index])
ax.set_title("Trajets (millions) par moment de la journée et type d'usager (cube OLAP)")
ax.legend(frameon=False)
ax.grid(axis="x", visible=False)
save(fig, "fig9_usage_cube.png")

# ---------- results.md ----------
raw = int(dq.loc[(dq.table == "trips")].rows.sum() - dq.loc[dq.check == "duplicate_ride_id", "rows"].sum())
valid = int(dq.loc[dq.check == "valid", "rows"].sum())
dq_rows = dq[dq.table == "trips"].sort_values("rows", ascending=False)
st = dq[dq.table == "stations"]
matched_pct = st.loc[st.check == "station_matched", "pct_of_raw"].iloc[0]
m = metrics.set_index("target")
wk = daily[~daily.is_weekend].trips.mean()
wkend = daily[daily.is_weekend].trips.mean()
dry, rainy = w.avg.iloc[0], (w.tot.iloc[1:].sum() / w.hours.iloc[1:].sum()) if len(w) > 1 else np.nan
peak = pattern[pattern.dow.between(2, 6)].groupby("hour").avg_trips_per_hour.mean()
ranked = priority.sort_values("priority_rank").head(10)

lines = [
    "# Résultats (exécution locale sur les données réelles)",
    "",
    f"Période : **{daily.date.min():%d/%m/%Y} → {daily.date.max():%d/%m/%Y}** ({len(daily)} jours). "
    "Généré par `python docs/make_results.py` à partir des tables Gold.",
    "",
    "## 1. Volumétrie et qualité des données (notebook 01)",
    "",
    f"* Trajets bruts : **{fmt(raw)}** · valides après contrôles : **{fmt(valid)}** ({valid / raw:.2%}).",
    f"* Réconciliation des identifiants : **{matched_pct:.2f} %** des départs/arrivées rattachés à une station GBFS actuelle.",
    f"* Grille Gold station × heure : **{fmt(len(hourly))}** lignes · {hourly.station_id.nunique()} stations actives.",
    "",
    "| Contrôle | Lignes | % du brut |",
    "|---|---:|---:|",
    *[f"| {r.check} | {fmt(r.rows)} | {r.pct_of_raw:.3f} |" for r in dq_rows.itertuples()],
    *[f"| {r.check} (stations) | {fmt(r.rows)} | {r.pct_of_raw:.2f} % des trajets |" for r in st.itertuples()],
    "",
    "## 2. Usage (notebook 02)",
    "",
    f"* **{fmt(daily.trips.sum())}** trajets, soit **{fmt(daily.trips.mean())}** par jour en moyenne ; "
    f"jour record : {fmt(top.trips)} le {top.date:%d/%m/%Y}.",
    f"* Jours ouvrés : {fmt(wk)} trajets/jour · week-end : {fmt(wkend)} ({(wkend - wk) / wk:+.1%}).",
    f"* Abonnés : **{(daily.trips * daily.member_share_pct).sum() / daily.trips.sum():.1f} %** des trajets ; "
    f"vélos électriques : **{(daily.trips * daily.ebike_share_pct).sum() / daily.trips.sum():.1f} %** ; "
    f"durée moyenne {(daily.trips * daily.avg_duration_min).sum() / daily.trips.sum():.1f} min.",
    f"* Heures de pointe en semaine : **{peak.idxmax()} h** ({fmt(peak.max())} trajets/h) — "
    f"pic du matin à {peak.loc[6:11].idxmax()} h ({fmt(peak.loc[6:11].max())} trajets/h).",
    "",
    "![](figures/fig1_daily_trips.png)",
    "![](figures/fig2_heatmap_day_hour.png)",
    "",
    "## 3. Stations",
    "",
    "Sur un mois entier, départs ≈ arrivées presque partout (rééquilibrage par camions, allers-retours) : "
    "le déséquilibre est **intrajournalier**. Rôle calculé sur le pic du matin (7h-10h, jours ouvrés) :",
    "",
    "| Rôle le matin | Stations |",
    "|---|---:|",
    *[f"| {k} | {v} |" for k, v in profile.station_role.value_counts().items()],
    "",
    f"**{(profile.am_peak_risk == 'risque vide').sum()}** stations perdent en moyenne plus de 30 % de leur capacité "
    f"chaque matin de semaine (se videraient sans rééquilibrage) et **{(profile.am_peak_risk == 'risque saturee').sum()}** "
    "en gagnent plus de 30 % (saturation).",
    "",
    "![](figures/fig3_top_stations.png)",
    "",
    "## 4. Effet de la météo",
    "",
    f"Jours ouvrés 7h-19h : **{fmt(dry)}** trajets/h par temps sec contre **{fmt(rainy)}** sous la pluie "
    f"(**{(rainy - dry) / dry:+.0%}**), sur {w.hours.iloc[1:].sum():.0f} heures pluvieuses seulement — à confirmer "
    "sur une période plus longue.",
    "",
    "![](figures/fig4_weather_effect.png)",
    "",
    "## 5. Machine Learning (notebook 03)",
    "",
    "Découpage chronologique : les 7 derniers jours servent de test. Baseline = moyenne historique de la station "
    "pour la même heure et le même type de jour.",
    "",
    "| Cible | MAE baseline | MAE modèle | RMSE baseline | RMSE modèle | Erreur réseau/heure (WAPE) baseline → modèle |",
    "|---|---:|---:|---:|---:|---:|",
    *[f"| {k} | {r.baseline_mae:.3f} | {r.model_mae:.3f} | {r.baseline_rmse:.3f} | {r.model_rmse:.3f} | "
      f"{r.system_wape_baseline_pct:.1f} % → **{r.system_wape_model_pct:.1f} %** |" for k, r in m.iterrows()],
    "",
    "Lecture : au niveau d'une station et d'une heure (≈ 1-2 trajets en moyenne), l'aléa domine et le modèle ne gagne "
    f"que {m.loc['departures', 'mae_improvement_pct']:.1f} % de MAE. Sommé sur le réseau, où la météo et la tendance "
    "saisonnière jouent pleinement, l'erreur horaire baisse nettement.",
    "",
    "![](figures/fig5_ml_network_test_week.png)",
    "",
    "## 6. Priorités d'intervention (notebook 04, état live GBFS)",
    "",
    f"Snapshot : **{snap:%d/%m/%Y %H:%M}** (heure de New York), horizon {int(priority.horizon_hours.iloc[0])} h.",
    "",
    "| État | Stations |",
    "|---|---:|",
    *[f"| {k} | {int(v)} |" for k, v in counts.items()],
    "",
    f"Vélos à déplacer pour ramener les stations à risque à 50 % : **{fmt(priority.bikes_to_move.abs().sum())}**.",
    "",
    "| Rang | Station | Vélos dispo / capacité | Départs prévus | Arrivées prévues | État | Action |",
    "|---:|---|---:|---:|---:|---|---|",
    *[f"| {r.priority_rank} | {r.station_name} | {r.num_bikes_available} / {int(r.capacity)} | {r.pred_departures:.1f} | "
      f"{r.pred_arrivals:.1f} | {r.risk} | {r.action} ({r.bikes_to_move:+d}) |" for r in ranked.itertuples()],
    "",
    "![](figures/fig6_priority_states.png)",
    "",
    "## 7. Agrégations avancées (notebook 05)",
    "",
    f"* **Rééquilibrage** : en moyenne **{fmt(rebal_d.forced_moves.mean())}** vélos par jour doivent être déplacés par camion "
    f"(jours ouvrés {fmt(rebal_d[~rebal_d.is_weekend].forced_moves.mean())}, week-end "
    f"{fmt(rebal_d[rebal_d.is_weekend].forced_moves.mean())}) ; "
    f"**{rebal_d.pct_stations_needing_truck.mean():.1f} %** des stations en ont besoin un jour donné. "
    "Méthode : somme cumulée heure par heure de (arrivées − départs) depuis minuit ; si "
    "l'amplitude de ce stock dépasse la capacité, aucun niveau de remplissage du matin n'évite la rupture.",
    "* Stations demandant le plus souvent un camion : "
    + ", ".join(f"{n} ({int(d)} j)" for n, d in rebal.groupby("station_id").needs_truck.sum().nlargest(5)
                .rename("d").reset_index().merge(profile[["station_id", "station_name"]]).set_index("station_name")
                .d.items()) + ".",
    f"* **Flux origine → destination** : {fmt(len(od))} liaisons ≥ 20 trajets ; les 100 premières concentrent "
    f"{od.head(100).trips.sum() / od.trips.sum():.1%} de ces trajets. Liaison n°1 : {od.iloc[0].origin_name} → "
    f"{od.iloc[0].destination_name} ({fmt(od.iloc[0].trips)} trajets, {od.iloc[0].median_duration_min:.0f} min médianes).",
    f"* **Volatilité** : le décile le plus volatil des stations a un coefficient de variation ≥ "
    f"{vol[vol.volatility_decile == 10].coef_variation.min():.2f} (demande horaire très irrégulière, plus difficile à prévoir).",
    f"* **Tendance hebdomadaire** : de {fmt(weekly.trips_per_day.iloc[1])} à {fmt(weekly.trips_per_day.iloc[-2])} trajets/jour "
    "entre la 2e et l'avant-dernière semaine complète.",
    "",
    "![](figures/fig7_rebalancing_daily.png)",
    "![](figures/fig8_top_od_flows.png)",
    "![](figures/fig9_usage_cube.png)",
    "",
    *([] if promo is None else [
        "## 8. MLOps : promotion du modèle",
        "",
        "| Modèle | Version candidate | MAE candidate | Champion avant | MAE champion | Baseline | Promu |",
        "|---|---:|---:|---:|---:|---:|---|",
        *[f"| {r.model} | {r.challenger_version} | {r.challenger_mae:.4f} | {r.champion_version_before} | "
          f"{r.champion_mae_before} | {r.baseline_mae:.4f} | {'oui' if r.promoted else 'non'} |" for r in promo.itertuples()],
        ""]),
]
(ROOT / "docs" / "results.md").write_text("\n".join(lines), encoding="utf-8")
print("written docs/results.md and", len(list(FIG.glob('*.png'))), "figures")
