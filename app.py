"""Unified Streamlit app: the suite's FLEET TRIAGE BOARD.

Two modes on one page:

  🚦 Fleet triage (default) — rank the WHOLE fleet by risked-NPV opportunity, with
     KPI cards, a top-wells bar chart, and the detect → predict → authorize funnel as
     a fleet summary. The front door: where do I look first?
  🔬 Well detail — the existing per-well detect → predict → authorize flow (Daily
     Production Digest → ESP Failure-Risk Agent → AFE Copilot) for the selected well.

Runs all three apps' logic IN ONE PROCESS (see pipeline_core), deterministically, with
no API key — no subprocesses or per-app virtualenvs.
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

import theme
import fleet_registry as fr

theme.setup_page("PE Pipeline — fleet triage → well detail", icon="🛢️")
theme.suite_nav("pipeline")

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

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

theme.header("PE Pipeline",
             subtitle="fleet triage board → drill into any well's detect → predict → authorize",
             chips=[("orchestrator", "info"), ("fleet triage", "eval")])
st.caption("The suite's front door: rank the WHOLE fleet by risked-NPV opportunity, then drill "
           "into any well's three-agent flow — the Daily Production Digest flags a pump-failure "
           "signature → the ESP Failure-Risk Agent scores 30-day risk + diagnoses the mode → the "
           "AFE Copilot drafts the authorization. Deterministic at every hop; runs with no API key.")


@st.cache_resource(show_spinner=False)
def _bootstrap():
    msgs: list[str] = []
    with st.status("First-time setup — generating synthetic data + training the ESP model…",
                   expanded=True) as status:
        pc.bootstrap(log=lambda m: (msgs.append(m), status.write(m)))
        status.update(label="Setup complete.", state="complete", expanded=False)
    return True


_bootstrap()

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


board = _ranked(price, nri)
board = fr.enrich(board)  # shared Permian fleet identity (basin/area/formation/lift/hero) — additive
alerts = pc.get_alerts(price_per_bbl=price)
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


tab_triage, tab_detail = st.tabs(["🚦 Fleet triage", "🔬 Well detail"])

# ════════════════════════════════════════════════════════════════════════════
# MODE A — FLEET TRIAGE (default)
# ════════════════════════════════════════════════════════════════════════════
with tab_triage:
    if board.empty:
        st.success("No wells in the fleet — nothing to triage. ✅")
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
        "30-day risk": show["failure_risk_30d"].map(lambda x: f"{x:.0%}"),
        "Deferred $/day": show["deferred_usd_per_day"].map(lambda x: f"${x:,.0f}"),
        "Addressable BOPD": show["incremental_bopd"].map(lambda x: f"{x:,.0f}"),
        "Intervention": show["recommended_intervention"].str.replace("_", " "),
        "Risked NPV": show["est_risked_npv"].map(lambda x: f"${x:,.0f}"),
        "NPV basis": show["npv_basis"].str.replace("_", " "),
    })
    st.dataframe(disp, width="stretch", hide_index=True)
    st.caption("Ranked descending by risked NPV (the opportunity score). Field / lift come from the "
               "shared **fleet registry** — every suite app references this same onshore Permian fleet "
               "(Midland + Delaware basins) by well ID; ★ marks a hero well with an end-to-end story. "
               "'NPV basis' shows whether the figure came from the chain's AFE intervention economics or "
               "the transparent deferred-$/day × 365 × risk proxy.")

    # ── Fleet funnel — detect → predict → authorize summary (Sankey) ─────────
    st.subheader("Pipeline funnel — this run")
    _n_fleet = fleet_n
    _n_alerts = len(alerts)
    _n_other = max(_n_fleet - _n_alerts, 0)
    _top_well = board["well_id"].iloc[0]
    _top_risk = float(board["failure_risk_30d"].iloc[0])
    _top_interv = str(board["recommended_intervention"].iloc[0]).replace("_", " ")
    _top_alert = _alert_for(_top_well)
    _top_usd = _deferred_usd_per_day(_top_alert)

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
               "predict → authorize chain. Open '🔬 Well detail' for any well's full flow.")

    # ── Drill-in selector → hands off to the Well detail tab ─────────────────
    st.divider()
    well_ids = board["well_id"].tolist()
    default_idx = well_ids.index(st.session_state.get("drill_well", well_ids[0])) \
        if st.session_state.get("drill_well") in well_ids else 0
    st.selectbox(
        "Drill into well →", well_ids, index=default_idx, key="drill_well",
        help="Pick a well, then open the '🔬 Well detail' tab for its detect → predict → authorize flow.")
    st.caption("Selection drives the '🔬 Well detail' tab.")

# ════════════════════════════════════════════════════════════════════════════
# MODE B — WELL DETAIL (existing detect → predict → authorize flow)
# ════════════════════════════════════════════════════════════════════════════
with tab_detail:
    sel_well = st.session_state.get("drill_well") or (board["well_id"].iloc[0] if not board.empty else None)
    if sel_well is None:
        st.info("No well selected. Use the '🚦 Fleet triage' tab to pick one.")
        st.stop()

    alert = _alert_for(sel_well)
    st.caption(f"Showing the detect → predict → authorize flow for **{sel_well}** "
               "(change the selection on the 🚦 Fleet triage tab).")

    # ── Stage 1 — Detect ─────────────────────────────────────────────────────
    st.subheader(f"1 · Detect — Daily Production Digest · {sel_well}")
    if sel_well in alerts_by_well:
        st.markdown(f"**{alert['category']}** · severity **{alert['severity']}** — {alert['headline']}")
        st.metric("Deferred $/day (net)", f"${_deferred_usd_per_day(alert):,.0f}")
    else:
        st.info("This well was **not** flagged by today's digest — it surfaced via fleet risk "
                "scoring. The ESP agent still scores it below.")

    # ── Stage 2 — Predict ────────────────────────────────────────────────────
    st.divider()
    st.subheader(f"2 · Predict — ESP Failure-Risk Agent · {sel_well}")
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
                       file_name=f"AFE_{sel_well}_{diag['intervention']}.md")
    with st.container(border=True):
        st.markdown(afe_md)

st.divider()
st.caption("Engineering math is deterministic at every hop; the LLM is optional and confined to "
           "narration. Source: github.com/diazaeric1-droid/pe-pipeline")
