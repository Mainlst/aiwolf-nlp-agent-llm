# Offline regression tests

Tests follow the responsibility of the code they exercise. All subdirectories are
Python packages so standard `unittest` discovery includes the full suite.

| Directory | Coverage |
| --- | --- |
| `agents/` | `src/` output guards, experiment switches, and stoppable threads |
| `evaluation/` | Shared statistics and judgment evaluation pipeline |
| `runtime/` | Opponent behavior, parallel runtime configuration, and summaries |
| `pp/` | PP conditions, evaluation specifications, and pilot preparation |
| `pp/fixed_boards/` | Original/v2 board invariants, semantic judging, and analysis runner |
| `pp/normal/` | Matched assignments, statistical analysis, and block audits |
| `pp/revision/` | A/B design, resumption, checkpoints, and generation/judging failures |
| `distribution/` | Release integrity, safe extraction paths, and exported-log resolution |

Run from the checkout root with the full development dependencies (`uv sync --locked`).
The offline reproduction requirements alone do not include the agent libraries used
by these tests. Model clients and game execution in runner tests are mocked.

```bash
mkdir -p .verification/tmp
TMPDIR="$PWD/.verification/tmp" PYTHONPATH=src .venv/bin/python3 -B -m unittest discover -s tests -t . -v

# A specific experiment family
TMPDIR="$PWD/.verification/tmp" PYTHONPATH=src .venv/bin/python3 -B -m unittest discover -s tests/pp/revision -t . -v

# A single module
TMPDIR="$PWD/.verification/tmp" PYTHONPATH=src .venv/bin/python3 -B -m unittest tests.pp.fixed_boards.test_judge -v
```

The filenames within PP families omit the redundant `pp_` prefix; the broad revision
suite is `tests.pp.revision.test_design`. The revision shell helper's `test` command
also selects the new revision directory. Use the directory commands above instead
of the former `test_pp_revision*.py` discovery pattern.

Tests check synthetic fixtures and protocol invariants. To verify the paper's full
numerical results after a code change, follow [the reproduction contract](../docs/reproduction.md):
deliberately seal the changed code, then run `release/reproduce.py` into a fresh
directory using the separate `dataset/` release.
