"""SmartBike AI - operator command center (Streamlit).

Run:  streamlit run app/dashboard.py
Complements Power BI: Power BI = analysis for managers; this app = action for the field team
(live map of stations to visit now, rebalancing needs, flows, what-if forecast, model monitoring).
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

import numpy as np
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
# Palette taken from the team portfolio (light + dark): ink #0B1020 · accent #2563EB · sky #8DDCFF · green #22C55E
# · red #E5484D · gold · slate. Fonts: Space Grotesk (titles), Manrope (text), JetBrains Mono (labels).
# Light/dark follows the viewer's choice (menu ⋮ > Settings, or the OS preference) via st.context.theme.
DARK = (st.context.theme.type or "light") == "dark"
P = dict(  # surface tokens
    bg="#080B12", plate="#111827", line="rgba(184,196,214,.14)", text="#F8FAFC", muted="#94A3B8", ink="#E4EAF1",
    accent="#8DDCFF", hero_shadow="0 30px 80px -30px rgba(0,0,0,.75)", grid="rgba(184,196,214,.08)",
    feat_bg="#8DDCFF", feat_val="#080B12", feat_lbl="#0B1020", feat_sub="#1D3FA8",
) if DARK else dict(
    bg="#F8FAFC", plate="#FFFFFF", line="#E2E8F0", text="#0B1020", muted="#526174", ink="#0B1020",
    accent="#2563EB", hero_shadow="0 30px 70px -35px rgba(16,24,39,.3)", grid="#EEF2F7",
    feat_bg="#0B1020", feat_val="#FFFFFF", feat_lbl="#8DDCFF", feat_sub="#B8C4D6",
)
# (variable names kept from the first theme)
CYAN, INDIGO, VIOLET, AMBER, ROSE, EMERALD, SLATE = ("#38BDF8", "#3B82F6" if DARK else "#2563EB", "#1D3FA8", "#A8822F",
                                                      "#E5484D", "#22C55E", P["muted"])
INK, SKY, GOLD, BRONZE, MUTED, LINE = P["ink"], "#8DDCFF", "#C9A54A", "#D9A57A", "#94A3B8", P["line"]
ORANGE, PINK, TEAL, YELLOW = BRONZE, SKY, CYAN, GOLD
INK_RGB = [228, 234, 241] if DARK else [11, 16, 32]
BAR_SOFT = "#334155" if DARK else "#C7D2E3"
RISK = {  # label, colour (rgb for maps), hex
    "vide maintenant": ("Vide maintenant", [229, 72, 77], ROSE),
    "risque vide": ("Risque de vide", [201, 165, 74], GOLD),
    "saturee maintenant": ("Saturée maintenant", INK_RGB, INK),
    "risque saturee": ("Risque de saturation", [59, 130, 246], INDIGO),
    "ok": ("OK", [34, 197, 94], EMERALD),
}
MAP_STYLE = ("https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json" if DARK
             else "https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json")
NYC = pdk.ViewState(latitude=40.735, longitude=-73.965, zoom=11.2, pitch=45, bearing=-18)
TAB_COLORS = [INK] * 7
SEQ = ([[0, "#111827"], [.35, "#1D3FA8"], [.7, "#3B82F6"], [1, "#8DDCFF"]] if DARK
       else [[0, "#F8FAFC"], [.3, "#8DDCFF"], [.65, "#2563EB"], [1, "#0B1020"]])
SEQ_STRONG = ([[0, "#1E3A8A"], [.5, "#1D4ED8"], [1, "#3B82F6"]] if DARK
              else [[0, "#60A5FA"], [.5, "#2563EB"], [1, "#0B1020"]])
HEAT_TEXT = "#FFFFFF"


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode() if path.exists() else ""


_vars = ":root { " + " ".join(f"--{k.replace('_', '-')}:{v};" for k, v in P.items()) + " }"
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Manrope:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');
VARS
html, body, [class*="css"], .stMarkdown, .stText, button, input { font-family: 'Manrope', sans-serif !important; }
.stApp { background: var(--bg); }
footer { visibility: hidden; }
header[data-testid="stHeader"] { background: transparent; }
.block-container { padding-top: 1.2rem; padding-bottom: 3rem; max-width: 1440px; }
section[data-testid="stSidebar"] { background: var(--plate); border-right: 1px solid var(--line); }
h1, h2, h3, .section, .kpi .val, .hero h1 { font-family: 'Space Grotesk', sans-serif !important; }

.hero { display:flex; align-items:center; gap:26px; padding:26px 30px; border-radius:22px; background:var(--plate);
  border:1px solid var(--line); box-shadow: var(--hero-shadow); margin-bottom: 20px; }
.hero img { width:92px; height:92px; border-radius:22px; }
.hero h1 { margin:0; font-size:2.6rem; font-weight:700; letter-spacing:-1.5px; color:var(--text); line-height:1; }
.hero h1 .o { color:transparent; -webkit-text-stroke: 1.4px var(--text); font-weight:500; }
.hero .tag { font-family:'Space Grotesk',sans-serif; font-size:1.15rem; color:var(--text); margin-top:8px; font-weight:500; }
.hero .mono { font-family:'JetBrains Mono',monospace; font-size:.85rem; color:var(--muted); margin-top:6px; }
.hero .mono b { color:var(--accent); font-weight:500; }
.pill { display:inline-flex; align-items:center; gap:7px; padding:5px 12px; border-radius:999px; font-size:.78rem;
  font-weight:600; color:var(--muted); background:var(--plate); border:1px solid var(--line); margin-right:6px;
  margin-top:12px; }
.pill.dark { background:var(--feat-bg); color:var(--feat-val); border-color:var(--feat-bg); }
.dot { width:8px; height:8px; border-radius:50%; background:#22C55E; animation: pulse 1.8s infinite; }
@keyframes pulse { 0% { box-shadow:0 0 0 0 rgba(34,197,94,.55); } 70% { box-shadow:0 0 0 8px rgba(34,197,94,0); }
  100% { box-shadow:0 0 0 0 rgba(34,197,94,0); } }

.kpi-grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap:14px; margin: 6px 0 20px; }
.kpi { position:relative; padding:16px 18px; border-radius:18px; background:var(--plate); border:1px solid var(--line);
  transition: transform .18s ease, box-shadow .18s ease; }
.kpi:hover { transform: translateY(-2px); box-shadow: 0 20px 40px -28px rgba(16,24,39,.45); }
.kpi:first-child { background:var(--feat-bg); border-color:var(--feat-bg); }
.kpi:first-child .val { color:var(--feat-val); } .kpi:first-child .lbl { color:var(--feat-lbl); }
.kpi:first-child .sub { color:var(--feat-sub); }
.kpi .lbl { font-family:'JetBrains Mono',monospace; color:var(--muted); font-size:.7rem; text-transform:uppercase;
  letter-spacing:.06em; display:flex; align-items:center; gap:7px; }
.kpi .lbl:before { content:""; width:7px; height:7px; border-radius:50%; background:var(--c); }
.kpi .val { color:var(--text); font-size:1.7rem; white-space:nowrap; font-weight:700; letter-spacing:-.8px;
  margin-top:6px; }
.kpi .sub { color:var(--muted); font-size:.8rem; font-weight:600; }
.kpi .ico { display:none; }

.section { font-size:1.2rem; font-weight:600; color:var(--text); margin: 14px 0 0; letter-spacing:-.3px; }
.hint { font-family:'JetBrains Mono',monospace; color:var(--muted); font-size:.76rem; margin:2px 0 10px; }

.stTabs [role="tablist"] { gap:6px; flex-wrap:wrap; background:var(--plate); border:1px solid var(--line); padding:6px;
  border-radius:16px; }
.stTabs [role="tab"] { height:40px; border-radius:11px; padding:0 15px; }
.stTabs [role="tab"] p { font-weight:600; color:var(--muted); }
.stTabs [role="tab"][aria-selected="true"] { background:var(--feat-bg) !important; }
.stTabs [role="tab"][aria-selected="true"] p { color:var(--feat-val) !important; }
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] { display:none; }

div[data-testid="stPlotlyChart"], div[data-testid="stDeckGlJsonChart"], div[data-testid="stDataFrame"] {
  border-radius:18px; overflow:hidden; border:1px solid var(--line); background:var(--plate); }
.card { padding:14px 16px; border-radius:16px; background:var(--plate); border:1px solid var(--line);
  border-left:4px solid var(--c); margin-bottom:10px; }
.card .t { font-family:'Space Grotesk',sans-serif; font-weight:600; color:var(--text); }
.card .m { color:var(--muted); font-size:.86rem; }
.badge { padding:3px 10px; border-radius:999px; font-size:.7rem; font-weight:700; color:white;
  font-family:'JetBrains Mono',monospace; }
.legend { display:flex; flex-wrap:wrap; gap:14px; color:var(--muted); font-size:.82rem; font-weight:600; margin:6px 0 10px; }
.legend i { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:6px; }

/* operator sidebar */
.sb-h { font-family:'JetBrains Mono',monospace; font-size:.7rem; text-transform:uppercase; letter-spacing:.08em;
  color:var(--muted); margin:18px 0 8px; }
.sb-fresh { padding:10px 12px; border-radius:12px; border:1px solid var(--line); font-size:.84rem; color:var(--text); }
.sb-fresh span { color:var(--muted); font-size:.76rem; }
.sb-fresh .sb-dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:7px; }
.sb-fresh.ok .sb-dot { background:#22C55E; } .sb-fresh.old .sb-dot { background:#C9A54A; }
.sb-fresh.old { border-color:rgba(201,165,74,.55); }
.sb-grid { display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:10px; }
.sb-grid div { padding:10px; border-radius:12px; border:1px solid var(--line); background:var(--bg); }
.sb-grid b { display:block; font-family:'Space Grotesk',sans-serif; font-size:1.35rem; color:var(--text);
  line-height:1.1; }
.sb-grid span { font-size:.72rem; color:var(--muted); font-weight:600; }
.sb-row { display:flex; align-items:center; gap:9px; padding:7px 0; border-bottom:1px solid var(--line); }
.sb-row i { flex:none; width:8px; height:8px; border-radius:50%; }
.sb-row div { flex:1; min-width:0; }
.sb-row b { display:block; font-size:.8rem; color:var(--text); white-space:nowrap; overflow:hidden;
  text-overflow:ellipsis; }
.sb-row span { font-size:.7rem; color:var(--muted); }
.sb-row em { font-style:normal; font-family:'JetBrains Mono',monospace; font-weight:600; font-size:.85rem; }
.sb-note { font-size:.82rem; color:var(--text); line-height:1.45; padding:10px 12px; border-radius:12px;
  border:1px solid var(--line); }
.sb-note span { color:var(--muted); font-size:.74rem; }
</style>
""".replace("VARS", _vars), unsafe_allow_html=True)

PLOT = dict(template="plotly_dark" if DARK else "plotly_white", paper_bgcolor=P["plate"], plot_bgcolor=P["plate"],
            font=dict(family="Manrope, sans-serif", color=P["muted"], size=12), margin=dict(l=14, r=14, t=40, b=12),
            hoverlabel=dict(bgcolor=P["feat_bg"], bordercolor=P["feat_bg"], font_color=P["feat_val"]),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, bgcolor="rgba(0,0,0,0)"),
            colorway=[INDIGO, INK, SKY, EMERALD, GOLD, ROSE, MUTED])


def show(fig: go.Figure, height: int = 340) -> None:
    fig.update_layout(**PLOT, height=height)
    fig.update_xaxes(gridcolor=P["grid"], zeroline=False)
    fig.update_yaxes(gridcolor=P["grid"], zeroline=False)
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
def _t(name: str, version: float) -> pd.DataFrame:
    return common.table(name).copy()


def t(name: str) -> pd.DataFrame:
    """Cached per table *version*: a notebook re-run shows up on the next page refresh, no restart needed."""
    return _t(name, common.table_version(name))


def has(name: str) -> bool:
    return (common.TABLES / name).exists()


def fmt(n: float) -> str:
    return f"{n:,.0f}".replace(",", " ")


def ago(delta: pd.Timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "à l'instant"
    if minutes < 60:
        return f"il y a {minutes} min"
    hours = minutes // 60
    return f"il y a {hours} h" if hours < 48 else f"il y a {hours // 24} jours"


@st.cache_data(ttl=60, show_spinner="Lecture du flux GBFS…")
def live_now() -> pd.DataFrame:
    return common.live_status(timeout=10)


FR_DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
FRESH_MIN = 60  # a forecast snapshot older than this is flagged as stale


def sidebar_operator_panel(prio: pd.DataFrame | None) -> None:
    """Operator panel: data freshness, what to do now, top urgencies, today's truck need, live check."""
    now = pd.Timestamp.now(tz=common.NY_TZ)

    # 1. freshness of the forecast snapshot (notebook 04)
    st.markdown('<div class="sb-h">Relevé utilisé</div>', unsafe_allow_html=True)
    if prio is None:
        st.warning("Aucun relevé : lancer le notebook 04.")
        return
    snap_ts = pd.Timestamp(prio.snapshot_time_local.iloc[0])
    snap_ts = snap_ts.tz_convert(common.NY_TZ) if snap_ts.tzinfo else snap_ts.tz_localize(common.NY_TZ)
    age = now - snap_ts
    if age < pd.Timedelta(minutes=-5):   # a snapshot "from the future" means this computer's clock is late
        st.warning(f"L'horloge de cet ordinateur retarde d'environ {round(-age.total_seconds() / 3600, 1)} h : "
                   "l'âge du relevé ne peut pas être calculé. Synchroniser l'heure de Windows.")
        age = pd.Timedelta(0)
    fresh = age <= pd.Timedelta(minutes=FRESH_MIN)
    st.markdown(f'<div class="sb-fresh {"ok" if fresh else "old"}"><span class="sb-dot"></span>'
                f'<b>{snap_ts:%d/%m %H:%M}</b> (New York) · {ago(age)}<br/>'
                f'<span>prévision à {int(prio.horizon_hours.iloc[0])} h · {len(prio)} stations relevées</span></div>',
                unsafe_allow_html=True)
    if not fresh:
        st.caption("Relevé ancien : relancer le notebook 04 (ou le pipeline) pour une tournée à jour.")

    # 2. what to do now
    todo = prio[prio.risk != "ok"]
    add = int(prio.loc[prio.bikes_to_move > 0, "bikes_to_move"].sum())
    remove = int(-prio.loc[prio.bikes_to_move < 0, "bikes_to_move"].sum())
    ok_n = int((prio.risk == "ok").sum())
    st.markdown('<div class="sb-h">À faire maintenant</div>', unsafe_allow_html=True)
    st.markdown(f"""<div class="sb-grid">
      <div><b>{fmt(len(todo))}</b><span>stations à visiter</span></div>
      <div><b>{fmt(int((todo.risk.isin(['vide maintenant', 'saturee maintenant'])).sum()))}</b>
           <span>déjà en rupture</span></div>
      <div><b style="color:{EMERALD}">+{fmt(add)}</b><span>vélos à livrer</span></div>
      <div><b style="color:{ROSE}">−{fmt(remove)}</b><span>vélos à retirer</span></div>
    </div>""", unsafe_allow_html=True)
    st.progress(ok_n / len(prio), text=f"{fmt(ok_n)} / {fmt(len(prio))} stations OK ({ok_n / len(prio):.0%})")

    # 3. top urgencies
    st.markdown('<div class="sb-h">5 urgences</div>', unsafe_allow_html=True)
    rows = []
    for _, r in todo.sort_values("priority_rank").head(5).iterrows():
        sign = f"+{int(r.bikes_to_move)}" if r.bikes_to_move > 0 else str(int(r.bikes_to_move))
        color = RISK[r.risk][2]
        rows.append(f'<div class="sb-row"><i style="background:{color}"></i><div><b>{r.station_name}</b>'
                    f'<span>{RISK[r.risk][0]} · {int(r.num_bikes_available)}/{int(r.capacity)} vélos</span></div>'
                    f'<em style="color:{EMERALD if r.bikes_to_move > 0 else ROSE}">{sign}</em></div>')
    st.markdown("".join(rows), unsafe_allow_html=True)
    st.caption("Liste complète : onglet « Interventions live ».")

    # 4. today's truck need, from the historical rebalancing table (same weekday)
    if has("gold_rebalancing_daily"):
        rd = t("gold_rebalancing_daily")
        dow = pd.to_datetime(rd.date).dt.day_of_week
        same = rd[dow == now.day_of_week]
        if not same.empty:
            st.markdown('<div class="sb-h">Camion aujourd\'hui</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="sb-note">Un <b>{FR_DAYS[now.day_of_week]}</b>, il faut en moyenne '
                        f'<b>{fmt(same.forced_moves.mean())} vélos</b> à déplacer sur '
                        f'<b>{fmt(same.stations_needing_truck.mean())} stations</b> '
                        f'(max observé {fmt(same.forced_moves.max())}).</div>', unsafe_allow_html=True)

    # 5. live check against the public feed (no forecast, just current state)
    st.markdown('<div class="sb-h">État en direct</div>', unsafe_allow_html=True)
    if st.button("Vérifier maintenant", width="stretch", type="primary"):
        st.session_state["live_check"] = True
    if st.session_state.get("live_check"):
        try:
            live = live_now()
            last = live.last_reported_utc.max().tz_convert(common.NY_TZ)
            st.markdown(f'<div class="sb-note"><b style="color:{ROSE}">{fmt(live.is_empty.sum())}</b> vides · '
                        f'<b style="color:{INK}">{fmt(live.is_full.sum())}</b> pleines sur {fmt(len(live))} stations'
                        f'<br/><span>flux mis à jour {last:%H:%M} (New York)</span></div>', unsafe_allow_html=True)
        except Exception as exc:  # noqa: BLE001 - network errors must not break the app
            st.error(f"Flux GBFS injoignable ({type(exc).__name__}).")

    # 6. reference
    with st.expander("Architecture"):
        st.markdown("""
- **Azure** · ADLS Gen2 · Key Vault · Policy
- **Fabric** · Lakehouse Delta (Bronze → Silver → Gold)
- **Spark** · 5 notebooks · agrégations avancées
- **Temps réel** · Eventstream → KQL → Activator
- **ML** · HistGradientBoosting + MLflow
""")
    with st.expander("Outils"):
        st.markdown("- API REST : [localhost:8000/docs](http://localhost:8000/docs)\n"
                    "- Alertes : `python scripts/alert_monitor.py`\n"
                    "- Rapport : `report/SmartBike_AI_Rapport_Technique.pdf`")
    st.caption("Projet CDDA 2026-2027 · Pr. Ibrahim Rahhal")


# ------------------------------------------------------------------ header + sidebar
daily = t("gold_daily_kpis").sort_values("date")
daily["date"] = pd.to_datetime(daily["date"])
prio = t("gold_station_priority") if has("gold_station_priority") else None
snap = str(prio.snapshot_time_local.iloc[0])[:16] if prio is not None else "—"

st.markdown(f"""
<div class="hero">
  <img src="data:image/png;base64,{_b64(LOGO)}"/>
  <div>
    <h1>SMARTBIKE <span class="o">AI</span></h1>
    <div class="tag">Anticiper les stations vides ou saturées, avant qu'elles ne le soient.</div>
    <div class="mono">Citi Bike New York · Lakehouse Gold · modèle <b>MLflow champion</b></div>
    <span class="pill"><span class="dot"></span> Flux GBFS · {snap}</span>
    <span class="pill">{daily.date.min():%d/%m} → {daily.date.max():%d/%m/%Y}</span>
    <span class="pill dark">{fmt(daily.trips.sum())} trajets analysés</span>
  </div>
</div>""", unsafe_allow_html=True)

with st.sidebar:
    if LOGO.exists():
        st.image(str(ROOT / "assets" / ("logo_full_dark.png" if DARK else "logo_full.png")), width="stretch")
    sidebar_operator_panel(prio)

tabs = st.tabs(["Vue d'ensemble", "Interventions live", "Tournée camion", "Rééquilibrage", "Flux & zones",
                "Usages & météo", "Prévision", "Modèle"])

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
                marker=dict(color=[SKY if w else BAR_SOFT for w in d.is_weekend]))
    fig.add_scatter(x=d.date, y=d.ma7, name="Moyenne 7 jours", line=dict(color=INDIGO, width=3, shape="spline"))
    fig.add_scatter(x=d.date, y=d.avg_temp_c, name="Température (°C)", yaxis="y2",
                    line=dict(color=GOLD, width=2, dash="dot"))
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
                                   colorscale=SEQ,
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
                                               line=dict(color="#FFFFFF", width=3)),
                                   textinfo="none", sort=False))
            fig.update_layout(showlegend=False, annotations=[dict(text=f"<b>{(prio.risk == 'ok').mean() * 100:.0f}%</b>"
                                                                       "<br>OK", showarrow=False,
                                                                  font=dict(size=22, color=P["text"]))])
            show(fig, 230)
        with c1:
            sel = prio[prio.risk.isin(risks)].copy()
            sel["color"] = sel.risk.map(lambda k: RISK[k][1] + [230])
            sel["label"] = sel.risk.map(lambda k: RISK[k][0])
            sel["elev"] = sel.bikes_to_move.abs().clip(lower=1)
            sel["fill_pct"] = (sel.fill_ratio_now * 100).round(0)
            tip = {"html": "<b>{station_name}</b><br/>🚲 {num_bikes_available} vélos · 🅿️ {num_docks_available} places"
                           "<br/>Remplissage {fill_pct} %<br/><b>{label}</b> → {action} ({bikes_to_move})",
                   "style": {"backgroundColor": P["feat_bg"], "color": P["feat_val"], "borderRadius": "10px",
                             "fontFamily": "Manrope, sans-serif"}}
            if mode.startswith("3D"):
                layer = pdk.Layer("ColumnLayer", sel, get_position=["lon", "lat"], get_elevation="elev",
                                  elevation_scale=22, radius=55, get_fill_color="color", pickable=True, extruded=True,
                                  auto_highlight=True)
            else:
                layer = pdk.Layer("ScatterplotLayer", sel, get_position=["lon", "lat"], get_fill_color="color",
                                  get_radius="40 + elev * 6", pickable=True, stroked=True, get_line_color=[255, 255, 255],
                                  line_width_min_pixels=1, auto_highlight=True)
            glow = pdk.Layer("ScatterplotLayer", prio[prio.risk == "ok"], get_position=["lon", "lat"],
                             get_fill_color=[34, 197, 94, 55], get_radius=35)
            deck([glow, layer], tooltip=tip)
            st.markdown('<div class="legend">' + "".join(
                f'<span><i style="background:{RISK[k][2]}"></i>{RISK[k][0]}</span>' for k in RISK) +
                "</div>", unsafe_allow_html=True)

        section("Tournée prioritaire", "Classement par score de priorité (urgence × vélos à déplacer × demande prévue).")
        tour = sel[sel.risk != "ok"].sort_values("priority_rank").head(60)
        if "p_empty" in tour:
            tour = tour.assign(p_risk=tour[["p_empty", "p_full"]].max(axis=1))
        st.dataframe(
            tour[[c for c in ["priority_rank", "station_name", "label", "p_risk", "action", "bikes_to_move",
                              "num_bikes_available", "capacity", "fill_ratio_now", "pred_departures",
                              "pred_arrivals"] if c in tour]],
            hide_index=True, width="stretch", height=420,
            column_config={
                "priority_rank": st.column_config.NumberColumn("#", width="small"),
                "station_name": st.column_config.TextColumn("Station", width="large"),
                "label": "Risque", "action": "Action",
                "p_risk": st.column_config.ProgressColumn(f"P(rupture < {int(prio.horizon_hours.iloc[0])} h)",
                                                          min_value=0, max_value=1, format="percent"),
                "bikes_to_move": st.column_config.NumberColumn("Vélos ±", format="%+d"),
                "num_bikes_available": "Vélos", "capacity": "Capacité",
                "fill_ratio_now": st.column_config.ProgressColumn("Remplissage", min_value=0, max_value=1,
                                                                  format="percent"),
                "pred_departures": st.column_config.NumberColumn("Départs prévus", format="%.1f"),
                "pred_arrivals": st.column_config.NumberColumn("Arrivées prévues", format="%.1f")})

# ================================================================== 2b. truck tours (routing)
TRUCK_RGB = [[37, 99, 235], [201, 165, 74], [34, 197, 94], [229, 72, 77], [141, 220, 255], [148, 163, 184]]


@st.cache_data(show_spinner="Optimisation de la tournée (OR-Tools)…")
def plan_tours(prio_key: str, n_trucks: int, capacity: int, shift: int, top_n: int):
    from smartbike.routing import FleetConfig, candidates_from_priority, greedy_plan, optimise_plan
    cfg = FleetConfig(n_trucks=n_trucks, truck_capacity=capacity, shift_minutes=shift, time_limit_s=8)
    cand = candidates_from_priority(prio, top_n=top_n)
    g, kg = greedy_plan(cand, cfg)
    o, ko = optimise_plan(cand, cfg)
    return o, pd.DataFrame([kg, ko])


with tabs[2]:
    if prio is None:
        st.warning("Lancer le notebook 04.")
    else:
        section("Tournée optimisée des camions",
                "Quelles stations, dans quel ordre, combien de vélos charger / décharger : problème de collecte et "
                "livraison avec capacité, résolu par OR-Tools, comparé à « suivre la liste de priorité ».")
        c1, c2, c3, c4 = st.columns(4)
        n_trucks = c1.slider("Camions", 1, 6, 3)
        capacity = c2.slider("Capacité (vélos)", 10, 60, 30, step=5)
        shift = c3.slider("Durée de tournée (min)", 60, 480, 240, step=30)
        top_n = c4.slider("Stations candidates", 20, 150, 80, step=10)
        plan, comp = plan_tours(snap, n_trucks, capacity, shift, top_n)
        g, o = comp.iloc[0], comp.iloc[1]
        gain = (o.bikes_moved / g.bikes_moved - 1) * 100 if g.bikes_moved else 0
        kpis([("🚚", "Vélos déplacés", fmt(o.bikes_moved), f"{gain:+.0f} % vs liste de priorité", INDIGO),
              ("🎯", "Urgence couverte", f"{o.value_share:.0%}", f"liste de priorité : {g.value_share:.0%}", EMERALD),
              ("🛣️", "Distance", f"{o.km:.0f} km", f"liste de priorité : {g.km:.0f} km", GOLD),
              ("⚡", "Vélos / km", f"{o.bikes_per_km:.1f}", f"liste de priorité : {g.bikes_per_km:.1f}", SKY),
              ("📍", "Arrêts", fmt(o.stops), f"sur {fmt(o.candidates)} candidates", ROSE)])
        left, right = st.columns([3, 2])
        with left:
            paths, pts = [], []
            for truck, r in plan.groupby("truck"):
                color = TRUCK_RGB[(truck - 1) % len(TRUCK_RGB)]
                paths.append({"truck": f"Camion {truck}", "path": r[["lon", "lat"]].to_numpy().tolist(),
                              "color": color})
                for _, x in r.iterrows():
                    pts.append({"lon": x.lon, "lat": x.lat, "color": color + [235], "label": f"{truck}.{x.stop}",
                                "txt": f"Camion {truck} · arrêt {x.stop}\n{x.station_name}\n"
                                       f"{'livrer' if x.delta > 0 else 'retirer'} {abs(int(x.delta))} vélos · "
                                       f"charge après : {int(x.load_after)}"})
            pts = pd.DataFrame(pts)
            deck([pdk.Layer("PathLayer", pd.DataFrame(paths), get_path="path", get_color="color", width_scale=1,
                            width_min_pixels=4, rounded=True, pickable=False),
                  pdk.Layer("ScatterplotLayer", pts, get_position=["lon", "lat"], get_fill_color="color",
                            get_radius=90, stroked=True, get_line_color=[255, 255, 255], line_width_min_pixels=2,
                            pickable=True),
                  pdk.Layer("TextLayer", pts, get_position=["lon", "lat"], get_text="label", get_size=12,
                            get_color=[255, 255, 255] if DARK else [11, 16, 32], get_pixel_offset=[0, -16])],
                 view=pdk.ViewState(latitude=float(plan.lat.mean()), longitude=float(plan.lon.mean()), zoom=11.6,
                                    pitch=0), tooltip={"text": "{txt}"}, height=560)
        with right:
            truck = st.selectbox("Feuille de route", sorted(plan.truck.unique()), format_func=lambda k: f"Camion {k}")
            r = plan[plan.truck == truck]
            st.dataframe(r.assign(action=np.where(r.delta > 0, "livrer", "retirer"), velos=r.delta.abs())[
                ["stop", "station_name", "action", "velos", "load_after", "minutes_cum", "leg_km"]],
                hide_index=True, width="stretch", height=480, column_config={
                    "stop": "#", "station_name": st.column_config.TextColumn("Station", width="large"),
                    "action": "Action", "velos": "Vélos", "load_after": "Charge",
                    "minutes_cum": st.column_config.NumberColumn("Minute", format="%.0f"),
                    "leg_km": st.column_config.NumberColumn("km", format="%.1f")})
        st.caption("Hypothèses : camions partis vides, 15 km/h, 4 min par arrêt + 15 s par vélo, distance réelle ≈ "
                   "1,3 × vol d'oiseau. Une station est visitée au plus une fois (besoin plafonné à la capacité).")

# ================================================================== 3. rebalancing
with tabs[3]:
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
                            line=dict(color=INDIGO, width=2.5), fillcolor="rgba(37,99,235,.10)")
            fig.add_scatter(x=rd.date, y=rd.forced_moves.rolling(7, min_periods=1).mean(), name="Moyenne 7 j",
                            line=dict(color=INK, width=2, dash="dot"))
            show(fig, 360)
        with c2:
            section("Rôle des stations le matin", "Source = se vide (départs domicile) · puits = se remplit (bureaux).")
            colors = {"source le matin (se vide)": [201, 165, 74, 220], "puits le matin (se remplit)": [37, 99, 235, 220],
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
with tabs[4]:
    if not has("gold_od_flows"):
        st.info("Lancer le notebook 05 (agrégations avancées).")
    else:
        c1, c2 = st.columns(2)
        with c1:
            section("Grands flux origine → destination", "Épaisseur = trajets · bleu ciel = départ, bleu nuit = arrivée.")
            od = t("gold_od_flows").sort_values("trips", ascending=False)
            n = st.slider("Nombre de liaisons", 20, 400, 150, step=10)
            top = od.head(n).dropna(subset=["origin_lat", "dest_lat"])
            deck([pdk.Layer("ArcLayer", top, get_source_position=["origin_lon", "origin_lat"],
                            get_target_position=["dest_lon", "dest_lat"], get_source_color=[141, 220, 255, 230],
                            get_target_color=INK_RGB + [230], get_width="trips / 250", get_height=0.6,
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
            lo, hi = ([29, 63, 168], [141, 220, 255]) if DARK else ([141, 220, 255], [11, 16, 32])
            zh["color"] = [[int(lo[k] + (hi[k] - lo[k]) * v) for k in range(3)] + [225] for v in q]
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
with tabs[5]:
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
                              color="day_type", color_discrete_map={"semaine": INDIGO, "week-end": INK})
            fig.update_traces(insidetextorientation="radial", hovertemplate="%{label}<br>%{value:,.0f} trajets"
                                                                            "<br>%{percentRoot:.1%} du total<extra></extra>",
                              marker=dict(line=dict(color="#FFFFFF", width=2)))
            show(fig, 460)
    with c2:
        section("Effet de la météo", "Trajets moyens par heure (journée 7h-19h, semaine) selon la pluie et la température.")
        if has("gold_weather_effect"):
            we_ = t("gold_weather_effect")
            sub = we_[(we_.daypart == "jour 7h-19h") & (~we_.is_weekend)]
            piv = sub.pivot_table(index="rain_bucket", columns="temp_bucket", values="avg_trips_per_hour")
            fig = go.Figure(go.Heatmap(z=piv.values, x=[c[3:] for c in piv.columns], y=[r[3:] for r in piv.index],
                                       text=[[f"{v:,.0f}" if pd.notna(v) else "" for v in row] for row in piv.values],
                                       texttemplate="%{text}", textfont=dict(size=15, color=HEAT_TEXT),
                                       colorscale=SEQ_STRONG, xgap=4, ygap=4,
                                       colorbar=dict(thickness=10, outlinewidth=0)))
            fig.update_yaxes(autorange="reversed")
            show(fig, 260)
        section("Durée des trajets", "Répartition par profil · courbe = pourcentage cumulé.")
        if has("gold_duration_distribution"):
            dd = t("gold_duration_distribution").sort_values("duration_bucket")
            dd["bucket"] = dd.duration_bucket.str[4:]
            dd["profil"] = dd.member_casual.map({"member": "Abonnés", "casual": "Occasionnels"})
            fig = px.bar(dd, x="bucket", y="pct_of_user_type", color="profil", barmode="group",
                         color_discrete_map={"Abonnés": INDIGO, "Occasionnels": SKY},
                         labels={"bucket": "", "pct_of_user_type": "% des trajets", "profil": ""})
            show(fig, 260)
    section("Les stations les plus imprévisibles", "Coefficient de variation de la demande horaire (écart-type / moyenne).")
    if has("gold_station_volatility"):
        vol = t("gold_station_volatility").dropna(subset=["lat"])
        fig = px.scatter(vol, x="mean_dep_h", y="p99_dep_h", color="coef_variation", size="capacity",
                         hover_name="station_name", color_continuous_scale=["#8DDCFF", "#2563EB", INK, "#E5484D"],
                         labels={"mean_dep_h": "Départs moyens / heure", "p99_dep_h": "Pic (99e percentile)",
                                 "coef_variation": "Variabilité"}, log_x=True, size_max=14, opacity=.75)
        show(fig, 380)

# ================================================================== 6. what-if forecast
with tabs[6]:
    stations = t("silver_stations").dropna(subset=["capacity"])
    stations = stations.merge(t("gold_station_profile")[["station_id", "departures"]], on="station_id") \
        .sort_values("departures", ascending=False)
    labels = (stations.station_name + "  ·  " + stations.station_id).tolist()
    c0, c1, c2, c3 = st.columns([2.2, 1, 1, 1])
    choice = c0.selectbox("Station", labels)
    row = stations.iloc[labels.index(choice)]
    day = c1.date_input("Jour", pd.Timestamp.now().date())
    temp = c2.slider("Température (°C)", -10, 38, 18)
    rain = c3.slider("Pluie (mm/h)", 0.0, 15.0, 0.0, 0.5)

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
    fig.add_bar(x=fc.heure, y=fc.pred_departures, name="Départs", marker_color=INDIGO)
    fig.add_bar(x=fc.heure, y=fc.pred_arrivals, name="Arrivées", marker_color=SKY)
    fig.add_scatter(x=fc.heure, y=cum, name="Stock relatif cumulé", yaxis="y2", mode="lines+markers",
                    line=dict(color=INK, width=3, shape="spline"), marker=dict(size=6))
    fig.add_hline(y=0, line=dict(color=P["muted"], dash="dot"), yref="y2")
    fig.update_layout(barmode="group", bargap=.25, xaxis=dict(dtick=1, ticksuffix="h", range=[-.6, 23.6]),
                      yaxis=dict(title="vélos / heure"),
                      yaxis2=dict(overlaying="y", side="right", showgrid=False, title="stock relatif"))
    show(fig, 400)
    st.caption(f"Modèle utilisé : `{rows[0]['model_departures']}` · essaie de passer la pluie à 5 mm/h pour voir la demande "
               "chuter.")

# ================================================================== 7. model
with tabs[7]:
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
                        line=dict(color="#94A3B8", width=1.5), fillcolor="rgba(141,220,255,.25)")
        fig.add_scatter(x=net.hour_ts, y=net.baseline_departures, name="Baseline", line=dict(color=GOLD, width=1.5,
                                                                                               dash="dot"))
        fig.add_scatter(x=net.hour_ts, y=net.pred_departures, name="Modèle ML", line=dict(color=INDIGO, width=2.5))
        show(fig, 380)
    with c2:
        section("Modèle vs baseline", "Plus bas = meilleur.")
        comp = pd.DataFrame({"cible": ["Départs", "Départs", "Arrivées", "Arrivées"],
                             "type": ["Baseline", "Modèle", "Baseline", "Modèle"],
                             "MAE": [dep.baseline_mae, dep.model_mae, arr.baseline_mae, arr.model_mae]})
        fig = px.bar(comp, x="cible", y="MAE", color="type", barmode="group", text_auto=".3f",
                     color_discrete_map={"Baseline": BAR_SOFT, "Modèle": INDIGO}, labels={"cible": "", "type": ""})
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
            col.markdown(f"""<div class="card" style="--c:{EMERALD if ok else ROSE}">
              <div class="t">{name} · challenger v{int(r.challenger_version)} {badge}</div>
              <div class="m">MAE challenger <b>{r.challenger_mae:.4f}</b> · champion v{int(r.champion_version_before)}
              <b>{r.champion_mae_before:.4f}</b> · baseline {r.baseline_mae:.4f}<br/>
              {'Le challenger devient le nouveau champion.' if ok else 'Le champion actuel reste en production.'}
              </div></div>""", unsafe_allow_html=True)

    section("Fiabilité des alertes (backtest sur l'historique réel)",
            "Quand le système annonce « vide / pleine bientôt », avait-il raison ? Vérité terrain = flux GBFS "
            "enregistré toutes les 5 min ; stations déjà en rupture exclues ; seuil choisi sur la 1re moitié.")
    if not has("gold_alert_backtest"):
        st.info("Pas encore de backtest : `python scripts/gbfs_history.py` (≥ 3 h) puis "
                "`python scripts/backtest_alerts.py`.")
    else:
        bt = t("gold_alert_backtest")
        names = {"naive": "Stock bas (sans prévision)", "point": "Prévision ponctuelle", "probabilistic": "Probabiliste"}
        c1, c2 = st.columns(2)
        for col, event, title in ((c1, "empty", "Alerte « vide »"), (c2, "full", "Alerte « pleine »")):
            b = bt[bt.event == event].assign(methode=lambda d: d.method.map(names))
            long = b.melt(id_vars="methode", value_vars=["precision", "recall", "pr_auc"], var_name="mesure")
            long["mesure"] = long.mesure.map({"precision": "Précision", "recall": "Rappel", "pr_auc": "PR-AUC"})
            fig = px.bar(long, x="mesure", y="value", color="methode", barmode="group", text_auto=".0%",
                         color_discrete_map={names["naive"]: BAR_SOFT, names["point"]: SKY,
                                             names["probabilistic"]: INDIGO},
                         labels={"mesure": "", "value": "", "methode": ""})
            fig.update_yaxes(range=[0, 1], tickformat=".0%")
            with col:
                r = b.set_index("method").loc["probabilistic"]
                st.markdown(f"**{title} à {int(r.horizon_h)} h**")
                show(fig, 330)
                st.caption(f"{int(r.n):,} fenêtres station × {int(r.horizon_h)} h sur {int(r.origins_test)} instants de test · "
                           f"prévalence {r.prevalence:.1%} · seuil P ≥ {r.p_threshold:.2f}"
                           + (" · ⚠ historique court : seuil réglé et testé sur la même période" if r.same_period else ""))
