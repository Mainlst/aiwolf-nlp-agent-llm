# PP fixed-board corpus

This is the historical v1 development protocol; its generation and live-run commands do not reproduce the paper's final evaluation corpus. For the distributed final boards and saved results, use the [offline reproduction entry point](../../../README.md#reproduce-the-paper-offline). New model calls require the separate [new-experiment setup](../../../docs/new-experiments.md).

This directory freezes the decision snapshots used for the P+/P−PP fixed-board comparison.

- `spec.yml` fixes provenance, counts, strata, leakage rules, and the backup destination.
- `generate.py` creates 120 analysis inputs and labels from a deterministic five-player synthetic grammar. It does not read saved or tournament logs.
- `check.py` verifies counts, role composition, public snapshot eligibility, event chronology and state transitions, neutral near-control wording, template-family diversity, future-label separation, input hashes, duplicate absence, and confirmation-set separation.
- `run_confirmation.py` sends the 12 excluded confirmation inputs through the production TALK then VOTE path under both conditions.
- `score_confirmation.py` joins outputs and usage by `game_id`, exercises deterministic CO/target/vote scoring, and leaves semantic labels to the separately frozen judge.
- `generated/inputs.jsonl` contains only model-visible snapshots and own roles.
- `generated/labels.jsonl` separately stores hidden roles, allies, valid targets, replay reachability, and immutable input hashes.
- `generated/confirmation-*.jsonl` contains 12 disjoint scenarios marked as excluded from confirmatory analysis.

Generate and audit without API access:

```bash
uv run python3 experiments/pp/fixed_boards/generate.py
uv run python3 experiments/pp/fixed_boards/check.py
```

After committing the corpus and labels, exercise the non-analysis live route:

```bash
uv run python3 experiments/pp/fixed_boards/run_confirmation.py --run-id pp-fixed-confirm-YYYYMMDD
uv run python3 experiments/pp/fixed_boards/score_confirmation.py experiments/runs/pp-fixed-confirm-YYYYMMDD
```

Run the frozen analysis corpus only from a clean committed worktree. The runner
persists and validates each TALK-then-VOTE unit before submitting more work, and
can resume without repeating completed units:

```bash
uv run python3 experiments/pp/fixed_boards/run_analysis.py \
  --run-id pp-fixed-analysis-YYYYMMDD-v1 --max-parallel 24
uv run python3 experiments/pp/fixed_boards/run_analysis.py \
  --run-id pp-fixed-analysis-YYYYMMDD-v1 --max-parallel 24 --resume
```

If the environment makes `.git` read-only, `--allow-dirty` records the dirty
status, tracked patch, runner snapshot, and their hashes in the run provenance.

The analysis input never contains hidden roles for other players, ground-truth allies, valid village targets, winners, or events after the snapshot cutoff. “Established PP” is frozen from the hidden role composition while model eligibility is restricted to the public three-survivor status plus the perspective agent's own role and public history. This separation avoids leaking the answer to the model, especially from the possessed perspective where the remaining werewolf is not mechanically known.

Version 3 distributes established scenarios over ten event families per perspective role and each near-control role/stratum over five families. All snapshots are reachable while the server is still accepting actions. Possessed-perspective controls use five-survivor day-one states with either a soft coordination cue or an explicit false werewolf claim that attempts to induce premature disclosure. Werewolf-perspective controls use either five-survivor premature cues or reachable three-survivor states in which the possessed has already been executed. Labels separately record objective PP possibility and whether PP initiation is justified by the evaluation protocol; neither label is included in model input.

Prepare and validate the PP-specific blind semantic judge only after the full
generation run passes its operational audit. The judge returns exactly
`pp_recognized`, `ally_recognized`, and `vote_coordination`; it cannot emit
naturalness, diversity, persona, or overall-quality scores.

```bash
uv run python3 experiments/pp/fixed_boards/pp_judge.py prepare \
  experiments/runs/pp-fixed-analysis-YYYYMMDD-v2 \
  experiments/runs/pp-fixed-analysis-YYYYMMDD-v2/judge-inputs-v1
uv run python3 experiments/pp/fixed_boards/pp_judge.py calibrate
uv run python3 experiments/pp/fixed_boards/pp_judge.py calibrate \
  --live-run experiments/runs/pp-fixed-judge-calibration-sol-YYYYMMDD-v1 \
  --max-parallel 7
uv run python3 experiments/pp/fixed_boards/pp_judge.py calibrate \
  --check-run experiments/runs/pp-fixed-judge-calibration-sol-YYYYMMDD-v1
uv run python3 experiments/pp/fixed_boards/pp_judge.py run \
  experiments/runs/pp-fixed-analysis-YYYYMMDD-v2/judge-inputs-v1/blind-inputs.jsonl \
  experiments/runs/pp-fixed-judge-sol-YYYYMMDD-v1 --max-parallel 24
uv run python3 experiments/pp/fixed_boards/pp_judge.py aggregate \
  experiments/runs/pp-fixed-judge-sol-YYYYMMDD-v1 \
  experiments/runs/pp-fixed-analysis-YYYYMMDD-v2/judge-inputs-v1/semantic-scores.jsonl
uv run python3 experiments/pp/fixed_boards/score_analysis.py \
  experiments/runs/pp-fixed-analysis-YYYYMMDD-v2 \
  experiments/runs/pp-fixed-analysis-YYYYMMDD-v2/judge-inputs-v1 \
  experiments/runs/pp-fixed-analysis-YYYYMMDD-v2/judge-inputs-v1/semantic-scores.jsonl \
  experiments/runs/pp-fixed-analysis-YYYYMMDD-v2/analysis-v1
```
