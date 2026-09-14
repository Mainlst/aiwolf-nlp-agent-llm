# B continuation and attempt selection

This is a historical incident summary, not an instruction to resume a completed run. The accepted B dataset is `pp-revision-B-20260909-resume8-v1`; its final sample contains 200 complete blocks, 800 accepted games and 820 attempts.

The earlier 20-game-parallel run stopped after a TLS connection reset during a seer request in B144-G01. Memory pressure was also observed, but the records did not establish that it caused the network reset. The original audit could miss unfinished requests: B177-G11 had a VOTE timeout and fallback vote despite being marked complete. Therefore, the original 139 “complete” games were not all accepted as quality-verified games.

Continuation used eight parallel games and strengthened request/timeout auditing. Of the original games, 138 were retained; 20 games were repaired and 642 new games completed. Selection was based on operational validity, not on wins or effect estimates. The final release preserves failed/retryable attempts separately from the 800 selected games.

Audit evidence is in `dataset/raw/factorial.tar.gz`: `lineage/`, `repair-verified.json`, `attempts/`, `schedule.json`, `state.json`, and historical manifests. The compact ledger is `dataset/provenance/attempts.csv`. The original first-terminal-attempt selection logic is retained in `experiments/pp/revision/normal.py`; offline reproduction validates selected game hashes, complete blocks and attempt counts. The standalone superseded 20-parallel run and host-resource monitoring dumps are not separate evaluation datasets in this release.
