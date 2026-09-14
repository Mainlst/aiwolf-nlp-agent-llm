# Target-Agent Power Play Instructions in LLM Werewolf

Camera-ready research materials for **Target-Agent Power Play Instructions in LLM Werewolf: Cue-Conditioned Execution and Matched-Game Outcomes**, by Shoma Yato, Ikumu Komatsu, Haruka Itakura, and Daisuke Katagami (AIWolfDial 2026).

This is the **code distribution snapshot 1.0.0-rc.3** for [Mainlst/aiwolf-nlp-agent-llm](https://github.com/Mainlst/aiwolf-nlp-agent-llm). The initial code distribution uses a single root commit on `main`, without the earlier development history. The dataset is a separate release whose publication and download location are still pending; paper publication identifiers are also pending. Code and data use MIT licenses with their respective attribution notices. See [release status](docs/release-status.md). [日本語](README.ja.md).

This version includes the **2026-09-10 post-outcome scoring correction** for vote declarations and affirmative role claims. Primary comparisons are unchanged; component and post-reach diagnostics are corrected. Use the matching corrected dataset. Original evidence and pre-correction analyses are retained; no new model outputs or semantic judgments were generated. [Correction details](experiments/pp/revision/scoring-correction-20260910.md).

The 2026-09-14 restoration adds the historical fixed-board classifier source, surviving [case-selection records](docs/case-provenance.md), and historical server source/build instructions. The saved samples, judgments, results and raw evidence archives are unchanged. See [new experiments](docs/new-experiments.md) for the server and [release status](docs/release-status.md) for verification and publication steps.

## Reproduce the paper offline

Use Python 3.11 or newer. The offline analysis needs only PyYAML in addition to the standard library. It does not read credentials, call a model, start a server, or require the original checkout.

```bash
python3 -m venv .venv
.venv/bin/python3 -m pip install -r release/requirements-offline.txt
.venv/bin/python3 -B release/reproduce.py --data ./dataset --output ./reproduced
```

`dataset/` is prepared locally and excluded from Git because the data are intended for a separate Dataset Hub repository. A code-only checkout must obtain the matching data release first; its public download location is pending. Keep the dataset directory intact, including `raw/`, `provenance/`, and `checksums.sha256`.

The command verifies data and code checksums, extracts evidence into the new output directory, reconstructs majority judgments, re-scores saved outputs, and compares all statistical and diagnostic fields with the corrected reference results (float tolerance `1e-12`). It also verifies all 2,792 extraction records, including the 377 changed records. It refuses an existing output directory. Allow approximately 4 GB of disk space for the dataset and extracted evidence; a run takes a few minutes. Successful completion produces `verification.json`, `tables.md`, extraction ledgers, and six analysis directories.

| Evaluation | Design | Main result |
| --- | --- | --- |
| Original fixed boards | 120 boards × 2 policies × 2 repetitions; 480 units | Composite success 65/80 vs 2/80; difference +78.75 pp [70, 87.5] |
| Original normal games | 200 matched pairs; 400 games | Wins 56/200 vs 54/200; +1.0 pp [−8.0, 10.0] |
| A: TALK/VOTE stages | 480 shared TALK generations; 960 units | A11/A10 consistency 78/80 vs 78/80; 0.0 pp [−5.0, 5.0] |
| B: joint adoption | 200 four-condition blocks; 800 selected games | G11/G00 wins 65/200 vs 53/200; +6.0 pp [−3.0, 15.5] |

Intervals are the original paired cluster bootstrap intervals. Fixed-board success requires both repetitions; false PP requires either repetition. Consistency does not imply a correct vote. A does not isolate individual instruction clauses. B does not establish an overall win-rate improvement. The boards are development-derived, role and cue are confounded, and semantic judgments have not been validated against human labels.

## Contents

- [`src/`](src/): agent implementation, including output guards.
- [`experiments/`](experiments/README.md): shared evaluation/runtime tools and paper-specific PP protocols.
- [`release/`](release/): offline reproduction, selected-run exporter, and integrity tooling.
- `dataset/`: local data package; [card source](release/dataset-card.md) and [data dictionary](docs/data-dictionary.md).
- [`docs/artifact-index.csv`](docs/artifact-index.csv): paper sections → evidence → analysis code.
- [`tests/`](tests/README.md): offline regression tests grouped by agent, infrastructure, and experiment family.

[Reproduction and verification details](docs/reproduction.md) · [New experiments](docs/new-experiments.md)

## License and citation

The existing MIT code license and Kano Laboratory copyright notice are preserved in [LICENSE](LICENSE). See [third-party notices](THIRD_PARTY_NOTICES.md). The separately distributed dataset carries its own MIT `LICENSE` and preserves the upstream notice for embedded code in `CODE-LICENSE-MIT`. Manuscript source and PDF files are not bundled in this repository. Citation metadata and the code repository URL are in [CITATION.cff](CITATION.cff); paper publication identifiers will be added when assigned.
