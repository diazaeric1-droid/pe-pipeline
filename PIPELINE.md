# PE Pipeline — detect → predict → authorize

The three apps chain into one multi-agent workflow that mirrors how an asset team
actually moves from a SCADA anomaly to an approved capital authorization:

```
  Daily Production Digest        ESP Failure-Risk Agent          AFE Copilot
  ──────────────────────         ──────────────────────          ───────────
  scans fleet SCADA,        ─▶   scores the flagged well's   ─▶  drafts a priced,
  flags pump-failure             30-day failure probability       risk-registered,
  signatures, ranks by           + classifies the failure         authority-routed
  deferred $/day                 mode (deterministic)             AFE for the fix
        │  WellAlert                    │  WellDiagnosis                 │
        └──────────────── JSON ─────────┴──────────── JSON ──────────────┘
```

The **same physical well** flows through all three stages: the ESP loader tolerates
the digest's SCADA schema (it backfills the v0.5 drive-frequency / current-imbalance
channels with healthy defaults), so a digest fleet CSV is scored directly.

## Run it

```bash
python3 pe_chain.py            # full chain, deterministic AFE (no API key needed)
python3 pe_chain.py --llm      # use Claude to draft the final AFE narrative
python3 pe_chain.py --docx     # also export the AFE as .docx
```

Each app is an independent repo with its own virtualenv (and all three are packaged
as `src`), so `pe_chain.py` runs each as a subprocess in its own venv and threads
JSON artifacts between them — the microservice-style handoff a real deployment uses.
Artifacts land in `pipeline_output/`: `alerts.json`, `diagnosis.json`, `afe.md`.

## Example run

```
[1/3] Daily Production Digest — detecting ESP-related anomalies
      → top alert: well_013 · intake_collapse (HIGH)
[2/3] ESP Failure-Risk Agent — scoring the flagged well
      → 30-day failure risk 61% · mode: Gas interference — intake pressure collapse
        → intervention: gas_lift_optimization
[3/3] AFE Copilot — drafting the authorization
      → well_013 gas-lift AFE, $26,510, routed to Production Engineer, net NPV $740k
```

## The handoff contract

Two versioned JSON artifacts keep the apps decoupled (each ignores keys it doesn't
own, so any stage can evolve independently):

**`WellAlert`** (`pe-pipeline/well-alert/v1`, digest → ESP) — only genuine ESP
mechanical signatures are forwarded (`intake_collapse`, `amps_creep`,
`motor_temp_spike`, `runtime_degradation`); rate drops are reservoir-ambiguous and
route elsewhere. Carries `well_id`, `category`, `severity`, `deferred_bopd`,
`baseline_bopd`, and the absolute `scada_csv` path so the next stage scores the same well.

**`WellDiagnosis`** (`pe-pipeline/well-diagnosis/v1`, ESP → AFE) — a superset of the
AFE Copilot's `AFEDiagnosis` (extra keys like `esp_risk_score`, `suspected_mode` are
ignored by its `from_pe_copilot` loader). The deterministic failure-mode classifier
maps the mode → a priced intervention; the uplift is the upstream-quantified deferral
or a mode-dependent fraction of the well's recent rate.

Stage entry points (each runnable standalone): `python -m src.handoff` in each repo.

## Why this matters

It turns three separate demos into one defensible narrative: **deterministic
detection → an ML failure-risk score with a grounded diagnosis → an economically
justified, governance-routed authorization** — with the engineering math trusted at
every hop and the LLM confined to narration. The whole chain runs with zero API keys.
