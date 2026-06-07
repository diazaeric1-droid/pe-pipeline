"""Unified Streamlit app: the suite's FLEET TRIAGE BOARD (multipage).

Modeled on the Daily Production Digest's ``st.navigation`` pattern — a Fleet
Triage **overview page** (default) plus one **drill-down page per well**:

  🚦 Fleet Triage (overview, default) — rank the WHOLE fleet by risked-NPV
     opportunity, with KPI cards, a top-wells bar chart, the ranked board table
     (basin·formation / lift / lateral from the shared fleet registry), and the
     detect → predict → authorize funnel as a fleet summary. The front door:
     where do I look first?
  🔬 Per-well pages (one per board well, "Wells" section in the sidebar) — the
     existing per-well detect → predict → authorize flow (Daily Production Digest
     → ESP Failure-Risk Agent → AFE Copilot) for that well, with a registry
     metadata header and a link back to the overview.

Runs all three apps' logic IN ONE PROCESS (see pipeline_core), deterministically,
with no API key — no subprocesses or per-app virtualenvs.
"""
from __future__ import annotations

import sys
from functools import partial
from pathlib import Path

import streamlit as st

import theme
import fleet_registry as fr

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# ── Shared page setup (runs every rerun, before navigation) ──────────────────
theme.setup_page("PE Pipeline — fleet triage → well detail", icon="🛢️")
theme.suite_nav("pipeline")

# Guarded import: the three apps are vendored under apps/; if they weren't checked
# out (e.g. a deploy without them) fail with a clear message.
try:
    import pipeline_core as pc
except Exception as e:  # noqa: BLE001
    st.title("PE Pipeline")
    st.error("Couldn't load the bundled apps.\n\n"
             f"```\n{type(e).__name__}: {e}\n```\n\n"
             "The three PE apps are vendored under `apps/` in this repo. Run the app from "
             "the repo root, or set `PE_APPS_ROOT` to where the apps live.")
    st.stop()

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402


# ── First-run bootstrap: synthetic data + trained ESP model (cached) ─────────
@st.cache_resource(show_spinner=False)
def _bootstrap():
    msgs: list[str] = []
    with st.status("First-time setup — generating synthetic data + training the ESP model…",
                   expanded=True) as status:
        pc.bootstrap(log=lambda m: (msgs.append(m), status.write(m)))
        status.update(label="Setup complete.", state="complete", expanded=False)
    return True


_bootstrap()


# ── Sidebar economics (drives the ranking + AFE economics) ───────────────────
with st.sidebar:
    st.header("Economics")
    price = st.number_input("Realized oil price ($/bbl)", 20.0, 150.0, 70.0, 1.0)
    wi = st.slider("Working interest (WI)", 0.0, 1.0, 1.0, 0.05)
    nri = st.slider("Net revenue interest (NRI)", 0.0, 1.0, 0.80, 0.01)
    st.caption("WI = operator's share of cost · NRI = share of revenue after royalty.")


# ── Shared data: the ranked board + the digest alerts ────────────────────────
@st.cache_data(show_spinner="Ranking the fleet by risked-NPV opportunity…")
def _ranked(price: float, nri: float) -> pd.DataFrame:
    return pc.rank_fleet(price_per_bbl=price, net_revenue_interest=nri)


board_error = None
try:
    board = _ranked(price, nri)
    board = fr.enrich(board)  # shared Permian fleet identity (basin/area/formation/lift/lateral/hero) — additive
except Exception as _board_exc:  # noqa: BLE001 — never let the triage front page hard-crash
    board_error = _board_exc
    board = fr.enrich(pc._empty_rank_frame())
try:
    alerts = pc.get_alerts(price_per_bbl=price)
except Exception:  # noqa: BLE001
    alerts = []
alerts_by_well = {a["well_id"]: a for a in alerts}


def _deferred_usd_per_day(a: dict) -> float:
    """Digest WellAlert carries deferred_bopd (not $). Convert to net revenue at
    risk per day from the sidebar price + NRI: bopd × $/bbl × NRI."""
    return float(a.get("deferred_bopd", 0.0)) * price * nri


def _alert_for(well_id: str) -> dict:
    """A WellAlert-shaped dict for any well on the board — real alert if the digest
    flagged it, else a synthesized one pointing at the well's SCADA CSV so the
    per-well detect→predict→authorize flow works for non-alerted wells too."""
    if well_id in alerts_by_well:
        return alerts_by_well[well_id]
    row = board.loc[board["well_id"] == well_id]
    deferred = float(row["deferred_bopd"].iloc[0]) if len(row) else 0.0
    return {
        "well_id": well_id,
        "category": "fleet_scan",
        "severity": "—",
        "headline": "Not flagged by today's digest — surfaced by fleet risk scoring.",
        "deferred_bopd": deferred,
        "baseline_bopd": 0.0,
        "scada_csv": str((pc.DIGEST_FLEET / f"{well_id}.csv").resolve()),
        "date": pd.Timestamp.today().date().isoformat(),
    }


def _well_ids() -> list[str]:
    """Wells to expose as drill-down pages: the board order if available, else the
    alert wells as a graceful fallback when the board is empty/errored."""
    if not board.empty:
        return board["well_id"].tolist()
    return list(alerts_by_well)


def _back_to_overview() -> None:
    """Link back to the registered overview page object (set during nav wiring);
    fall back to the entrypoint path otherwise."""
    target = globals().get("overview")
    try:
        st.page_link(target if target is not None else "app.py",
                     label="← Back to Fleet triage", icon="🚦")
    except Exception:  # noqa: BLE001
        pass


# ════════════════════════════════════════════════════════════════════════════
# PAGE: FLEET TRIAGE (overview, default)
# ════════════════════════════════════════════════════════════════════════════

def render_overview() -> None:
    theme.header("PE Pipeline",
                 subtitle="fleet triage board → drill into any well's detect → predict → authorize",
                 chips=[("orchestrator", "info"), ("fleet triage", "eval")])
    st.caption("The suite's front door: rank the WHOLE fleet by risked-NPV opportunity, then drill "
               "into any well's three-agent flow — the Daily Production Digest flags a pump-failure "
               "signature → the ESP Failure-Risk Agent scores 30-day risk + diagnoses the mode → the "
               "AFE Copilot drafts the authorization. Deterministic at every hop; runs with no API key.")

    if board_error is not None:
        st.error(
            "Fleet triage couldn't be computed in this deployment. The most common cause is the "
            "bleeding-edge **Python 3.14** runtime + pandas 3.x stack — set this app's Python version "
            "to **3.12** in Streamlit (⋮ → Settings → Python version) and reboot. Full error below:")
        st.exception(board_error)
        st.stop()
    if board.empty:
        st.info("No wells in the fleet — nothing to triage.")
        st.stop()

    fleet_n = len(board)
    at_risk = int((board["failure_risk_30d"] >= 0.5).sum())
    total_deferred_usd = float(board["deferred_usd_per_day"].sum())
    total_risked_npv = float(board["est_risked_npv"].clip(lower=0).sum())

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Fleet size", f"{fleet_n} wells")
    k2.metric("At-risk (≥50% 30-day)", f"{at_risk}")
    k3.metric("Total deferred $/day", f"${total_deferred_usd:,.0f}")
    k4.metric("Addressable risked NPV", f"${total_risked_npv:,.0f}")
    st.caption("At-risk = ESP 30-day failure risk ≥ 50%. Addressable risked NPV = Σ of each "
               "well's positive risk-weighted net-to-operator NPV from its recommended intervention.")

    # ── Top wells by opportunity — horizontal bar ────────────────────────────
    st.subheader("Top opportunities — risked NPV by well")
    top = board.head(12).iloc[::-1]  # reverse so the biggest sits at the top of the bar chart
    colors = [theme.RED if r >= 0.5 else theme.AMBER for r in top["failure_risk_30d"]]
    bar = go.Figure(go.Bar(
        x=top["est_risked_npv"], y=top["well_id"], orientation="h",
        marker_color=colors,
        customdata=top[["failure_risk_30d", "recommended_intervention"]],
        hovertemplate="<b>%{y}</b><br>Risked NPV: $%{x:,.0f}"
                      "<br>30-day risk: %{customdata[0]:.0%}"
                      "<br>Intervention: %{customdata[1]}<extra></extra>",
        text=[f"${v:,.0f}" for v in top["est_risked_npv"]], textposition="auto",
    ))
    bar.update_layout(xaxis_title="Risked NPV ($)", yaxis_title="")
    st.plotly_chart(theme.style_fig(bar, height=380, legend=False), width="stretch")
    st.caption("Red = ESP 30-day failure risk ≥ 50%, amber below. Ranked by risk-weighted "
               "net-to-operator NPV of the recommended intervention.")

    _hero0 = fr.get(str(board["well_id"].iloc[0]))
    if _hero0.hero:
        st.info(f"**★ {_hero0.well_id} — {_hero0.name}** · {_hero0.basin} Basin · "
                f"{_hero0.formation} · {_hero0.lift} lift. {_hero0.storyline}")

    # ── Ranked board table — formatted $ and % ───────────────────────────────
    st.subheader("Fleet triage board")
    show = board.copy()
    disp = pd.DataFrame({
        "Well": [f"★ {w}" if h else w for w, h in zip(show["well_id"], show["hero"])],
        "Field": show["basin"] + " · " + show["formation"],
        "Lift": show["lift"],
        "Lateral (ft)": show["lateral_length_ft"].map(lambda x: f"{int(x):,}"),
        "30-day risk": show["failure_risk_30d"].map(lambda x: f"{x:.0%}"),
        "Deferred $/day": show["deferred_usd_per_day"].map(lambda x: f"${x:,.0f}"),
        "Addressable BOPD": show["incremental_bopd"].map(lambda x: f"{x:,.0f}"),
        "Intervention": show["recommended_intervention"].str.replace("_", " "),
        "Risked NPV": show["est_risked_npv"].map(lambda x: f"${x:,.0f}"),
        "NPV basis": show["npv_basis"].str.replace("_", " "),
    })
    st.dataframe(disp, width="stretch", hide_index=True)
    st.caption("Ranked descending by risked NPV (the opportunity score). Field / lift / lateral come "
               "from the shared **fleet registry** — every suite app references this same onshore Permian "
               "fleet (Midland + Delaware basins) by well ID; ★ marks a hero well with an end-to-end "
               "story. 'NPV basis' shows whether the figure came from the chain's AFE intervention "
               "economics or the transparent deferred-$/day × 365 × risk proxy. Open any well from the "
               "**Wells** section in the sidebar for its detect → predict → authorize flow.")

    # ── Fleet funnel — detect → predict → authorize summary (Sankey) ─────────
    st.subheader("Pipeline funnel — this run")
    _n_fleet = fleet_n
    _n_alerts = len(alerts)
    _n_other = max(_n_fleet - _n_alerts, 0)
    _top_well = board["well_id"].iloc[0]
    _top_risk = float(board["failure_risk_30d"].iloc[0])
    _top_interv = str(board["recommended_intervention"].iloc[0]).replace("_", " ")

    _nodes = [
        f"Fleet scanned · {_n_fleet} wells",                              # 0
        f"ESP alerts · {_n_alerts}",                                      # 1
        "Routed elsewhere",                                              # 2
        f"Top opportunity · {_top_well}  ({_top_risk:.0%} risk)",         # 3
        f"AFE-ready · {_top_interv}",                                     # 4
    ]
    sankey = go.Figure(go.Sankey(
        arrangement="snap",
        node=dict(
            label=_nodes, pad=18, thickness=18,
            color=[theme.NAVY, theme.BLUE, theme.GREY, theme.AMBER, theme.GREEN],
            line=dict(color=theme.BORDER, width=0.5),
        ),
        link=dict(
            source=[0, 0, 1, 3],
            target=[1, 2, 3, 4],
            value=[max(_n_alerts, 1), max(_n_other, 1), 1, 1],
            color="rgba(79,129,189,0.3)",
        ),
    ))
    st.plotly_chart(theme.style_fig(sankey, height=240, legend=False), width="stretch")
    st.caption("Funnel for this run: the fleet narrows to ESP-related digest alerts; the highest-"
               "opportunity well is scored + diagnosed and an AFE is authorized — the detect → "
               "predict → authorize chain. Open any well's page (sidebar **Wells**) for its full flow.")

    # ── Quick jump → switch directly to a well's drill-down page ─────────────
    st.divider()
    well_ids = board["well_id"].tolist()
    c1, c2 = st.columns([3, 1])
    with c1:
        jump = st.selectbox(
            "Jump to a well's drill-down →", well_ids,
            help="Pick a well and open its detect → predict → authorize page.")
    with c2:
        st.write("")  # vertical spacer to align the button with the selectbox
        if st.button("Open well page", type="primary", width="stretch"):
            st.switch_page(_well_pages.get(jump, "app.py"))
    st.caption("Each board well is also its own page under **Wells** in the sidebar.")


# ════════════════════════════════════════════════════════════════════════════
# PAGE: PER-WELL DRILL-DOWN (detect → predict → authorize)
# ════════════════════════════════════════════════════════════════════════════

def render_well(well_id: str) -> None:
    meta = fr.get(well_id)
    flagged = well_id in alerts_by_well

    theme.header(
        f"{well_id} · {meta.name}",
        subtitle=f"{meta.lift} · {meta.basin} · {meta.formation} · {meta.area}",
        chips=[("well detail", "info"),
               ("flagged" if flagged else "fleet-scan", "warn" if flagged else "info")],
    )
    _back_to_overview()

    # Registry metadata header — lift / lateral / basin·formation at a glance.
    mh = st.columns(4)
    mh[0].metric("Lift", meta.lift)
    mh[1].metric("Lateral (ft)", f"{meta.lateral_length_ft:,}")
    mh[2].metric("Basin · formation", f"{meta.basin} · {meta.formation}")
    mh[3].metric("Peer group", meta.peer_group)
    theme.well_cross_links("pipeline", well_id)

    alert = _alert_for(well_id)
    st.caption(f"Detect → predict → authorize flow for **{well_id}**. "
               "Economics use the sidebar price / WI / NRI.")

    # ── Stage 1 — Detect ─────────────────────────────────────────────────────
    st.subheader(f"1 · Detect — Daily Production Digest · {well_id}")
    if flagged:
        st.markdown(f"**{alert['category']}** · severity **{alert['severity']}** — {alert['headline']}")
        st.metric("Deferred $/day (net)", f"${_deferred_usd_per_day(alert):,.0f}")
    else:
        st.info("This well was **not** flagged by today's digest — it surfaced via fleet risk "
                "scoring. The ESP agent still scores it below.")

    # ── Stage 2 — Predict ────────────────────────────────────────────────────
    st.divider()
    st.subheader(f"2 · Predict — ESP Failure-Risk Agent · {well_id}")
    diag = pc.diagnose(alert)
    m1, m2, m3 = st.columns(3)
    m1.metric("30-day failure risk", f"{diag['esp_risk_score']:.0%}")
    m2.metric("Suspected mode", diag["suspected_mode"].split("—")[0].strip())
    m3.metric("→ Intervention", diag["intervention"].replace("_", " "))
    st.caption(diag["primary_diagnosis"])

    scada = pc.well_scada(alert)
    fig = go.Figure()
    for col in ["bopd", "intake_pressure_psi", "motor_temp_f", "motor_amps"]:
        if col in scada.columns:
            fig.add_trace(go.Scatter(x=scada["date"], y=scada[col], name=col))
    st.plotly_chart(theme.style_fig(fig, height=340), width="stretch")

    # ── Stage 3 — Authorize ──────────────────────────────────────────────────
    st.divider()
    st.subheader("3 · Authorize — AFE Copilot")
    afe_md = pc.render_afe(diag, working_interest=wi, net_revenue_interest=nri, realized_price=price)
    st.download_button("⬇ Download AFE (markdown)", afe_md,
                       file_name=f"AFE_{well_id}_{diag['intervention']}.md")
    with st.container(border=True):
        st.markdown(afe_md)

    st.divider()
    _back_to_overview()
    st.caption("Engineering math is deterministic at every hop; the LLM is optional and confined to "
               "narration. Source: github.com/diazaeric1-droid/pe-pipeline")


# ════════════════════════════════════════════════════════════════════════════
# Navigation wiring
# ════════════════════════════════════════════════════════════════════════════

overview = st.Page(render_overview, title="Fleet Triage", icon="🚦", default=True)
wells = [
    st.Page(partial(render_well, wid), title=wid, url_path=wid, icon="🔬")
    for wid in _well_ids()
]
# Map well_id → its Page object so the overview's quick-jump can switch_page to it.
_well_pages = {wid: page for wid, page in zip(_well_ids(), wells)}

nav_pages: dict = {"Fleet": [overview]}
if wells:
    nav_pages["Wells"] = wells
st.navigation(nav_pages).run()
