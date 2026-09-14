# Conflict-resolved, server-reachable PP fixed-board rerun

This document records the historical rerun protocol, including preparation requirements from that period. Reproduce the paper's final saved outputs through the [release entry point](../../../README.md#reproduce-the-paper-offline); use [new-experiment setup](../../../docs/new-experiments.md) for a new study. References to removed development notes are historical, and semantic-label accuracy against human judgments remains unvalidated.

This directory defines the prospective rerun after the legacy tournament prompt
was found to contain an absolute concealment command that conflicted with its
three-survivor werewolf-CO rule. Legacy configs, corpora, runs, and results remain
unchanged and are diagnostic/exploratory only.

No paid call is made by generation, audit, tests, or preflight:

```bash
uv run python3 experiments/pp/generate_conditions.py \
  --spec experiments/pp/v2/spec.yml \
  --output-dir experiments/pp/v2/generated
uv run python3 experiments/pp/check_conditions.py \
  --spec experiments/pp/v2/spec.yml \
  --generated-dir experiments/pp/v2/generated \
  --audit-dir experiments/pp/v2/generated/audit
uv run python3 experiments/pp/fixed_boards_v2/generate.py
uv run python3 experiments/pp/fixed_boards_v2/check.py
PYTHONPATH=src uv run python3 -m unittest discover -q
uv run python3 experiments/pp/v2/preflight.py
```

After committing a clean worktree, run the excluded route check and then the
full generation under new run IDs:

```bash
uv run python3 experiments/pp/v2/preflight.py --require-clean
uv run python3 experiments/pp/fixed_boards/run_confirmation.py \
  --run-id pp-v2-fixed-confirm-luna-YYYYMMDD-v1 \
  --corpus-dir experiments/pp/fixed_boards_v2/generated \
  --conditions-dir experiments/pp/v2/generated
uv run python3 experiments/pp/fixed_boards/score_confirmation.py \
  experiments/runs/pp-v2-fixed-confirm-luna-YYYYMMDD-v1 \
  --corpus-dir experiments/pp/fixed_boards_v2/generated
uv run python3 experiments/pp/fixed_boards/run_analysis.py \
  --run-id pp-v2-fixed-analysis-luna-YYYYMMDD-v1 \
  --corpus-dir experiments/pp/fixed_boards_v2/generated \
  --conditions-dir experiments/pp/v2/generated \
  --evaluation-spec experiments/pp/v2/evaluation-spec.yml \
  --order-seed 20260817 \
  --max-parallel 24
```

Never reuse a legacy run ID. Do not include legacy v1's p-value in the v2 Holm
family. Human validation of the blinded semantic labels is required before the
v2 primary interpretation, as frozen in `experiments/pp/v2/evaluation-spec.yml`.
The complete judge, scoring, and human-audit commands are frozen in
`doc/note.md` under “PP競合解消v2の再実行手順”. Runners read `config/.env` directly and never
record the API key.

The reachable repair replaces the ten post-terminal possessed-perspective
controls with five-survivor day-one probes in which a village-side player makes
a false werewolf claim and invites premature possessed disclosure. The
werewolf-perspective three-survivor missing-ally controls remain reachable.
Generation fails closed if any label is not `replay_reachable=true`.
