# Experiment code

The paper release covers original PP fixed boards, original 400 matched games, revision A (shared TALK with branched VOTEs), and revision B (joint werewolf/possessed adoption). Shared infrastructure is retained because the scoring, board generation and live runners import it.

Use [`release/reproduce.py`](../release/reproduce.py) for the distributed data, following the [root README](../README.md). The historical live-run analyzers validate original manifests and have different path/overwrite contracts; do not run them directly on transformed data archives.

Accepted outputs, frozen source/policy snapshots and attempt histories are distributed in `dataset/raw/`. Superseded runs, pilot outputs, internal delivery packages and duplicate backups have been removed from this checkout. Existing pilot configuration/helper code remains where imported by the main experiments. [Paper/evidence mapping](../docs/artifact-index.csv).

## Code layout

| Directory | Responsibility |
| --- | --- |
| `evaluation/` | Shared offline statistics, log parsing, summaries, and saved-judgment input/output validation |
| `runtime/` | Single/parallel game runners, rule opponents, opponent qualification, and artifact freezing |
| `preflight/` | Budget estimates and saved-connection checks; `verify_openai_access.py` performs a **live API check** |
| `pilot/` | Historical pilot preparation, checks, and configuration |
| `judge/` | Shared judgment rubric, prompt, schema, and calibration fixtures |
| `pp/` | PP condition generation, specifications, and pilot preparation |
| `pp/fixed_boards/` | Original board generator, fixed-board runners, scoring, and semantic judge |
| `pp/fixed_boards_v2/` | Revised board corpus and its generator/checks |
| `pp/v2/normal/` | Original matched-game schedule, execution audits, and analysis |
| `pp/v2/` | Cue and human-audit helpers, v2 conditions and evaluation specification |
| `pp/revision/` | Revision A/B preparation, execution, recovery, and analysis |

The shared Python modules formerly at `experiments/*.py` now live in the four
role-specific packages above. Use their new import paths and CLI paths; for example:

```bash
python3 -m experiments.evaluation.judge_outputs calibrate
python3 -m experiments.runtime.run_parallel --help
python3 -m experiments.pilot.prepare --help
```

Shared-module migration (filenames are unchanged):

| New package | Modules formerly at the root |
| --- | --- |
| `evaluation` | `analyze`, `export_game_talk`, `judge_inputs`, `judge_outputs`, `pp_log_parser`, `summarize`, `verify_saved_logs`, `verify_pp_saved_log` |
| `runtime` | `freeze_artifacts`, `opponents`, `qualify_opponents`, `run_one`, `run_parallel` |
| `preflight` | `estimate_budget`, `verify_luna_connection`, `verify_openai_access` |
| `pilot` | `check`, `prepare` |

The paper-specific `pp/` paths and the release reproduction command remain stable.
Saved protocol snapshots retain their original flat filenames (for example,
`protocol/pp_log_parser.py`); preparation copies from the new source locations.
Historical manifests, generated corpora, and archived results retain their recorded
hashes. The current code inventory is sealed separately in `release/code-manifest.json`.
See [test layout and commands](../tests/README.md) for offline development checks.

The current release uses the [post-outcome scoring correction](pp/revision/scoring-correction-protocol.md), implemented in `pp/fixed_boards/scoring_rules.py`. `pp/revision/reanalyze.py` audits original development archives; distributed, path-normalized data use `release/reproduce.py`.
