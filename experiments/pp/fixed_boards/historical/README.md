# Historical fixed-board judge runner

`pp_judge_original.py` is the byte-identical runner recorded by the original fixed-board semantic-judging run on August 16, 2026. It was recovered from development commit `ca973c12057c36674fb3636772f95b38195830ea`, path `experiments/pp/fixed_boards/pp_judge.py`. Its SHA-256 is `ccc372def4e086b03e46d8579a7c6857a269aec84416c234aaa848e3087ad798`, matching `runner_sha256` in `dataset/raw/fixed_judge.tar.gz::manifest.json`. Machine-readable details are in [provenance.json](provenance.json).

This snapshot is retained for source audit. The supported runner is [../pp_judge.py](../pp_judge.py); offline reproduction consumes saved judgments and does not execute this historical file. The snapshot retains its original imports and provider-calling implementation, and has not been adapted into a standalone command at its new location.

The only source difference from the supported runner at restoration is the line `events["target-vote"] = judge_input["target_vote"]` in `evidence_errors`. The historical validator accepts the submitted VOTE as an evidence event; the current validator omits that event. Both versions still construct judge inputs containing `target_vote`. Restoring this source neither changes saved semantic labels nor makes the original scoring independent of the submitted VOTE.
