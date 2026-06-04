# PE Pipeline — detect → predict → authorize

One screen, three agents, one well. A SCADA anomaly becomes an ML failure-risk score
becomes a priced, governance-routed capital authorization — the **same physical well**
flowing through all three apps, with the engineering math trusted at every hop and the
LLM confined to narration.

```
  Daily Production Digest  ──WellAlert──▶  ESP Failure-Risk Agent  ──WellDiagnosis──▶  AFE Copilot
   (detect: flag pump-           (predict: 30-day failure risk          (authorize: priced,
    failure signatures,           + deterministic failure-mode           risk-registered,
    rank by deferred $)           classification)                        authority-routed AFE)
```

The three apps live in their own repos and are pulled in here as **git submodules**
under `apps/`. The unified Streamlit app runs the whole chain **in one process** — no
subprocesses, no per-app virtualenvs — by loading each app's `src` package under a
distinct alias (see `pipeline_core.py`). It runs with **zero API keys** (the AFE stage
renders deterministically).

## Two ways to run it

**Unified Streamlit app (single environment — what gets deployed):**
```bash
git clone --recurse-submodules https://github.com/diazaeric1-droid/pe-pipeline
cd pe-pipeline
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run app.py
```
First load regenerates the synthetic data and trains the ESP model (~30s, one time;
data/artifacts are `.gitignore`d in the app repos). Then pick a flagged well and watch
it flow detect → predict → authorize, ending in a downloadable AFE.

**CLI orchestrator (separate repos, each with its own venv):**
```bash
python3 pe_chain.py            # full chain, deterministic AFE
python3 pe_chain.py --llm      # Claude-drafted AFE narrative
```
`pe_chain.py` runs each app as a subprocess in its own venv (the microservice handoff a
real deployment uses). It expects the three apps as sibling repos of this one; override
with `APPS_ROOT=/path/to/apps`.

## Deploy to Streamlit Community Cloud

1. Point a new app at this repo, `app.py`, branch `main`.
2. **Enable submodules** so `apps/` gets checked out (Advanced settings → "Include
   submodules", or the app will show a clear "init submodules" message and stop).
3. `requirements.txt` is the union of the three apps' deps + the UI.

No secrets required — the chain is deterministic. (Add `ANTHROPIC_API_KEY` only if you
extend the AFE stage to use the Claude drafter.)

## The handoff contract

Two versioned JSON artifacts keep the apps decoupled (each ignores keys it doesn't own):
- **`WellAlert`** (`pe-pipeline/well-alert/v1`, digest → ESP) — only genuine ESP
  mechanical signatures are forwarded; carries the absolute `scada_csv` path so the next
  stage scores the same well.
- **`WellDiagnosis`** (`pe-pipeline/well-diagnosis/v1`, ESP → AFE) — a superset of the
  AFE Copilot's `AFEDiagnosis`.

See [PIPELINE.md](PIPELINE.md) for the full contract and an example run.

## Apps (submodules)

- [esp-failure-risk-agent](https://github.com/diazaeric1-droid/esp-failure-risk-agent)
- [afe-copilot](https://github.com/diazaeric1-droid/afe-copilot)
- [daily-production-digest](https://github.com/diazaeric1-droid/daily-production-digest)
