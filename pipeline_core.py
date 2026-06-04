"""In-process pipeline core for the unified Streamlit app.

The three apps are each packaged as a top-level ``src`` package, so they can't all
be imported normally (the name collides). This module loads each app's ``src`` under
a distinct alias (``digest``, ``esp``, ``afe``) via importlib, so the whole
detect → predict → authorize chain runs in ONE Python process — no subprocesses,
no per-app virtualenvs — which is what makes a single Streamlit page possible.

The CLI orchestrator (``pe_chain.py``) is the alternative for when the three apps are
checked out as independent repos each with its own venv; this module is for the
single-environment (Streamlit Cloud) deployment, where the apps are git submodules
under ``apps/``.
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
            f"{alias}: missing {src}/__init__.py — did the submodules get checked out? "
            f"Run: git submodule update --init --recursive")
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
afe_handoff = importlib.import_module("afe.handoff")

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
