---
title: PE Pipeline — detect, predict, authorize
emoji: 🛢️
colorFrom: blue
colorTo: gray
sdk: streamlit
sdk_version: 1.50.0
app_file: app.py
pinned: true
license: mit
---

# PE Pipeline — detect → predict → authorize

> The YAML block above is config for **Hugging Face Spaces** (genuinely public, no
> login wall); GitHub just renders it as a small table. See "Deploy" below.

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

The three apps are **vendored** as plain directories under `apps/` (mirrored from their
own repos — see the links below), so this is a single self-contained repo with **no git
submodules** (Streamlit Community Cloud doesn't reliably check those out). The unified
Streamlit app runs the whole chain **in one process** — no subprocesses, no per-app
virtualenvs — by loading each app's `src` package under a distinct alias (see
`pipeline_core.py`). It runs with **zero API keys** (the AFE stage renders deterministically).

## Two ways to run it

**Unified Streamlit app (single environment — what gets deployed):**
```bash
git clone https://github.com/diazaeric1-droid/pe-pipeline
cd pe-pipeline
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run app.py
```
First load regenerates the synthetic data and trains the ESP model (~30s, one time;
data/artifacts are `.gitignore`d). Then pick a flagged well and watch it flow
detect → predict → authorize, ending in a downloadable AFE.

**CLI orchestrator (separate repos, each with its own venv):**
```bash
python3 pe_chain.py            # full chain, deterministic AFE
python3 pe_chain.py --llm      # Claude-drafted AFE narrative
```
`pe_chain.py` runs each app as a subprocess in its own venv (the microservice handoff a
real deployment uses). It expects the three apps as sibling repos of this one; override
with `APPS_ROOT=/path/to/apps`.

## Deploy

**Hugging Face Spaces (recommended — genuinely public, no login wall):**
1. huggingface.co → **New Space** → SDK **Streamlit**, hardware **CPU basic** (free).
2. Push this repo to the Space's git remote (the Space reads the YAML config at the top
   of this README + `requirements.txt`; `app_file` is `app.py`):
   ```bash
   git remote add hf https://huggingface.co/spaces/<user>/pe-pipeline
   git push hf main
   ```
3. It builds and serves at `https://<user>-pe-pipeline.hf.space` with **no sign-in**.
   First load trains the ESP model (~30s).

**Streamlit Community Cloud:** works the same (single self-contained repo, no submodule
settings), but note that some accounts now force **viewer sign-in** even on "public"
apps — if your `share.streamlit.io` app redirects visitors to `/-/auth/app`, that's the
gate, and Hugging Face Spaces avoids it.

No secrets required — the chain is deterministic. (Add `ANTHROPIC_API_KEY` only if you
extend the AFE stage to use the Claude drafter.)

> **Keeping the vendored apps in sync:** they mirror the three repos linked below. When
> those change, refresh with `rsync`/copy from each repo's `src/` (or re-vendor). The
> import-alias machinery in `pipeline_core.py` is path-based, so nothing else changes.

## The handoff contract

Two versioned JSON artifacts keep the apps decoupled (each ignores keys it doesn't own):
- **`WellAlert`** (`pe-pipeline/well-alert/v1`, digest → ESP) — only genuine ESP
  mechanical signatures are forwarded; carries the absolute `scada_csv` path so the next
  stage scores the same well.
- **`WellDiagnosis`** (`pe-pipeline/well-diagnosis/v1`, ESP → AFE) — a superset of the
  AFE Copilot's `AFEDiagnosis`.

See [PIPELINE.md](PIPELINE.md) for the full contract and an example run.

## Apps (vendored under `apps/`, mirrored from these repos)

- [esp-failure-risk-agent](https://github.com/diazaeric1-droid/esp-failure-risk-agent)
- [afe-copilot](https://github.com/diazaeric1-droid/afe-copilot)
- [daily-production-digest](https://github.com/diazaeric1-droid/daily-production-digest)
