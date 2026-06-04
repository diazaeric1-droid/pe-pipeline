#!/usr/bin/env python3
"""End-to-end PE pipeline: detect → predict → authorize.

    Daily Production Digest ──WellAlert──▶ ESP Failure-Risk Agent ──WellDiagnosis──▶ AFE Copilot

Each app is an independent repo with its own virtualenv (and all three are packaged
as ``src``), so they can't be imported into one process. This orchestrator runs each
as a subprocess in its own venv and threads JSON artifacts between them — the same
microservice-style handoff a real deployment would use. It runs with **zero API
keys** (the AFE stage renders deterministically); pass ``--llm`` to use the Claude
drafter for the final AFE narrative when a key is available.

Usage:
    python3 pe_chain.py                 # full chain, deterministic AFE
    python3 pe_chain.py --llm --docx    # Claude-drafted AFE + .docx export
"""
from __future__ import annotations

import argparse
import json
import subprocess
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "pipeline_output"

# The three app repos are expected as siblings of this repo (i.e. under its parent).
# Override with the APPS_ROOT env var if they live elsewhere.
APPS_ROOT = Path(os.environ.get("APPS_ROOT", HERE.parent))
APPS = {
    "digest": APPS_ROOT / "daily-production-digest",
    "esp": APPS_ROOT / "esp-failure-risk-agent",
    "afe": APPS_ROOT / "afe-copilot",
}


def _venv_python(app_dir: Path) -> str:
    py = app_dir / ".venv" / "bin" / "python"
    if not py.exists():
        sys.exit(f"Missing venv for {app_dir.name}: {py}\n"
                 f"  Create it: cd {app_dir} && python3 -m venv .venv && "
                 f".venv/bin/pip install -e '.[ml,demo,dev]'  (extras vary per app)")
    return str(py)


def _run(app: str, args: list[str], cwd: Path) -> None:
    cmd = [_venv_python(cwd), "-m", "src.handoff", *args]
    print(f"  $ ({app}) {' '.join('src.handoff' if c.endswith('python') else c for c in cmd[1:])}")
    subprocess.run(cmd, cwd=cwd, check=True)


def main():
    ap = argparse.ArgumentParser(description="Run the detect → predict → authorize PE pipeline.")
    ap.add_argument("--llm", action="store_true", help="Use the Claude drafter for the AFE (needs API key)")
    ap.add_argument("--docx", action="store_true", help="Also export the AFE as .docx")
    ap.add_argument("--wi", type=float, default=1.0, help="Working interest for AFE economics")
    ap.add_argument("--nri", type=float, default=0.80, help="Net revenue interest for AFE economics")
    args = ap.parse_args()

    OUT.mkdir(exist_ok=True)
    alerts_path = OUT / "alerts.json"
    diag_path = OUT / "diagnosis.json"
    afe_path = OUT / "afe.md"

    # Ensure the digest fleet exists (deterministic regen if not).
    fleet_dir = APPS["digest"] / "data" / "synthetic" / "fleet"
    if not any(fleet_dir.glob("well_*.csv")):
        print("[setup] generating digest fleet…")
        subprocess.run([_venv_python(APPS["digest"]), "data/synthetic/generate_fleet.py"],
                       cwd=APPS["digest"], check=True)

    print("\n[1/3] Daily Production Digest — detecting ESP-related anomalies")
    _run("digest", ["--out", str(alerts_path)], APPS["digest"])
    alerts = json.loads(alerts_path.read_text())
    if not alerts:
        print("\nNo ESP-related anomalies today — nothing to authorize. ✅")
        return
    top = alerts[0]
    print(f"      → top alert: {top['well_id']} · {top['category']} ({top['severity']}) "
          f"· deferring ~{top['deferred_bopd']:.0f} BOPD")

    print("\n[2/3] ESP Failure-Risk Agent — scoring the flagged well")
    _run("esp", ["--alerts", str(alerts_path), "--out", str(diag_path)], APPS["esp"])
    diag = json.loads(diag_path.read_text())
    print(f"      → 30-day failure risk {diag['esp_risk_score']:.0%} · mode: {diag['suspected_mode']} "
          f"· → intervention: {diag['intervention']}")

    print("\n[3/3] AFE Copilot — drafting the authorization")
    afe_args = ["--input", str(diag_path), "--out", str(afe_path),
                "--wi", str(args.wi), "--nri", str(args.nri)]
    if args.llm:
        afe_args.append("--llm")
    if args.docx:
        afe_args += ["--docx", str(OUT / "afe.docx")]
    _run("afe", afe_args, APPS["afe"])

    print("\n" + "=" * 70)
    print(f"PIPELINE COMPLETE — {top['well_id']}")
    print(f"  detect : {top['category']} ({top['severity']}) — {top['headline']}")
    print(f"  predict: ESP 30-day risk {diag['esp_risk_score']:.0%} — {diag['suspected_mode']}")
    print(f"  authorize: {diag['intervention']} → {afe_path}")
    print("=" * 70)
    print(f"\nArtifacts in {OUT}/ : alerts.json · diagnosis.json · afe.md"
          + (" · afe.docx" if args.docx else ""))


if __name__ == "__main__":
    main()
