# PE Pipeline — detect → predict → authorize

A multi-agent orchestrator that chains three independent upstream-production apps into
one workflow: a SCADA anomaly becomes an ML failure-risk score becomes a priced,
governance-routed capital authorization — the **same physical well** flowing through
all three, with the engineering math trusted at every hop and the LLM confined to
narration.

```
  Daily Production Digest  ──WellAlert──▶  ESP Failure-Risk Agent  ──WellDiagnosis──▶  AFE Copilot
   (detect: flag pump-           (predict: 30-day failure risk          (authorize: priced,
    failure signatures,           + deterministic failure-mode           risk-registered,
    rank by deferred $)           classification)                        authority-routed AFE)
```

## Apps (sibling repos)

- [esp-failure-risk-agent](https://github.com/diazaeric1-droid/esp-failure-risk-agent)
- [afe-copilot](https://github.com/diazaeric1-droid/afe-copilot)
- [daily-production-digest](https://github.com/diazaeric1-droid/daily-production-digest)

Clone all three as **siblings of this repo** (or set `APPS_ROOT` to where they live),
and install each app's venv (`.venv`) per its own README.

## Run

```bash
python3 pe_chain.py            # full chain, deterministic AFE (no API key needed)
python3 pe_chain.py --llm      # use Claude to draft the final AFE narrative
python3 pe_chain.py --docx     # also export the AFE as .docx
APPS_ROOT=/path/to/apps python3 pe_chain.py   # apps live elsewhere
```

Each app is packaged as `src` and has its own virtualenv, so they can't share a
process — `pe_chain.py` runs each as a subprocess in its own venv and threads JSON
artifacts between them (the microservice-style handoff a real deployment uses).
Artifacts land in `pipeline_output/`: `alerts.json`, `diagnosis.json`, `afe.md`.

See [PIPELINE.md](PIPELINE.md) for the full handoff contract and an example run.
