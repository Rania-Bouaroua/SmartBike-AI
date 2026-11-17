"""SmartBike AI - operator web app (Streamlit).

Run:  streamlit run app/dashboard.py
Complements Power BI: Power BI = analysis for managers; this app = action for the field team
(map of stations to visit now, what-if forecast for a station, rebalancing needs).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pydeck as pdk
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import common  # noqa: E402

st.set_page_config(page_title="SmartBike AI", page_icon="🚲", layout="wide")

RISK_COLORS = {"vide maintenant": [208, 59, 59], "risque vide": [236, 131, 90],
               "saturee maintenant": [24, 79, 149], "risque saturee": [57, 135, 229], "ok": [27, 175, 122]}


@st.cache_data
def t(name: str) -> pd.DataFrame:
    return common.table(name).copy()


def fmt(n: float) -> str:
    return f"{n:,.0f}".replace(",", " ")


st.title("🚲 SmartBike AI")
st.caption("Anticiper les stations vides ou saturées — Citi Bike New York · données réelles · "
           "tables Gold du Lakehouse + modèle MLflow (alias champion)")

tabs = st.tabs(["Vue d'ensemble", "Interventions", "Rééquilibrage", "Flux origine → destination",
                "Prévision", "Modèle"])

# ---------------------------------------------------------------- 1. overview
with tabs[0]:
    d = t("gold_daily_kpis").sort_values("date")
    d["date"] = pd.to_datetime(d["date"])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Trajets", fmt(d.trips.sum()), help=f"{d.date.min():%d/%m/%Y} → {d.date.max():%d/%m/%Y}")
    c2.metric("Trajets / jour", fmt(d.trips.mean()))
    c3.metric("Abonnés", f"{(d.member_share_pct * d.trips).sum() / d.trips.sum():.1f} %")
    c4.metric("Vélos électriques", f"{(d.ebike_share_pct * d.trips).sum() / d.trips.sum():.1f} %")

    st.subheader("Trajets par jour")
    st.line_chart(d.set_index("date")[["trips"]], height=260)

    left, right = st.columns(2)
    with left:
        st.subheader("Quand roule-t-on ? (trajets / heure, moyenne)")
        pat = t("gold_hourly_pattern")
        piv = pat.pivot_table(index="hour", columns="day_name", values="avg_trips_per_hour")
        order = [c for c in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] if c in piv.columns]
        st.dataframe(piv[order].style.background_gradient(cmap="Blues", axis=None).format("{:.0f}"),
                     height=420, width="stretch")
    with right:
        st.subheader("Tendance hebdomadaire")
        try:
            w = t("gold_weekly_trend").sort_values("week_start")
            st.bar_chart(w.set_index("week_start")[["trips_per_day"]], height=240)
            st.dataframe(w[["week_start", "trips_per_day", "wow_growth_pct", "ebike_share_pct"]], hide_index=True,
                         width="stretch")
        except FileNotFoundError:
            st.info("Lancer le notebook 05 pour la tendance hebdomadaire.")

# ---------------------------------------------------------------- 2. interventions (live + forecast)
with tabs[1]:
    try:
        p = t("gold_station_priority")
    except FileNotFoundError:
        st.warning("Lancer le notebook 04 (score de priorité).")
        p = None
    if p is not None:
        snap = p.snapshot_time_local.iloc[0]
        st.caption(f"Instantané GBFS : {snap} (heure de New York) · horizon {int(p.horizon_hours.iloc[0])} h")
        counts = p.risk.value_counts()
        cols = st.columns(5)
        for col, k in zip(cols, ["vide maintenant", "risque vide", "saturee maintenant", "risque saturee", "ok"]):
            col.metric(k, int(counts.get(k, 0)))
        risks = st.multiselect("Filtrer par risque", list(RISK_COLORS), default=[k for k in RISK_COLORS if k != "ok"])
        sel = p[p.risk.isin(risks)].copy()
        sel["color"] = sel.risk.map(RISK_COLORS)
        sel["radius"] = 40 + sel.bikes_to_move.abs() * 6
        st.pydeck_chart(pdk.Deck(
            map_style=None,
            initial_view_state=pdk.ViewState(latitude=40.73, longitude=-73.97, zoom=11),
            layers=[pdk.Layer("ScatterplotLayer", sel, get_position=["lon", "lat"], get_fill_color="color",
                              get_radius="radius", pickable=True, opacity=0.8)],
            tooltip={"text": "{station_name}\n{num_bikes_available} vélos / {capacity}\n{risk} → {action} "
                             "({bikes_to_move})"}))
        st.subheader("Liste de tournée (par priorité)")
        st.dataframe(sel.sort_values("priority_rank")[
            ["priority_rank", "station_name", "num_bikes_available", "num_docks_available", "capacity",
             "pred_departures", "pred_arrivals", "risk", "action", "bikes_to_move"]].head(50),
            hide_index=True, width="stretch")

# ---------------------------------------------------------------- 3. rebalancing need (notebook 05)
with tabs[2]:
    try:
        rd = t("gold_rebalancing_daily").sort_values("date")
        rn = t("gold_rebalancing_need")
        prof = t("gold_station_profile")[["station_id", "station_name", "lat", "lon", "station_role"]]
        c1, c2, c3 = st.columns(3)
        c1.metric("Déplacements forcés / jour (moy.)", fmt(rd.forced_moves.mean()),
                  help="Vélos qui ne tiennent pas dans la capacité de la station sur la journée : un camion doit passer.")
        c2.metric("Stations à rééquilibrer / jour", fmt(rd.stations_needing_truck.mean()))
        c3.metric("Part du réseau", f"{rd.pct_stations_needing_truck.mean():.1f} %")
        rd["date"] = pd.to_datetime(rd["date"])
        st.line_chart(rd.set_index("date")[["forced_moves"]], height=240)
        top = (rn.groupby("station_id").agg(days_with_truck=("needs_truck", "sum"), avg_forced=("forced_moves", "mean"),
                                            usual_min_hour=("hour_of_min_stock", "median"))
               .reset_index().merge(prof, on="station_id").sort_values("days_with_truck", ascending=False).head(30))
        st.subheader("Stations qui demandent le plus souvent un camion")
        st.dataframe(top, hide_index=True, width="stretch")
        st.caption("Méthode : stock relatif = somme cumulée (arrivées − départs) heure par heure depuis minuit. "
                   "Si l'amplitude de ce stock dépasse la capacité, aucun niveau de départ n'évite une rupture.")
    except FileNotFoundError:
        st.info("Lancer le notebook 05 (agrégations avancées).")

# ---------------------------------------------------------------- 4. OD flows
with tabs[3]:
    try:
        od = t("gold_od_flows").sort_values("trips", ascending=False)
        n = st.slider("Nombre de liaisons", 10, 300, 80)
        top = od.head(n).dropna(subset=["origin_lat", "dest_lat"])
        st.pydeck_chart(pdk.Deck(
            map_style=None,
            initial_view_state=pdk.ViewState(latitude=40.73, longitude=-73.97, zoom=11, pitch=40),
            layers=[pdk.Layer("ArcLayer", top, get_source_position=["origin_lon", "origin_lat"],
                              get_target_position=["dest_lon", "dest_lat"], get_source_color=[42, 120, 214],
                              get_target_color=[235, 104, 52], get_width="trips / 300", pickable=True)],
            tooltip={"text": "{origin_name} → {destination_name}\n{trips} trajets · {median_duration_min} min"}))
        st.dataframe(top[["rank_global", "origin_name", "destination_name", "trips", "median_duration_min",
                          "avg_dist_km", "avg_speed_kmh", "member_pct", "am_peak_pct"]], hide_index=True,
                     width="stretch")
    except FileNotFoundError:
        st.info("Lancer le notebook 05 (agrégations avancées).")

# ---------------------------------------------------------------- 5. what-if forecast
with tabs[4]:
    stations = t("silver_stations").dropna(subset=["capacity"])
    prof = t("gold_station_profile")[["station_id", "departures"]]
    stations = stations.merge(prof, on="station_id").sort_values("departures", ascending=False)
    label = stations.station_name + " (" + stations.station_id + ")"
    choice = st.selectbox("Station", label.tolist())
    sid = stations.station_id.iloc[label.tolist().index(choice)]
    c1, c2, c3 = st.columns(3)
    day = c1.date_input("Jour", pd.Timestamp.now().date())
    temp = c2.slider("Température (°C)", -10, 38, 15)
    rain = c3.slider("Pluie (mm/h)", 0.0, 15.0, 0.0, 0.5)
    rows = [common.predict(sid, pd.Timestamp(day) + pd.Timedelta(hours=h),
                           {"temperature_c": temp, "precipitation_mm": rain}) for h in range(24)]
    fc = pd.DataFrame(rows)
    fc["heure"] = pd.to_datetime(fc.hour_local).dt.hour
    st.subheader("Départs et arrivées prévus par heure")
    st.bar_chart(fc.set_index("heure")[["pred_departures", "pred_arrivals"]], height=300)
    cum = fc.pred_net_flow.cumsum()
    st.caption(f"Bilan cumulé sur la journée : {cum.iloc[-1]:+.0f} vélos · point le plus bas {cum.min():+.0f} "
               f"à {int(fc.heure[cum.idxmin()])} h (vélos à prévoir en début de journée).")
    st.caption(f"Modèle : {rows[0]['model_departures']}")

# ---------------------------------------------------------------- 6. model
with tabs[5]:
    m = t("gold_ml_metrics")
    show = ["target", "baseline_mae", "model_mae", "mae_improvement_pct", "baseline_rmse", "model_rmse",
            "rmse_improvement_pct", "system_wape_baseline_pct", "system_wape_model_pct"]
    st.subheader("Évaluation sur la dernière semaine (jamais vue à l'entraînement)")
    st.dataframe(m[[c for c in show if c in m.columns]].round(3), hide_index=True, width="stretch")
    try:
        st.subheader("Journal de promotion (champion / challenger)")
        st.dataframe(t("gold_model_promotion_log"), hide_index=True, width="stretch")
    except FileNotFoundError:
        pass
    f = t("gold_demand_forecast")
    f["hour_ts"] = pd.to_datetime(f.hour_ts)
    net = f.groupby("hour_ts")[["departures", "pred_departures", "baseline_departures"]].sum()
    st.subheader("Réseau entier : départs observés vs prévus")
    st.line_chart(net, height=300)
