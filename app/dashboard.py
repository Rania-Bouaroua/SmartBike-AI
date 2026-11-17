"""SmartBike AI - operator command center (Streamlit).

Run:  streamlit run app/dashboard.py
Complements Power BI: Power BI = analysis for managers; this app = action for the field team
(live map of stations to visit now, rebalancing needs, flows, what-if forecast, model monitoring).
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import pydeck as pdk
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import common  # noqa: E402

LOGO = ROOT / "assets" / "logo.png"
st.set_page_config(page_title="SmartBike AI · Command Center", page_icon=str(LOGO) if LOGO.exists() else "🚲",
                   layout="wide", initial_sidebar_state="expanded")

# ------------------------------------------------------------------ design system
CYAN, INDIGO, VIOLET, AMBER, ROSE, EMERALD, SLATE = ("#22D3EE", "#818CF8", "#C084FC", "#FBBF24", "#FB7185",
                                                      "#34D399", "#94A3B8")
RISK = {  # label, colour (rgb for maps), hex
    "vide maintenant": ("Vide maintenant", [251, 113, 133], ROSE),
    "risque vide": ("Risque de vide", [251, 191, 36], AMBER),
    "saturee maintenant": ("Saturée maintenant", [192, 132, 252], VIOLET),
    "risque saturee": ("Risque de saturation", [129, 140, 248], INDIGO),
    "ok": ("OK", [52, 211, 153], EMERALD),
}
MAP_STYLE = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json"
NYC = pdk.ViewState(latitude=40.735, longitude=-73.965, zoom=11.2, pitch=45, bearing=-18)


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode() if path.exists() else ""


st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
html, body, [class*="css"], .stMarkdown, .stText, button, input { font-family: 'Inter', sans-serif !important; }
.stApp {
  background:
    radial-gradient(1100px 600px at 0% -10%, rgba(34,211,238,.13), transparent 60%),
    radial-gradient(900px 600px at 100% 0%, rgba(147,51,234,.16), transparent 60%),
    #070B18;
}
#MainMenu, footer, header [data-testid="stToolbar"] { visibility: hidden; }
.block-container { padding-top: 1.4rem; padding-bottom: 3rem; max-width: 1500px; }
section[data-testid="stSidebar"] { background: linear-gradient(180deg, #0D1430 0%, #070B18 100%);
  border-right: 1px solid rgba(129,140,248,.15); }

.hero { display:flex; align-items:center; gap:22px; padding:22px 28px; border-radius:24px;
  background: linear-gradient(120deg, rgba(6,182,212,.18), rgba(79,70,229,.22) 55%, rgba(147,51,234,.25));
  border:1px solid rgba(255,255,255,.08); box-shadow: 0 20px 60px -20px rgba(79,70,229,.45); margin-bottom: 18px;
  position:relative; overflow:hidden; }
.hero:after { content:""; position:absolute; right:-80px; top:-80px; width:320px; height:320px; border-radius:50%;
  background: radial-gradient(circle, rgba(251,191,36,.18), transparent 70%); }
.hero img { width:84px; height:84px; filter: drop-shadow(0 10px 25px rgba(79,70,229,.6)); }
.hero h1 { margin:0; font-size:2.3rem; font-weight:800; letter-spacing:-1px; color:#F8FAFC; line-height:1.1; }
.hero h1 span { background: linear-gradient(90deg,#22D3EE,#818CF8,#C084FC); -webkit-background-clip:text;
  -webkit-text-fill-color:transparent; }
.hero p { margin:6px 0 0; color:#A5B4FC; font-size:.98rem; }
.pill { display:inline-flex; align-items:center; gap:6px; padding:4px 11px; border-radius:999px; font-size:.75rem;
  font-weight:600; background:rgba(255,255,255,.07); border:1px solid rgba(255,255,255,.1); color:#E2E8F0;
  margin-right:6px; margin-top:10px; }
.dot { width:8px; height:8px; border-radius:50%; background:#34D399; box-shadow:0 0 0 0 rgba(52,211,153,.7);
  animation: pulse 1.8s infinite; }
@keyframes pulse { 0% { box-shadow:0 0 0 0 rgba(52,211,153,.7); } 70% { box-shadow:0 0 0 9px rgba(52,211,153,0); }
  100% { box-shadow:0 0 0 0 rgba(52,211,153,0); } }

.kpi-grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap:14px; margin: 6px 0 18px; }
.kpi { position:relative; padding:16px 18px; border-radius:18px; background: rgba(17,24,51,.72);
  border:1px solid rgba(129,140,248,.16); backdrop-filter: blur(8px); overflow:hidden;
  transition: transform .2s ease, border-color .2s ease; }
.kpi:hover { transform: translateY(-3px); border-color: rgba(129,140,248,.45); }
.kpi:before { content:""; position:absolute; left:0; top:0; height:3px; width:100%; background:var(--c); }
.kpi .lbl { color:#94A3B8; font-size:.74rem; font-weight:600; text-transform:uppercase; letter-spacing:.08em; }
.kpi .val { color:#F8FAFC; font-size:1.75rem; font-weight:800; letter-spacing:-.5px; margin-top:4px; }
.kpi .sub { color:var(--c); font-size:.8rem; font-weight:600; margin-top:2px; }
.kpi .ico { position:absolute; right:14px; top:12px; font-size:1.5rem; opacity:.85; }

.section { font-size:1.08rem; font-weight:700; color:#F1F5F9; margin: 10px 0 2px; display:flex; align-items:center;
  gap:8px; }
.section:before { content:""; width:4px; height:18px; border-radius:4px;
  background: linear-gradient(180deg,#22D3EE,#C084FC); }
.hint { color:#94A3B8; font-size:.84rem; margin-bottom:8px; }

.stTabs [role="tablist"] { gap:6px; background: rgba(17,24,51,.6); padding:6px; border-radius:16px;
  border:1px solid rgba(129,140,248,.14); }
.stTabs [role="tab"] { height:42px; border-radius:12px; padding:0 16px; color:#CBD5E1; font-weight:600; }
.stTabs [role="tab"][aria-selected="true"] { background: linear-gradient(120deg, #0891B2, #4F46E5 60%, #7E22CE) !important;
  color:white !important; }
.stTabs [role="tab"][aria-selected="true"] p { color:white !important; }
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] { display:none; }
.stTabs [role="tab"] p { font-weight:600; }

div[data-testid="stPlotlyChart"], div[data-testid="stDeckGlJsonChart"], div[data-testid="stDataFrame"] {
  border-radius:18px; overflow:hidden; border:1px solid rgba(129,140,248,.14); background: rgba(17,24,51,.5); }
.card { padding:14px 16px; border-radius:16px; background: rgba(17,24,51,.72); border:1px solid rgba(129,140,248,.16);
  margin-bottom:10px; }
.card .t { font-weight:700; color:#F8FAFC; }
.card .m { color:#94A3B8; font-size:.82rem; }
.badge { padding:3px 10px; border-radius:999px; font-size:.72rem; font-weight:700; color:#0B1020; }
.legend { display:flex; flex-wrap:wrap; gap:14px; color:#CBD5E1; font-size:.8rem; margin:4px 0 10px; }
.legend i { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:6px; }
</style>
""", unsafe_allow_html=True)

PLOT = dict(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(family="Inter, sans-serif", color="#CBD5E1", size=12), margin=dict(l=10, r=10, t=36, b=10),
            hoverlabel=dict(bgcolor="#111833", bordercolor="#818CF8", font_color="#F8FAFC"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, bgcolor="rgba(0,0,0,0)"),
            colorway=[CYAN, INDIGO, VIOLET, AMBER, ROSE, EMERALD])


def show(fig: go.Figure, height: int = 340) -> None:
    fig.update_layout(**PLOT, height=height)
    fig.update_xaxes(gridcolor="rgba(148,163,184,.08)", zeroline=False)
    fig.update_yaxes(gridcolor="rgba(148,163,184,.08)", zeroline=False)
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


def kpis(items: list[tuple[str, str, str, str, str]]) -> None:
    """items = (icon, label, value, sub-text, colour)."""
    html = "".join(f'<div class="kpi" style="--c:{c}"><div class="ico">{i}</div><div class="lbl">{lb}</div>'
                   f'<div class="val">{v}</div><div class="sub">{s}</div></div>' for i, lb, v, s, c in items)
    st.markdown(f'<div class="kpi-grid">{html}</div>', unsafe_allow_html=True)


def section(title: str, hint: str | None = None) -> None:
    st.markdown(f'<div class="section">{title}</div>', unsafe_allow_html=True)
    if hint:
        st.markdown(f'<div class="hint">{hint}</div>', unsafe_allow_html=True)


def deck(layers: list, view: pdk.ViewState = NYC, tooltip: dict | None = None, height: int = 520) -> None:
    st.pydeck_chart(pdk.Deck(map_style=MAP_STYLE, initial_view_state=view, layers=layers, tooltip=tooltip),
                    height=height)


@st.cache_data
def t(name: str) -> pd.DataFrame:
    return common.table(name).copy()


def has(name: str) -> bool:
    return (common.TABLES / name).exists()


def fmt(n: float) -> str:
    return f"{n:,.0f}".replace(",", " ")


# ------------------------------------------------------------------ header + sidebar
daily = t("gold_daily_kpis").sort_values("date")
daily["date"] = pd.to_datetime(daily["date"])
prio = t("gold_station_priority") if has("gold_station_priority") else None
snap = str(prio.snapshot_time_local.iloc[0])[:16] if prio is not None else "—"

st.markdown(f"""
<div class="hero">
  <img src="data:image/png;base64,{_b64(LOGO)}"/>
  <div>
    <h1>SmartBike <span>AI</span> · Command Center</h1>
    <p>Anticiper les stations vides ou saturées de Citi Bike New York — données réelles, Lakehouse Gold
       et modèle MLflow en production.</p>
    <span class="pill"><span class="dot"></span> Flux GBFS · {snap}</span>
    <span class="pill">📅 {daily.date.min():%d/%m} → {daily.date.max():%d/%m/%Y}</span>
    <span class="pill">🚲 {fmt(daily.trips.sum())} trajets analysés</span>
    <span class="pill">🧠 Modèle champion MLflow</span>
  </div>
</div>""", unsafe_allow_html=True)

with st.sidebar:
    if LOGO.exists():
        st.image(str(ROOT / "assets" / "logo_full_dark.png"), width="stretch")
    st.markdown("#### Plateforme")
    st.markdown("""
- **Azure** · ADLS Gen2 · Key Vault · Policy
- **Fabric** · Lakehouse Delta (Bronze → Silver → Gold)
- **Spark** · 5 notebooks · agrégations avancées
- **Temps réel** · Eventstream → KQL → Activator
- **ML** · HistGradientBoosting + MLflow
""")
    st.markdown("#### Réseau")
    if prio is not None:
        ok_pct = (prio.risk == "ok").mean() * 100
        st.progress(ok_pct / 100, text=f"Santé du réseau : {ok_pct:.0f} % des stations OK")
    st.caption(f"{len(t('silver_stations'))} stations · {len(common.available_tables())} tables Gold/Silver")
    st.markdown("---")
    st.caption("Projet CDDA 2026-2027 · Pr. Ibrahim Rahhal")

tabs = st.tabs(["🏙️ Vue d'ensemble", "🚨 Interventions live", "🚚 Rééquilibrage", "🔀 Flux & zones",
                "🌦️ Usages & météo", "🔮 Prévision", "🧠 Modèle"])

# ================================================================== 1. overview
with tabs[0]:
    w_member = (daily.member_share_pct * daily.trips).sum() / daily.trips.sum()
    w_ebike = (daily.ebike_share_pct * daily.trips).sum() / daily.trips.sum()
    best = daily.loc[daily.trips.idxmax()]
    kpis([("🚲", "Trajets", fmt(daily.trips.sum()), f"{len(daily)} jours analysés", CYAN),
          ("📈", "Trajets / jour", fmt(daily.trips.mean()), f"record {fmt(best.trips)} le {best.date:%d/%m}", INDIGO),
          ("⏱️", "Durée moyenne", f"{(daily.avg_duration_min * daily.trips).sum() / daily.trips.sum():.1f} min",
           "par trajet", VIOLET),
          ("👥", "Abonnés", f"{w_member:.1f} %", "des trajets", EMERALD),
          ("⚡", "Vélos électriques", f"{w_ebike:.1f} %", "des trajets", AMBER),
          ("📍", "Stations actives", fmt(daily.active_stations.max()), "sur la période", ROSE)])

    section("Activité quotidienne", "Trajets par jour, moyenne glissante 7 jours et température moyenne.")
    d = daily.assign(ma7=daily.trips.rolling(7, min_periods=1).mean())
    fig = go.Figure()
    fig.add_bar(x=d.date, y=d.trips, name="Trajets / jour",
                marker=dict(color=[VIOLET if w else INDIGO for w in d.is_weekend], opacity=.55))
    fig.add_scatter(x=d.date, y=d.ma7, name="Moyenne 7 jours", line=dict(color=CYAN, width=3, shape="spline"))
    fig.add_scatter(x=d.date, y=d.avg_temp_c, name="Température (°C)", yaxis="y2",
                    line=dict(color=AMBER, width=2, dash="dot"))
    fig.update_layout(yaxis2=dict(overlaying="y", side="right", showgrid=False, title="°C"), bargap=.15)
    show(fig, 360)

    left, right = st.columns([1.25, 1])
    with left:
        section("Quand roule-t-on ?", "Trajets moyens par heure et par jour de la semaine.")
        pat = t("gold_hourly_pattern")
        order = [c for c in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] if c in set(pat.day_name)]
        fr = dict(zip(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]))
        piv = pat.pivot_table(index="day_name", columns="hour", values="avg_trips_per_hour").reindex(order)
        fig = go.Figure(go.Heatmap(z=piv.values, x=[f"{h}h" for h in piv.columns], y=[fr[d] for d in piv.index],
                                   colorscale=[[0, "#0B1020"], [.35, "#312E81"], [.7, "#7C3AED"], [1, "#22D3EE"]],
                                   hovertemplate="%{y} %{x}<br>%{z:,.0f} trajets/h<extra></extra>", xgap=2, ygap=2,
                                   colorbar=dict(thickness=10, outlinewidth=0)))
        fig.update_yaxes(autorange="reversed")
        show(fig, 330)
    with right:
        section("Tendance hebdomadaire", "Trajets par jour, croissance semaine sur semaine.")
        if has("gold_weekly_trend"):
            w = t("gold_weekly_trend").sort_values("week_start")
            g = w.wow_growth_pct.fillna(0)
            fig = go.Figure(go.Bar(x=pd.to_datetime(w.week_start), y=w.trips_per_day,
                                   marker_color=[EMERALD if v >= 0 else ROSE for v in g],
                                   text=[f"{v:+.0f} %" if i else "" for i, v in enumerate(g)], textposition="outside",
                                   hovertemplate="Semaine du %{x|%d/%m}<br>%{y:,.0f} trajets/jour<extra></extra>"))
            show(fig, 330)

# ================================================================== 2. live interventions
with tabs[1]:
    if prio is None:
        st.warning("Lancer le notebook 04 (score de priorité).")
    else:
        counts = prio.risk.value_counts()
        kpis([("🔴", RISK["vide maintenant"][0], fmt(counts.get("vide maintenant", 0)), "ajouter des vélos", ROSE),
              ("🟠", RISK["risque vide"][0], fmt(counts.get("risque vide", 0)), f"d'ici {int(prio.horizon_hours.iloc[0])} h",
               AMBER),
              ("🟣", RISK["saturee maintenant"][0], fmt(counts.get("saturee maintenant", 0)), "retirer des vélos", VIOLET),
              ("🔵", RISK["risque saturee"][0], fmt(counts.get("risque saturee", 0)),
               f"d'ici {int(prio.horizon_hours.iloc[0])} h", INDIGO),
              ("🟢", "Stations OK", fmt(counts.get("ok", 0)), f"{counts.get('ok', 0) / len(prio) * 100:.0f} % du réseau",
               EMERALD),
              ("🚚", "Vélos à déplacer", fmt(prio.bikes_to_move.abs().sum()), "pour tout rééquilibrer", CYAN)])

        c1, c2 = st.columns([3, 1])
        with c2:
            st.markdown("<br>", unsafe_allow_html=True)
            risks = st.multiselect("Afficher", list(RISK), default=[k for k in RISK if k != "ok"],
                                   format_func=lambda k: RISK[k][0])
            mode = st.radio("Vue", ["3D (colonnes)", "2D (points)"], horizontal=True)
            fig = go.Figure(go.Pie(labels=[RISK[k][0] for k in counts.index], values=counts.values, hole=.68,
                                   marker=dict(colors=[RISK[k][2] for k in counts.index],
                                               line=dict(color="#070B18", width=3)),
                                   textinfo="none", sort=False))
            fig.update_layout(showlegend=False, annotations=[dict(text=f"<b>{(prio.risk == 'ok').mean() * 100:.0f}%</b>"
                                                                       "<br>OK", showarrow=False,
                                                                  font=dict(size=22, color="#F8FAFC"))])
            show(fig, 230)
        with c1:
            sel = prio[prio.risk.isin(risks)].copy()
            sel["color"] = sel.risk.map(lambda k: RISK[k][1] + [230])
            sel["label"] = sel.risk.map(lambda k: RISK[k][0])
            sel["elev"] = sel.bikes_to_move.abs().clip(lower=1)
            sel["fill_pct"] = (sel.fill_ratio_now * 100).round(0)
            tip = {"html": "<b>{station_name}</b><br/>🚲 {num_bikes_available} vélos · 🅿️ {num_docks_available} places"
                           "<br/>Remplissage {fill_pct} %<br/><b>{label}</b> → {action} ({bikes_to_move})",
                   "style": {"backgroundColor": "#111833", "color": "#F8FAFC", "borderRadius": "10px"}}
            if mode.startswith("3D"):
                layer = pdk.Layer("ColumnLayer", sel, get_position=["lon", "lat"], get_elevation="elev",
                                  elevation_scale=22, radius=55, get_fill_color="color", pickable=True, extruded=True,
                                  auto_highlight=True)
            else:
                layer = pdk.Layer("ScatterplotLayer", sel, get_position=["lon", "lat"], get_fill_color="color",
                                  get_radius="40 + elev * 6", pickable=True, stroked=True, get_line_color=[7, 11, 24],
                                  line_width_min_pixels=1, auto_highlight=True)
            glow = pdk.Layer("ScatterplotLayer", prio[prio.risk == "ok"], get_position=["lon", "lat"],
                             get_fill_color=[52, 211, 153, 50], get_radius=35)
            deck([glow, layer], tooltip=tip)
            st.markdown('<div class="legend">' + "".join(
                f'<span><i style="background:{RISK[k][2]}"></i>{RISK[k][0]}</span>' for k in RISK) +
                "</div>", unsafe_allow_html=True)

        section("Tournée prioritaire", "Classement par score de priorité (urgence × vélos à déplacer × demande prévue).")
        tour = sel[sel.risk != "ok"].sort_values("priority_rank").head(60)
        st.dataframe(
            tour[["priority_rank", "station_name", "label", "action", "bikes_to_move", "num_bikes_available",
                  "capacity", "fill_ratio_now", "pred_departures", "pred_arrivals"]],
            hide_index=True, width="stretch", height=420,
            column_config={
                "priority_rank": st.column_config.NumberColumn("#", width="small"),
                "station_name": st.column_config.TextColumn("Station", width="large"),
                "label": "Risque", "action": "Action",
                "bikes_to_move": st.column_config.NumberColumn("Vélos ±", format="%+d"),
                "num_bikes_available": "Vélos", "capacity": "Capacité",
                "fill_ratio_now": st.column_config.ProgressColumn("Remplissage", min_value=0, max_value=1,
                                                                  format="percent"),
                "pred_departures": st.column_config.NumberColumn("Départs prévus", format="%.1f"),
                "pred_arrivals": st.column_config.NumberColumn("Arrivées prévues", format="%.1f")})

# ================================================================== 3. rebalancing
with tabs[2]:
    if not has("gold_rebalancing_daily"):
        st.info("Lancer le notebook 05 (agrégations avancées).")
    else:
        rd = t("gold_rebalancing_daily").sort_values("date")
        rd["date"] = pd.to_datetime(rd["date"])
        rn = t("gold_rebalancing_need")
        prof = t("gold_station_profile")
        kpis([("🚚", "Vélos à déplacer / jour", fmt(rd.forced_moves.mean()), "en camion, en moyenne", CYAN),
              ("📍", "Stations à visiter / jour", fmt(rd.stations_needing_truck.mean()),
               f"{rd.pct_stations_needing_truck.mean():.1f} % du réseau", INDIGO),
              ("📅", "Pire journée", fmt(rd.forced_moves.max()), f"le {rd.loc[rd.forced_moves.idxmax(), 'date']:%d/%m}",
               ROSE),
              ("🌅", "Stations « source »", fmt((prof.station_role == "source le matin (se vide)").sum()),
               "se vident le matin", AMBER),
              ("🌆", "Stations « puits »", fmt((prof.station_role == "puits le matin (se remplit)").sum()),
               "se remplissent le matin", VIOLET)])
        c1, c2 = st.columns([1.3, 1])
        with c1:
            section("Vélos à déplacer chaque jour",
                    "Stock relatif = somme cumulée (arrivées − départs) heure par heure ; si son amplitude dépasse "
                    "la capacité, un camion est indispensable.")
            fig = go.Figure()
            fig.add_scatter(x=rd.date, y=rd.forced_moves, fill="tozeroy", name="Vélos à déplacer",
                            line=dict(color=CYAN, width=2.5), fillcolor="rgba(34,211,238,.15)")
            fig.add_scatter(x=rd.date, y=rd.forced_moves.rolling(7, min_periods=1).mean(), name="Moyenne 7 j",
                            line=dict(color=AMBER, width=2, dash="dot"))
            show(fig, 360)
        with c2:
            section("Rôle des stations le matin", "Source = se vide (départs domicile) · puits = se remplit (bureaux).")
            colors = {"source le matin (se vide)": [251, 191, 36, 220], "puits le matin (se remplit)": [192, 132, 252, 220],
                      "equilibree": [148, 163, 184, 60]}
            pr = prof.dropna(subset=["lat", "lon"]).copy()
            pr["color"] = pr.station_role.map(colors)
            pr["r"] = 30 + pr.am_peak_net_flow_per_day.abs().fillna(0) * 4
            deck([pdk.Layer("ScatterplotLayer", pr, get_position=["lon", "lat"], get_fill_color="color", get_radius="r",
                            pickable=True)],
                 view=pdk.ViewState(latitude=40.74, longitude=-73.97, zoom=10.6), height=360,
                 tooltip={"text": "{station_name}\n{station_role}\nflux net matin : {am_peak_net_flow_per_day} / jour"})
        section("Stations qui demandent le plus souvent un camion")
        top = (rn.groupby("station_id").agg(jours=("needs_truck", "sum"), velos=("forced_moves", "mean"),
                                            heure_min=("hour_of_min_stock", "median"))
               .reset_index().merge(prof[["station_id", "station_name", "capacity", "station_role"]], on="station_id")
               .sort_values("jours", ascending=False).head(25))
        st.dataframe(top[["station_name", "station_role", "capacity", "jours", "velos", "heure_min"]], hide_index=True,
                     width="stretch", column_config={
                         "station_name": st.column_config.TextColumn("Station", width="large"), "station_role": "Rôle",
                         "capacity": "Capacité",
                         "jours": st.column_config.ProgressColumn("Jours avec camion", min_value=0, max_value=len(rd),
                                                                  format="%d"),
                         "velos": st.column_config.NumberColumn("Vélos / jour", format="%.1f"),
                         "heure_min": st.column_config.NumberColumn("Heure du stock min.", format="%d h")})

# ================================================================== 4. flows & zones
with tabs[3]:
    if not has("gold_od_flows"):
        st.info("Lancer le notebook 05 (agrégations avancées).")
    else:
        c1, c2 = st.columns(2)
        with c1:
            section("Grands flux origine → destination", "Épaisseur = nombre de trajets · bleu = départ, orange = arrivée.")
            od = t("gold_od_flows").sort_values("trips", ascending=False)
            n = st.slider("Nombre de liaisons", 20, 400, 150, step=10)
            top = od.head(n).dropna(subset=["origin_lat", "dest_lat"])
            deck([pdk.Layer("ArcLayer", top, get_source_position=["origin_lon", "origin_lat"],
                            get_target_position=["dest_lon", "dest_lat"], get_source_color=[34, 211, 238, 200],
                            get_target_color=[251, 191, 36, 220], get_width="trips / 250", get_height=0.6,
                            pickable=True, auto_highlight=True)],
                 tooltip={"text": "{origin_name} → {destination_name}\n{trips} trajets · {median_duration_min} min"})
        with c2:
            section("Demande par zone et par heure", "Grille d'environ 1 km · départs moyens par jour à l'heure choisie.")
            z = t("gold_zone_hourly")
            cc1, cc2 = st.columns(2)
            hour = cc1.slider("Heure", 0, 23, 8)
            we = cc2.toggle("Week-end", value=False)
            zh = z[(z.hour == hour) & (z.is_weekend == we)].copy()
            cap_v = max(zh.avg_departures_per_day.quantile(.97), 1)
            zh["elev"] = zh.avg_departures_per_day.clip(upper=cap_v)
            q = (zh.elev / cap_v).fillna(0)
            zh["color"] = [[int(34 + 217 * v), int(211 - 120 * v), int(238 - 100 * v), 215] for v in q]
            deck([pdk.Layer("ColumnLayer", zh, get_position=["cell_lon", "cell_lat"], get_elevation="elev",
                            elevation_scale=10, radius=380, get_fill_color="color", pickable=True, extruded=True,
                            auto_highlight=True, coverage=.9)],
                 tooltip={"text": "Zone {zone_id}\n{avg_departures_per_day} départs/jour à cette heure\n"
                                  "{stations} stations · rang {zone_rank_in_hour}"})
        section("Top liaisons")
        st.dataframe(top.head(30)[["rank_global", "origin_name", "destination_name", "trips", "median_duration_min",
                                   "avg_dist_km", "avg_speed_kmh", "member_pct", "am_peak_pct"]],
                     hide_index=True, width="stretch", column_config={
                         "rank_global": "#", "origin_name": "Départ", "destination_name": "Arrivée",
                         "trips": st.column_config.ProgressColumn("Trajets", min_value=0, max_value=int(top.trips.max()),
                                                                  format="%d"),
                         "median_duration_min": st.column_config.NumberColumn("Durée méd.", format="%.1f min"),
                         "avg_dist_km": st.column_config.NumberColumn("Distance", format="%.2f km"),
                         "avg_speed_kmh": st.column_config.NumberColumn("Vitesse", format="%.1f km/h"),
                         "member_pct": st.column_config.NumberColumn("Abonnés", format="%.0f %%"),
                         "am_peak_pct": st.column_config.NumberColumn("Pointe matin", format="%.0f %%")})

# ================================================================== 5. usage & weather (cube, distributions)
with tabs[4]:
    c1, c2 = st.columns(2)
    with c1:
        section("Qui roule, quand, sur quoi ?", "Cube OLAP (CUBE + grouping_id) : type de jour → moment → profil → vélo.")
        if has("gold_usage_cube"):
            cube = t("gold_usage_cube")
            base = cube[cube.grouping_level == 0].copy()
            base["rideable_type"] = base.rideable_type.map({"electric_bike": "électrique", "classic_bike": "classique"}) \
                .fillna(base.rideable_type)
            base["member_casual"] = base.member_casual.map({"member": "abonné", "casual": "occasionnel"})
            fig = px.sunburst(base, path=["day_type", "daypart", "member_casual", "rideable_type"], values="trips",
                              color="day_type", color_discrete_map={"semaine": INDIGO, "week-end": VIOLET})
            fig.update_traces(insidetextorientation="radial", hovertemplate="%{label}<br>%{value:,.0f} trajets"
                                                                            "<br>%{percentRoot:.1%} du total<extra></extra>",
                              marker=dict(line=dict(color="#070B18", width=1.5)))
            show(fig, 460)
    with c2:
        section("Effet de la météo", "Trajets moyens par heure (journée 7h-19h, semaine) selon la pluie et la température.")
        if has("gold_weather_effect"):
            we_ = t("gold_weather_effect")
            sub = we_[(we_.daypart == "jour 7h-19h") & (~we_.is_weekend)]
            piv = sub.pivot_table(index="rain_bucket", columns="temp_bucket", values="avg_trips_per_hour")
            fig = go.Figure(go.Heatmap(z=piv.values, x=[c[3:] for c in piv.columns], y=[r[3:] for r in piv.index],
                                       text=[[f"{v:,.0f}" if pd.notna(v) else "" for v in row] for row in piv.values],
                                       texttemplate="%{text}", textfont=dict(size=15, color="white"),
                                       colorscale=[[0, "#1E1B4B"], [.5, "#6D28D9"], [1, "#22D3EE"]], xgap=4, ygap=4,
                                       colorbar=dict(thickness=10, outlinewidth=0)))
            fig.update_yaxes(autorange="reversed")
            show(fig, 260)
        section("Durée des trajets", "Répartition par profil · courbe = pourcentage cumulé.")
        if has("gold_duration_distribution"):
            dd = t("gold_duration_distribution").sort_values("duration_bucket")
            dd["bucket"] = dd.duration_bucket.str[4:]
            dd["profil"] = dd.member_casual.map({"member": "Abonnés", "casual": "Occasionnels"})
            fig = px.bar(dd, x="bucket", y="pct_of_user_type", color="profil", barmode="group",
                         color_discrete_map={"Abonnés": CYAN, "Occasionnels": VIOLET},
                         labels={"bucket": "", "pct_of_user_type": "% des trajets", "profil": ""})
            show(fig, 260)
    section("Les stations les plus imprévisibles", "Coefficient de variation de la demande horaire (écart-type / moyenne).")
    if has("gold_station_volatility"):
        vol = t("gold_station_volatility").dropna(subset=["lat"])
        fig = px.scatter(vol, x="mean_dep_h", y="p99_dep_h", color="coef_variation", size="capacity",
                         hover_name="station_name", color_continuous_scale=["#22D3EE", "#818CF8", "#C084FC", "#FB7185"],
                         labels={"mean_dep_h": "Départs moyens / heure", "p99_dep_h": "Pic (99e percentile)",
                                 "coef_variation": "Variabilité"}, log_x=True, size_max=14, opacity=.75)
        show(fig, 380)

# ================================================================== 6. what-if forecast
with tabs[5]:
    stations = t("silver_stations").dropna(subset=["capacity"])
    stations = stations.merge(t("gold_station_profile")[["station_id", "departures"]], on="station_id") \
        .sort_values("departures", ascending=False)
    labels = (stations.station_name + "  ·  " + stations.station_id).tolist()
    c0, c1, c2, c3 = st.columns([2.2, 1, 1, 1])
    choice = c0.selectbox("📍 Station", labels)
    row = stations.iloc[labels.index(choice)]
    day = c1.date_input("📅 Jour", pd.Timestamp.now().date())
    temp = c2.slider("🌡️ Température (°C)", -10, 38, 18)
    rain = c3.slider("🌧️ Pluie (mm/h)", 0.0, 15.0, 0.0, 0.5)

    rows = [common.predict(row.station_id, pd.Timestamp(day) + pd.Timedelta(hours=h),
                           {"temperature_c": temp, "precipitation_mm": rain}) for h in range(24)]
    fc = pd.DataFrame(rows)
    fc["heure"] = pd.to_datetime(fc.hour_local).dt.hour
    cum = fc.pred_net_flow.cumsum()
    low_h = int(fc.heure[cum.idxmin()])
    cap = float(row.capacity)
    need = max(0.0, -cum.min())
    kpis([("🚲", "Départs prévus", fmt(fc.pred_departures.sum()), "sur la journée", CYAN),
          ("🅿️", "Arrivées prévues", fmt(fc.pred_arrivals.sum()), "sur la journée", VIOLET),
          ("⚖️", "Bilan de la journée", f"{cum.iloc[-1]:+.0f}", "vélos (arrivées − départs)", AMBER),
          ("🌅", "Vélos à prévoir à l'ouverture", fmt(need), f"stock le plus bas à {low_h} h", ROSE),
          ("🏗️", "Capacité", fmt(cap), "bornes", EMERALD)])

    fig = go.Figure()
    fig.add_bar(x=fc.heure, y=fc.pred_departures, name="Départs", marker_color=CYAN, opacity=.9)
    fig.add_bar(x=fc.heure, y=fc.pred_arrivals, name="Arrivées", marker_color=VIOLET, opacity=.9)
    fig.add_scatter(x=fc.heure, y=cum, name="Stock relatif cumulé", yaxis="y2", mode="lines+markers",
                    line=dict(color=AMBER, width=3, shape="spline"), marker=dict(size=6))
    fig.add_hline(y=0, line=dict(color="rgba(251,191,36,.35)", dash="dot"), yref="y2")
    fig.update_layout(barmode="group", bargap=.25, xaxis=dict(dtick=1, ticksuffix="h", range=[-.6, 23.6]),
                      yaxis=dict(title="vélos / heure"),
                      yaxis2=dict(overlaying="y", side="right", showgrid=False, title="stock relatif"))
    show(fig, 400)
    st.caption(f"Modèle utilisé : `{rows[0]['model_departures']}` · essaie de passer la pluie à 5 mm/h pour voir la demande "
               "chuter.")

# ================================================================== 7. model
with tabs[6]:
    m = t("gold_ml_metrics").set_index("target")
    dep, arr = m.loc["departures"], m.loc["arrivals"]
    kpis([("🎯", "MAE départs", f"{dep.model_mae:.3f}", f"baseline {dep.baseline_mae:.3f} · −{dep.mae_improvement_pct:.1f} %",
           CYAN),
          ("🎯", "MAE arrivées", f"{arr.model_mae:.3f}", f"baseline {arr.baseline_mae:.3f} · −{arr.mae_improvement_pct:.1f} %",
           VIOLET),
          ("🌐", "Erreur réseau (WAPE)", f"{dep.system_wape_model_pct:.1f} %", f"baseline {dep.system_wape_baseline_pct:.1f} %",
           EMERALD),
          ("🔥", "Heures chargées (MAE)", f"{dep.model_busy_mae:.2f}", f"baseline {dep.baseline_busy_mae:.2f}", AMBER)])
    c1, c2 = st.columns([1.6, 1])
    with c1:
        section("Réseau entier : observé vs prévu", "Dernière semaine, jamais vue à l'entraînement.")
        f = t("gold_demand_forecast")
        f["hour_ts"] = pd.to_datetime(f.hour_ts)
        net = f.groupby("hour_ts")[["departures", "pred_departures", "baseline_departures"]].sum().reset_index()
        fig = go.Figure()
        fig.add_scatter(x=net.hour_ts, y=net.departures, name="Observé", fill="tozeroy",
                        line=dict(color="rgba(148,163,184,.9)", width=1.5), fillcolor="rgba(148,163,184,.12)")
        fig.add_scatter(x=net.hour_ts, y=net.baseline_departures, name="Baseline", line=dict(color=AMBER, width=1.5,
                                                                                               dash="dot"))
        fig.add_scatter(x=net.hour_ts, y=net.pred_departures, name="Modèle ML", line=dict(color=CYAN, width=2.5))
        show(fig, 380)
    with c2:
        section("Modèle vs baseline", "Plus bas = meilleur.")
        comp = pd.DataFrame({"cible": ["Départs", "Départs", "Arrivées", "Arrivées"],
                             "type": ["Baseline", "Modèle", "Baseline", "Modèle"],
                             "MAE": [dep.baseline_mae, dep.model_mae, arr.baseline_mae, arr.model_mae]})
        fig = px.bar(comp, x="cible", y="MAE", color="type", barmode="group", text_auto=".3f",
                     color_discrete_map={"Baseline": "#475569", "Modèle": CYAN}, labels={"cible": "", "type": ""})
        fig.update_yaxes(range=[comp.MAE.min() * .9, comp.MAE.max() * 1.04])
        show(fig, 380)
    if has("gold_model_promotion_log"):
        section("MLOps : champion / challenger",
                "Un nouveau modèle n'est mis en production que s'il bat le champion actuel ET la baseline.")
        cols = st.columns(2)
        for col, (_, r) in zip(cols, t("gold_model_promotion_log").iterrows()):
            ok = bool(r.promoted)
            name = "Départs" if "departures" in r.model else "Arrivées"
            badge = (f'<span class="badge" style="background:{EMERALD}">PROMU ✓</span>' if ok
                     else f'<span class="badge" style="background:{ROSE}">REJETÉ ✗</span>')
            col.markdown(f"""<div class="card"><div class="t">{name} · challenger v{int(r.challenger_version)} {badge}</div>
              <div class="m">MAE challenger <b>{r.challenger_mae:.4f}</b> · champion v{int(r.champion_version_before)}
              <b>{r.champion_mae_before:.4f}</b> · baseline {r.baseline_mae:.4f}<br/>
              {'Le challenger devient le nouveau champion.' if ok else 'Le champion actuel reste en production.'}
              </div></div>""", unsafe_allow_html=True)
