# Blind judge specification

This directory freezes the model configuration, prompt, output schema, rubric,
calibration cases, and sampling rule used by the AIWolfDial 2026 ablation.

`gpt-5.6-sol` is the primary judge at temperature 0. The model catalog did not
expose a dated snapshot when this specification was frozen, so every response
must retain the returned model identifier and execution timestamp. A model
identifier change stops the batch. `gpt-5.6-terra` is used only for the fixed
adjudication rule; if it is unavailable, disputed units remain missing.

Before paid evaluation, run:

```bash
uv run python3 experiments/evaluation/judge_outputs.py calibrate
uv run python3 experiments/evaluation/judge_outputs.py validate <outputs.jsonl> \
  --inputs <blind-inputs.jsonl>
```

The calibration command validates fixture structure and expected labels without
calling an API. Actual model calibration must be recorded separately before
Stage 2.

