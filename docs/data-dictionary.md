# Data dictionary

All `dataset/data/*.jsonl` files contain one UTF-8 JSON object per line. They are small typed browsing views; `raw/*.tar.gz` carries the complete evidence. Stable scalar columns are used where possible. Heterogeneous records are encoded as JSON **strings** in `*_json` columns, avoiding mixed-type nested columns. Boolean fields are JSON booleans, missing values are JSON null, and counts/repetitions are integers. Text and identifiers are strings.

| File | Rows | Columns and joins |
| --- | ---: | --- |
| `boards.jsonl` | 120 | `scenario_id`; `input_json` (generation-visible decision snapshot); `label_json` (ground-truth scoring reference). Also provided as native records under `boards/inputs.jsonl` and `boards/labels.jsonl`. |
| `fixed_units.jsonl` | 480 | `unit_id`, `scenario_id`, `condition` (P+, P-PP), `repetition` (1/2), `game_id`, `talk`, `vote`, `cluster`; `group_id` is null. |
| `stage_units.jsonl` | 960 | Same fields; condition A11/A10/A01/A00. `group_id` joins the shared TALK and its two VOTE branches. |
| `normal_games.jsonl` | 400 | `row_id`, `pair_id` (200 matched pairs), `condition`, `game_id`, `won` (wolf-team victory), `details_json` (original per-game CSV values, represented as strings). |
| `factorial_games.jsonl` | 800 | `block_id` (200 blocks), `condition` (G00/G10/G01/G11), `game_id`, `won`, `diagnostics` (object: `reached` is always present; post-reach boolean fields are absent when not reached). |
| `talk_judgments.jsonl` | 2,880 | `experiment` (fixed/stage), `talk_id` (fixed blind ID or A group ID), `repetition` (1/2/3), `success`, `judgment_json` (labels, evidence quotes, rationale and usage metadata). |

Fixed-board IDs use role perspectives POS/WER. `cluster` removes that perspective marker; paired perspectives of the same content remain in one resampling cluster. A11/A10 share a T1 TALK, and A01/A00 share a T0 TALK: 960 units do not represent 960 independently generated TALKs. The first A digit controls TALK policy, the second VOTE policy. B's first G digit controls the werewolf policy and the second the possessed policy. A digit of 1 means P+, and 0 means P-PP.

The fixed archive's `judge-inputs-v1/private-map.jsonl` is an experimental join table, not personal information. `blind_id` joins judgments to `unit_id`, `game_id`, and `input_sha256`. Original synthetic names are preserved consistently across prompts, votes, labels and trajectories.

`provenance/attempts.csv` lists all 820 B attempts: `row_id` (block-condition), `attempt`, `status`, `result_json`. Complete selected games number 800. Retryable/failed attempts must not be counted as extra games. In raw B evidence, `lineage/` and repair records preserve retained prior trials and continuation history. A and original-normal attempt logs are retained in their respective raw archives.

`provenance/release-manifest.json`: schema version, release status, selected run IDs, view row counts, archive-member paths/bytes/source hashes/exported hashes, and excluded paths/reasons. `transformations.jsonl` records changed archive members. `results/*.json` are the six current reference analyses, including the 2026-09-10 scoring correction. Archive members use run-relative paths; historical frozen manifests describe the originals and are retained for audit.

`raw/correction.tar.gz` contains the formally verified correction outputs, source snapshots, input hashes, old/new differences, and review evidence. Its `extractions.jsonl` has 2,792 records; `changed-extractions.jsonl` has the 377 records whose extraction changed. Each record retains `source`, `extraction_id`, `input` (text and participant names), `old`, `corrected` (target and role CO), and `target_evidence`. A changed extraction is not necessarily a changed primary score or independently validated semantic error.

`provenance/scoring-correction.json` records the extraction version, upstream commit, original correction-manifest hash, previous dataset checksum-file hash, and unchanged raw-archive hashes. Pre-correction references and the old B browsing view are retained in `results/original/`. The current `factorial_games.jsonl` uses corrected diagnostics with exactly the same 800 selected games and outcomes. The original 400-game heuristic and partner audit are unchanged.

Fixed-board 80 established-PP and 40 control scenarios are evaluated under each condition. Positives require both repetitions to succeed; false PP on controls is counted if either repetition triggers. A consistent vote can still target the wrong player. Descriptive post-reach statistics condition on survival/reach and do not estimate unconditional intervention effects. The single `evaluation` split is a packaging label, not a held-out or independent train/test partition.

False PP is the OR of an extracted possessed/werewolf self-claim, a positive PP-recognition label, and a positive coordination label. On controls, the ally reference is null and the valid-target list is empty; all control coordination judgments were negative. This endpoint does not independently measure every incorrect ally identification. Coordination and ally recognition are separate labels and need not agree.

The original fixed-board classifier input includes the submitted VOTE, and its historical evidence validator permits `target-vote` citations. The shared-TALK classifier sees neither VOTE branch. The code release includes the restored original classifier source and its recorded hash under `experiments/pp/fixed_boards/historical/`; `docs/case-provenance.md` explains the evidence for each qualitative case and the limits of the recovered selection records.
