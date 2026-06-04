"""Unified Streamlit app: one screen showing a well flow detect → predict → authorize.

Runs all three apps' logic IN ONE PROCESS (see pipeline_core), so there are no
subprocesses or per-app virtualenvs — the whole chain is interactive on a single page.
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="PE Pipeline — detect → predict → authorize",
                   page_icon="🛢️", layout="wide")

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# Guarded import: the three apps are git submodules; if they weren't checked out
# (e.g. a Streamlit Cloud deploy without submodule init) fail with a clear message.
try:
    import pipeline_core as pc
except Exception as e:  # noqa: BLE001
    st.title("PE Pipeline")
    st.error("Couldn't load the app submodules.\n\n"
             f"```\n{type(e).__name__}: {e}\n```\n\n"
             "This repo pulls the three PE apps in as git submodules under `apps/`. "
             "Initialise them with:\n\n```\ngit submodule update --init --recursive\n```\n\n"
             "On Streamlit Cloud, enable submodules in the app's advanced settings.")
    st.stop()

import plotly.graph_objects as go  # noqa: E402

st.title("PE Pipeline  ·  detect → predict → authorize")
st.caption("One well, three agents, one screen. The Daily Production Digest flags a pump-failure "
           "signature → the ESP Failure-Risk Agent scores its 30-day risk + diagnoses the mode → "
           "the AFE Copilot drafts the authorization. Deterministic at every hop; runs with no API key.")

c1, c2, c3 = st.columns(3)
c1.info("**1 · Detect**\nDaily Production Digest")
c2.warning("**2 · Predict**\nESP Failure-Risk Agent")
c3.success("**3 · Authorize**\nAFE Copilot")


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

# ── Stage 1 — Detect ────────────────────────────────────────────────────────
st.divider()
st.subheader("1 · Detect — Daily Production Digest")
alerts = pc.get_alerts(price_per_bbl=price)
if not alerts:
    st.success("No ESP-related anomalies in the fleet today — nothing to authorize. ✅")
    st.stop()

import pandas as pd  # noqa: E402
adf = pd.DataFrame([{
    "Well": a["well_id"], "Category": a["category"], "Severity": a["severity"],
    "Deferred $/day": f"${a['deferred_usd_per_day']:,.0f}" if a.get("deferred_usd_per_day") else "—",
    "Headline": a["headline"],
} for a in alerts])
st.dataframe(adf, use_container_width=True, hide_index=True)
st.caption("Only genuine ESP/pump-failure signatures are forwarded (rate drops are reservoir-"
           "ambiguous and route elsewhere). Ranked by severity, then deferred $/day.")

labels = [f"{a['well_id']} — {a['category']} ({a['severity']})" for a in alerts]
idx = st.selectbox("Push a well through the pipeline:", range(len(alerts)),
                   format_func=lambda i: labels[i])
alert = alerts[idx]

# ── Stage 2 — Predict ───────────────────────────────────────────────────────
st.divider()
st.subheader(f"2 · Predict — ESP Failure-Risk Agent · {alert['well_id']}")
diag = pc.diagnose(alert)
m1, m2, m3 = st.columns(3)
m1.metric("30-day failure risk", f"{diag['esp_risk_score']:.0%}")
m2.metric("Suspected mode", diag["suspected_mode"].split("—")[0].strip())
m3.metric("→ Intervention", diag["intervention"].replace("_", " "))
st.caption(diag["primary_diagnosis"])

scada = pc.well_scada(alert)
fig = go.Figure()
for col, color in [("bopd", "#1f77b4"), ("intake_pressure_psi", "#ff7f0e"),
                   ("motor_temp_f", "#d62728"), ("motor_amps", "#2ca02c")]:
    if col in scada.columns:
        fig.add_trace(go.Scatter(x=scada["date"], y=scada[col], name=col,
                                 line=dict(color=color)))
fig.update_layout(height=320, margin=dict(l=0, r=0, t=20, b=0),
                  legend=dict(orientation="h"))
st.plotly_chart(fig, use_container_width=True)

# ── Stage 3 — Authorize ─────────────────────────────────────────────────────
st.divider()
st.subheader("3 · Authorize — AFE Copilot")
afe_md = pc.render_afe(diag, working_interest=wi, net_revenue_interest=nri, realized_price=price)
st.download_button("⬇ Download AFE (markdown)", afe_md,
                   file_name=f"AFE_{alert['well_id']}_{diag['intervention']}.md")
with st.container(border=True):
    st.markdown(afe_md)

st.divider()
st.caption("Engineering math is deterministic at every hop; the LLM is optional and confined to "
           "narration. Source: github.com/diazaeric1-droid/pe-pipeline")
