---
language:
  - en
license: mit
pretty_name: AIWolfDial 2026 Power Play Evaluation
size_categories:
  - 1K<n<10K
task_categories:
  - text-generation
tags:
  - synthetic
  - aiwolf
  - evaluation
configs:
  - config_name: boards
    data_files:
      - split: evaluation
        path: data/boards.jsonl
  - config_name: fixed_units
    data_files:
      - split: evaluation
        path: data/fixed_units.jsonl
  - config_name: normal_games
    data_files:
      - split: evaluation
        path: data/normal_games.jsonl
  - config_name: stage_units
    data_files:
      - split: evaluation
        path: data/stage_units.jsonl
  - config_name: factorial_games
    data_files:
      - split: evaluation
        path: data/factorial_games.jsonl
  - config_name: talk_judgments
    data_files:
      - split: evaluation
        path: data/talk_judgments.jsonl
---

# AIWolfDial 2026 Power Play Evaluation

This maintained card text records the acceptance status. The published data snapshot and its original code pin remain unchanged; code documentation release **1.0.0-rc.4** supports the same saved-data analyses.

**Public data release: 2026-09-14.** This dataset is available at [synonym/aiwolf-nlp-agent-llm](https://huggingface.co/datasets/synonym/aiwolf-nlp-agent-llm), with the snapshot tag `release-20260914`. The matching code distribution is **1.0.0-rc.3**, commit [`d427dc299bacf4eb4cb41c114c8af476b71ac7ed`](https://github.com/Mainlst/aiwolf-nlp-agent-llm/tree/d427dc299bacf4eb4cb41c114c8af476b71ac7ed). The code distribution uses a single root commit; this dataset is separate and is not included in that repository. Paper publication identifiers are still pending. The dataset is distributed under the MIT license in [LICENSE](https://huggingface.co/datasets/synonym/aiwolf-nlp-agent-llm/blob/release-20260914/LICENSE). Bundled historical code retains its upstream MIT notice in [CODE-LICENSE-MIT](https://huggingface.co/datasets/synonym/aiwolf-nlp-agent-llm/blob/release-20260914/CODE-LICENSE-MIT); see [attribution and scope](https://huggingface.co/datasets/synonym/aiwolf-nlp-agent-llm/blob/release-20260914/LICENSE-SCOPE.md).

[Publication provenance](https://huggingface.co/datasets/synonym/aiwolf-nlp-agent-llm/blob/release-20260914/provenance/publication-20260914.json) records the matching code and the previous local dataset checksum. The `unpublished` status in `provenance/release-manifest.json` describes the historical export, and the matching code's documentation predates this data publication. Both historical records are retained. This publication adds the Hub's `.gitattributes` and publication metadata to the dataset checksums; the six raw archives, evaluation records and reference results are unchanged.

Supporting data for the accepted AIWolfDial 2026 paper *Target-Agent Power Play Instructions in LLM Werewolf: Cue-Conditioned Execution and Matched-Game Outcomes*, by Shoma Yato, Ikumu Komatsu, Haruka Itakura, and Daisuke Katagami, Tokyo Polytechnic University.

The data include 120 synthetic decision boards (80 established PP, 40 controls), 480 original fixed-board units, 400 original matched games, 960 additional stage units sharing 480 TALKs, and 800 additional factorial games in 200 blocks. There are 2,880 selected raw semantic judgments: three per original fixed unit and three per shared A TALK. All 820 B attempts and their selection/continuation records are retained. Development data, old runs, and third-party tournament corpora are not evaluation samples in this release.

The generator was gpt-5.6-luna at temperature 1.0; the semantic classifier was gpt-5.6-sol with three judgments and majority voting. The classifier received condition-masked TALK and ground-truth references; it was not blind to the references needed for scoring. The original fixed-board classifier also received the submitted VOTE; the shared-TALK classifier received neither VOTE branch. Eleven of the 1,440 original classifications, across ten units, cited VOTE alongside utterance evidence. This count does not bound the influence of VOTE visibility. Ground-truth references were not provided to the generating agents. Saved prompts, source/configuration snapshots, game histories, outputs, votes, judgments, and attempt logs are under `raw/`.

Each config has an `evaluation` split, a packaging name with no claim of a held-out test set. See [data dictionary](https://huggingface.co/datasets/synonym/aiwolf-nlp-agent-llm/blob/release-20260914/DATA_DICTIONARY.md) for types, denominators, ID joins, missing fields, and experimental conditions. The original synthetic player names and blind-ID join tables are retained. No human participant dataset is claimed. Path normalization and exclusions are recorded in `provenance/`; source and exported hashes are distinguished.

The intended use is auditing and offline re-scoring/reanalysis of these four evaluations. The boards are development-derived; roles and available cues are confounded. Results cover a single generation model, five-player games and fixed village policies. Consistent votes may be incorrect. Semantic classifier accuracy against human labels has not been validated. A is a stage comparison, not an individual-clause ablation; B's overall win-rate interval includes zero.

Fixed-board composite success requires both repetitions. False PP on a control requires an extracted possessed/werewolf self-claim or a positive PP-recognition or vote-coordination label in either repetition. Controls have a null ally and no valid village-team targets, so coordination labels do not separately detect all fictitious-ally coordination; all control coordination judgments were negative. Ally recognition and coordination were judged independently: a reply matching an ally's request or an appeal by role could receive a positive coordination label without identifying a particular ally. These statements describe the saved labels, not human validation or a revised rubric.

The accompanying code release restores the historical fixed-board classifier source and the surviving case-selection records. The exact selection order for the final qualitative examples is not recoverable from those records; the cases illustrate behavior and do not estimate its frequency.

Obtain the matching code and the complete dataset (including the raw evidence), using the pinned code commit and dataset tag:

```bash
git clone https://github.com/Mainlst/aiwolf-nlp-agent-llm.git
cd aiwolf-nlp-agent-llm
git checkout d427dc299bacf4eb4cb41c114c8af476b71ac7ed
python3 -m pip install huggingface_hub
hf download synonym/aiwolf-nlp-agent-llm --repo-type dataset --revision release-20260914 --local-dir ./dataset
```

The download needs network access; this public dataset does not require an API token. The six Dataset Hub configurations are browsing views. Loading one config alone does not download the complete evidence required for reanalysis. Install `release/requirements-offline.txt` from the code release, then run:

```bash
python3 -B release/reproduce.py --data ./dataset --output ./reproduced
```

Install the minimal dependencies from the code release's `release/requirements-offline.txt`. No API key, network access or game server is needed. Preserve all files and verify `checksums.sha256`. Full-precision corrected reference results are in `results/`; the paper-to-evidence mapping is in `provenance/artifact-index.csv`. The release loader validates archive-member integrity, A's shared TALK/branch states and B's complete blocks/selected game hashes. It reconstructs the original fixed majority labels and compares six analyses, including cue and partner diagnostics.

Citation: use the paper title and author list above until its official bibliographic identifiers are assigned. The code repository's `CITATION.cff` records the code URL and version; no paper DOI has been assigned.

## Scoring correction (2026-09-10)

This release includes the post-outcome deterministic correction
`english-declarations-v2-20260910`. It corrects vote-declaration and affirmative
role-claim extraction, while retaining the samples, outputs, semantic-majority
judgments, statistical settings and primary comparisons. Component and post-reach
diagnostics change; independent human-label accuracy is not established.

The five original raw archives remain unchanged. `raw/correction.tar.gz` adds
the formally verified correction outputs, code snapshots, input hashes, all 2,792
extraction records (377 changed), old/new differences and review evidence.
`results/original/` retains pre-correction references and the B browsing view;
`data/factorial_games.jsonl` contains the same 800 games with corrected diagnostics.
`provenance/scoring-correction.json` distinguishes original source hashes from
exported hashes and records the previous dataset checksum-file hash. Use the
matching corrected code release; the earlier code cannot reproduce these diagnostics.
