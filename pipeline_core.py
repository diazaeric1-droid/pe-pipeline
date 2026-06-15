"""In-process pipeline core for the unified Streamlit app.

The three apps are each packaged as a top-level ``src`` package, so they can't all
be imported normally (the name collides). This module loads each app's ``src`` under
a distinct alias (``digest``, ``esp``, ``afe``) via importlib, so the whole
detect → predict → authorize chain runs in ONE Python process — no subprocesses,
no per-app virtualenvs — which is what makes a single Streamlit page possible.

The CLI orchestrator (``pe_chain.py``) is the alternative for when the three apps are
checked out as independent repos each with its own venv; this module is for the
single-environment (Streamlit Cloud) deployment, where the apps are **vendored** as
plain directories under ``apps/`` (mirrored from their own repos) so the deploy is a
single self-contained clone — no submodules required.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Apps are git submodules under apps/ by default; override for local layouts.
APPS_ROOT = Path(os.environ.get("PE_APPS_ROOT", HERE / "apps"))
APP_DIRS = {
    "digest": APPS_ROOT / "daily-production-digest",
    "esp": APPS_ROOT / "esp-failure-risk-agent",
    "afe": APPS_ROOT / "afe-copilot",
}


def _load_pkg(app_dir: Path, alias: str):
    """Load ``app_dir/src`` as a top-level package named ``alias`` so its internal
    relative imports (``from .features import ...``) resolve under that alias."""
    if alias in sys.modules:
        return sys.modules[alias]
    src = app_dir / "src"
    if not (src / "__init__.py").exists():
        raise FileNotFoundError(
            f"{alias}: missing {src}/__init__.py — the apps are vendored under apps/; "
            f"run from the repo root (or set PE_APPS_ROOT).")
    spec = importlib.util.spec_from_file_location(
        alias, src / "__init__.py", submodule_search_locations=[str(src)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod


# Register the three packages under aliases, then import the handoff entry points.
_load_pkg(APP_DIRS["digest"], "digest")
_load_pkg(APP_DIRS["esp"], "esp")
_load_pkg(APP_DIRS["afe"], "afe")

digest_handoff = importlib.import_module("digest.handoff")
digest_loader = importlib.import_module("digest.data_loader")
esp_handoff = importlib.import_module("esp.handoff")
esp_loader = importlib.import_module("esp.data_loader")
esp_features = importlib.import_module("esp.features")
esp_model = importlib.import_module("esp.model")
esp_explainer = importlib.import_module("esp.explainer")
afe_handoff = importlib.import_module("afe.handoff")
afe_cost_db = importlib.import_module("afe.cost_db")
afe_economics = importlib.import_module("afe.economics")
# Suite-wide economics kernel, vendored inside the AFE app and reused here so the
# orchestrator risks cash flows with the exact same convention as the apps it chains.
econ_core = importlib.import_module("afe.econ_core")

DIGEST_FLEET = APP_DIRS["digest"] / "data" / "synthetic" / "fleet"
DIGEST_ACK = APP_DIRS["digest"] / "acknowledged.yml"
ESP_DATA = APP_DIRS["esp"] / "data" / "synthetic"
ESP_MODEL = APP_DIRS["esp"] / "artifacts" / "esp_risk_model.joblib"


# ---- bootstrap (data + model are .gitignore'd, so regenerate on first run) ----

def ensure_digest_data(log=print) -> None:
    if not any(DIGEST_FLEET.glob("well_*.csv")):
        log("Generating synthetic fleet (digest)…")
        runpy.run_path(str(APP_DIRS["digest"] / "data" / "synthetic" / "generate_fleet.py"),
                       run_name="__main__")


def ensure_esp_model(log=print) -> Path:
    if ESP_MODEL.exists():
        return ESP_MODEL
    if not any(ESP_DATA.glob("well_*.csv")):
        log("Generating synthetic SCADA (ESP)…")
        runpy.run_path(str(ESP_DATA / "generate.py"), run_name="__main__")
    log("Training the ESP failure-risk model (~30s, one time)…")
    fleet = esp_loader.load_fleet(ESP_DATA)
    X = esp_features.featurize_fleet(fleet)
    labels = esp_loader.load_labels(ESP_DATA / "labels.csv").set_index("well_id")["failed_within_30d"]
    aligned = X.join(labels, how="inner")
    m = esp_model.ESPRiskModel()
    m.fit(aligned[X.columns], aligned["failed_within_30d"])
    m.save(ESP_MODEL)
    return ESP_MODEL


def bootstrap(log=print) -> None:
    ensure_digest_data(log)
    ensure_esp_model(log)


# ---- the chain ---------------------------------------------------------------

def get_alerts(price_per_bbl: float = 70.0) -> list[dict]:
    """Stage 1: digest → ESP-related WellAlerts (ranked money-first)."""
    return digest_handoff.export_alerts(
        DIGEST_FLEET, price_per_bbl=price_per_bbl, ack_path=str(DIGEST_ACK))


def fleet_size() -> int:
    """Number of wells the digest scanned this run (for the funnel visual)."""
    try:
        return len(digest_loader.load_fleet(DIGEST_FLEET))
    except Exception:  # noqa: BLE001
        return 0


def diagnose(alert: dict, model_path: Path | None = None) -> dict:
    """Stage 2: ESP scores the alert's well → AFE-ready WellDiagnosis."""
    return esp_handoff.diagnose(
        alert["scada_csv"], well_id=alert.get("well_id"),
        deferred_bopd=alert.get("deferred_bopd", 0.0),
        baseline_bopd=alert.get("baseline_bopd", 0.0),
        model_path=str(model_path or ESP_MODEL))


def render_afe(diag: dict, working_interest: float = 1.0,
               net_revenue_interest: float = 0.80, realized_price: float = 70.0) -> str:
    """Stage 3: WellDiagnosis → deterministic AFE markdown."""
    return afe_handoff.render_afe_markdown(
        diag, working_interest=working_interest,
        net_revenue_interest=net_revenue_interest, realized_price=realized_price)


def well_scada(alert_or_csv) -> "object":
    """Load the well's SCADA (with the digest's bopd column) for plotting."""
    csv = alert_or_csv["scada_csv"] if isinstance(alert_or_csv, dict) else alert_or_csv
    return digest_loader.load_well(csv)


# ---- fleet triage board (deterministic, no LLM) -----------------------------

# Column schema for the ranked board. Kept as a module constant so an empty fleet
# can return a correctly-typed (and correctly-ordered) frame.
RANK_COLUMNS: dict[str, str] = {
    "well_id": "string",
    "deferred_bopd": "float64",
    "deferred_usd_per_day": "float64",
    "failure_risk_30d": "float64",
    "recommended_intervention": "string",
    "incremental_bopd": "float64",       # addressable rate the intervention protects/restores
    "est_risked_npv": "float64",         # risk-weighted net NPV (chain economics or proxy)
    "npv_basis": "string",               # "chain_economics" or "proxy" — which est_risked_npv used
    "opportunity_score": "float64",      # the sort key (== est_risked_npv)
}


def _empty_rank_frame() -> "object":
    import pandas as pd
    return pd.DataFrame({c: pd.Series(dtype=t) for c, t in RANK_COLUMNS.items()})


def _score_fleet_risk(fleet: dict, model_path: Path) -> dict[str, float]:
    """Score every well in ``fleet`` with the ESP model → {well_id: risk}.

    The digest fleet CSVs load cleanly through the ESP featurizer (the ESP loader
    backfills the v0.5 drive-frequency / current-imbalance channels with healthy
    defaults), so the WHOLE fleet is scored in one batch — no alerted-subset
    compromise is needed. If the model artifact or scientific stack is unavailable
    (e.g. sklearn/xgboost missing), we degrade to an empty map and the caller
    assigns a baseline risk to every well; this is documented and deterministic.
    """
    try:
        X = esp_features.featurize_fleet(fleet)
        model = esp_model.ESPRiskModel.load(str(model_path))
        probs = model.predict_proba(X)
        return {str(wid): float(p) for wid, p in zip(X.index, probs)}
    except Exception:  # noqa: BLE001  (missing model/deps → baseline fallback)
        return {}


# When the ESP model can't score a well, assign this baseline 30-day risk so the
# board is still fully populated and deterministic. Conservative (low) by design.
BASELINE_RISK_30D = 0.05

# ---- no-action tier thresholds ----------------------------------------------
# Wells below BOTH thresholds (or with zero deferment AND risk below the second)
# are classified as "no_action" and carry opportunity_score = 0 so they don't
# clutter the triage board. Tune here — no other file needs to change.
_NO_ACTION_NPV_THRESHOLD = 10_000    # $ — below this, not worth flagging
_NO_ACTION_RISK_THRESHOLD = 0.15     # 30-day failure probability


def _lift_of(well_id: str) -> str | None:
    """The well's artificial-lift type from the shared fleet registry (None if the
    registry is unavailable — callers then fall back to the lift-agnostic mapping)."""
    try:
        import fleet_registry
        return fleet_registry.get(str(well_id)).lift
    except Exception:  # noqa: BLE001
        return None


def rank_fleet(price_per_bbl: float = 70.0, net_revenue_interest: float = 0.80,
               model_path: Path | None = None) -> "object":
    """Rank the WHOLE bootstrapped fleet by risked-NPV opportunity (deterministic).

    This is the suite's "front door": instead of pushing one well through the
    detect → predict → authorize chain, it scores every well in the fleet and ranks
    them by the dollars an intervention could protect, so the user sees where to
    look first.

    Pure and deterministic given ``(fleet, price_per_bbl, net_revenue_interest)`` —
    no LLM, no API key. ``bootstrap()`` must have run so the synthetic fleet and the
    trained ESP model exist.

    For each well it computes:

    - ``failure_risk_30d`` — the ESP model's calibrated 30-day failure probability.
      The full fleet is scored (the digest CSVs featurize cleanly); if the model is
      unavailable every well falls back to ``BASELINE_RISK_30D`` (documented).
    - ``deferred_bopd`` — barrels/day already being lost, from the digest alert for
      that well (0 for wells the digest hasn't flagged / pre-failure signatures).
    - ``deferred_usd_per_day`` = ``deferred_bopd × price × nri`` (the same net-revenue
      conversion the per-well view uses).
    - ``recommended_intervention`` — from the deterministic failure-mode classifier,
      mapped to a priced AFE intervention (reuses the ESP→AFE handoff mapping).
    - ``incremental_bopd`` — the rate the intervention protects/restores: the deferred
      rate if the digest quantified one, else a mode-dependent fraction of recent rate.
    - ``est_risked_npv`` — risk-weighted net-to-operator NPV. When the chain's AFE
      economics are reachable it is ``net_NPV(intervention) × risk`` (``npv_basis ==
      "chain_economics"``); otherwise a transparent proxy
      ``deferred_usd_per_day × 365 × risk`` (``npv_basis == "proxy"``).
    - ``opportunity_score`` — the sort key, equal to ``est_risked_npv``.

    Returns a pandas DataFrame sorted descending by ``opportunity_score`` (ties broken
    by ``failure_risk_30d``). An empty/missing fleet returns an empty, correctly-typed
    frame with the same columns.
    """
    import pandas as pd

    model_path = Path(model_path or ESP_MODEL)

    fleet = digest_loader.load_fleet(DIGEST_FLEET)
    if not fleet:
        return _empty_rank_frame()

    # Deferred barrels already-lost per well, from the digest's ESP alerts (same
    # money-first scan the per-well view uses). Wells not flagged → 0 deferred.
    deferred_by_well: dict[str, float] = {}
    for a in get_alerts(price_per_bbl=price_per_bbl):
        deferred_by_well[a["well_id"]] = float(a.get("deferred_bopd", 0.0))

    risk_by_well = _score_fleet_risk(fleet, model_path)

    rows: list[dict] = []
    for well_id, scada in fleet.items():
        well_id = str(well_id)
        baseline_bopd = (float(scada["bopd"].tail(7).mean())
                         if "bopd" in scada.columns and len(scada) else 0.0)
        deferred_bopd = deferred_by_well.get(well_id, 0.0)
        deferred_usd_per_day = deferred_bopd * price_per_bbl * net_revenue_interest

        risk = risk_by_well.get(well_id, BASELINE_RISK_30D)

        # Deterministic failure-mode → priced AFE intervention + recovery fraction.
        # featurize_well gives the same features the classifier expects.
        try:
            feats = esp_features.featurize_well(scada)
            mode, _evidence = esp_explainer.classify_failure_mode(feats)
        except Exception:  # noqa: BLE001
            mode = ""
        # Gate the priced intervention to one valid for the well's artificial-lift
        # type (no ESP swap on a rod-pumped well, no gas-lift optimization on a well
        # with no injection) — the recommendation a PE reads must be physical.
        intervention, frac = esp_handoff._map_mode(mode, _lift_of(well_id))

        # The rate the intervention protects/restores (mirrors esp.handoff.diagnose):
        # the quantified deferral if present, else a mode-fraction of recent rate.
        incremental_bopd = round(max(deferred_bopd, frac * baseline_bopd, 20.0), 1)

        # est_risked_npv: prefer the chain's real intervention economics (net NPV to
        # the operator), risk-weighted; fall back to a transparent flow proxy.
        est_risked_npv = deferred_usd_per_day * 365.0 * risk
        npv_basis = "proxy"
        try:
            total_cost = afe_cost_db.cost_rollup(intervention)["total"]
            econ = afe_economics.compute_economics(
                total_cost, incremental_bopd,
                realized_price_per_bbl=price_per_bbl,
                net_revenue_interest=net_revenue_interest, working_interest=1.0)
            # risked NPV = risk · PV(net revenue) − cost. net_npv_10pct_usd already nets
            # the cost, so PV(net revenue) = net_npv_10pct_usd + total_cost. Cost is
            # certain; only the upside is risk-weighted (econ_core.risked_npv).
            est_risked_npv = econ_core.risked_npv(
                econ.net_npv_10pct_usd + total_cost, total_cost, risk)
            npv_basis = "chain_economics"
        except Exception:  # noqa: BLE001
            pass

        # No-action tier: suppress wells that don't meet minimum thresholds so the
        # triage board reflects what actually needs attention.
        # Condition 1: low NPV AND low risk (not worth flagging at all).
        # Condition 2: zero deferment AND risk is negligible (no alarm, no loss).
        no_action = (
            (est_risked_npv < _NO_ACTION_NPV_THRESHOLD and risk < _NO_ACTION_RISK_THRESHOLD)
            or (deferred_bopd == 0.0 and risk < 0.10)
        )
        if no_action:
            intervention = "no_action"
            est_risked_npv = 0.0

        rows.append({
            "well_id": well_id,
            "deferred_bopd": round(deferred_bopd, 1),
            "deferred_usd_per_day": round(deferred_usd_per_day, 2),
            "failure_risk_30d": round(risk, 4),
            "recommended_intervention": intervention,
            "incremental_bopd": incremental_bopd,
            "est_risked_npv": round(est_risked_npv, 2),
            "npv_basis": npv_basis,
            "opportunity_score": round(est_risked_npv, 2),
        })

    df = pd.DataFrame(rows)
    df = df.sort_values(["opportunity_score", "failure_risk_30d"],
                        ascending=[False, False], kind="mergesort").reset_index(drop=True)
    # Coerce to the declared schema dtypes so the frame matches _empty_rank_frame().
    for col, dtype in RANK_COLUMNS.items():
        df[col] = df[col].astype(dtype)
    return df[list(RANK_COLUMNS)]


def run_chain(price_per_bbl: float = 70.0, working_interest: float = 1.0,
              net_revenue_interest: float = 0.80, log=print) -> dict:
    """Full chain end to end; returns every stage's artifact."""
    bootstrap(log)
    alerts = get_alerts(price_per_bbl)
    if not alerts:
        return {"alerts": [], "top": None, "diagnosis": None, "afe_md": None}
    top = alerts[0]
    diag = diagnose(top)
    afe_md = render_afe(diag, working_interest, net_revenue_interest, price_per_bbl)
    return {"alerts": alerts, "top": top, "diagnosis": diag, "afe_md": afe_md}
