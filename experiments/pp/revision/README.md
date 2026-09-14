# Additional experiments A and B

A reuses each TALK for independent P+/P-PP VOTE branches (480 TALKs, 960 units). B manipulates the werewolf and possessed in four conditions per block (200 blocks, 800 accepted games, 820 attempts). Policies for the three village players remain fixed.

For the camera-ready results, use the [root offline reproduction instructions](../../../README.md). `analyze.py` retains the original computation functions; the release loader verifies the exported manifests and writes to a separate output directory. For new generation, see [new experiments](../../../docs/new-experiments.md). The historical [incident record](../../../docs/experiment-incident.md) documents continuation and repair decisions.

The [2026-09-10 correction](scoring-correction-20260910.md) changes vote-declaration and affirmative role-claim extraction after observing the results. The [scoring protocol](scoring-correction-protocol.md) records its scope and limitations. Primary comparisons, samples, saved judgments, and bootstrap settings are unchanged. A01/A00 consistency is now 62/80 and 75/80; B G01 post-reach joint CO is 0/57.

`reanalyze.py` is the maintainer entry for original, untransformed development runs and their checkpoint archive. Its defaults require those original files. For this portable distribution use `release/reproduce.py`, which verifies the exported evidence and the corrected results in `dataset/`. The correction archive contains the original correction manifest, code snapshots, old/new extraction ledgers and review records; original analyses remain under `dataset/results/original/` and in the five unchanged evidence archives.
