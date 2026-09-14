# Reproduction contract

The release entry point consumes only the selected distributed evidence and the source/configuration files covered by `release/code-manifest.json`. `dataset/checksums.sha256` covers the complete dataset inventory. The release manifest records every archive member's source and exported SHA-256; changed members are listed in `provenance/transformations.jsonl`. These hashes establish integrity against the supplied manifest, not cryptographic author authentication.

Archives retain saved prompts, outputs, votes, judgments, game histories, policy and source snapshots, schedules, frozen state, and attempt records. Synthetic player names and blind-ID joins are retained. Credentials, caches, host-resource monitoring, launchers, obsolete qualification data, and binary/derived figure files are excluded. Historical home paths are normalized. `normal/execution.jsonl` resolves selected `server_json` entries relative to the extracted run. Original `frozen.json` files remain historical records; their hashes are not falsely presented as hashes of the transformed release. The release manifest independently verifies the exported evidence.

The offline path uses the corrected declaration extractor with the original statistical procedures. It validates all A units against the schedule, both VOTE branches against the same pre-branch state, and the shared TALK/output hashes. B uses the original first-terminal-attempt selection, verifies selected game hashes, requires 200 complete blocks and 820 attempts, and retains failed attempts separately. Old source/server hashes are replaced only at the release entry point by release integrity checks; the original live-run verifier is still available for new runs.

The original 1,440 raw fixed-board judgments are aggregated into 480 majority labels and checked against the saved majority labels. A uses 1,440 selected judgments of its 480 shared TALKs. Repeated judgment attempt records remain in raw archives but are not duplicate selected judgments in the Dataset Hub view.

All non-provenance fields of the six reference JSON analyses are compared recursively, including denominators, estimates, intervals, components, role/cue strata, partner diagnostics, token summaries, and multiple-comparison adjustments. Integer and categorical values match exactly; floats use absolute/relative tolerance `1e-12`. Original fixed-board defaults are preserved; original normal games use 100,000 bootstrap draws, seed 20260815; A/B use 10,000 draws, seed 20260907. Paths, file hashes, purpose/run identifiers are excluded only at the top-level comparison because the exported files have a new location and provenance.

Outputs contain the extracted evidence, reconstructed semantic labels, `fixed/`, `normal/`, `stage/`, `factorial/`, `cue/`, `partner/`, `tables.md`, and a machine-readable verification report. Original live-run CLIs must not be pointed at this transformed release. Use a new output directory on every reproduction run.

For a release change, review and update the explicit `release/package-files.txt` inventory, run the relevant tests, deliberately regenerate manifests with `python3 release/seal.py`, and perform a fresh reproduction. Never run sealing as a way to accept an unexpected integrity failure. Code hashes include the reader itself and restored source artifacts. The manifest itself and `docs/verification.json` are excluded from its own hash coverage to avoid a dependency cycle; both are included and checked in the code ZIP. Offline reproduction cannot recreate model generations; semantic re-scoring here uses saved model judgments, not new calls or a human gold standard.

After copying the successful reproduction's `verification.json` to `docs/verification.json`, build a fresh versioned ZIP with `python3 release/package.py`, then run `python3 release/audit.py`. Packaging updates `dist/checksums.sha256`; the audit examines the actual ZIP as well as its source files and dataset archives. Its report is written outside the ZIP to `dist/distribution-audit.json`, so writing the audit cannot change the artifact being audited. The commands and publication steps are also documented in [release status](release-status.md).

The [test guide](../tests/README.md) lists full-suite and experiment-specific commands.
Shared experiment modules are grouped by role; the [code layout and migration map](../experiments/README.md)
list their current locations. The release entry point and paper-specific PP module
paths are unchanged, and saved protocol filenames and source/export hash distinctions
are preserved.

The exporter is a maintainer tool for a checkout that still contains the five original selected directories under `experiments/runs/`. Those unpacked development runs are removed from the cleaned repository after verification; the release already contains their selected evidence. It refuses to overwrite an existing export and does not fetch anything from a sibling checkout.

The 2026-09-10 correction is explicitly post-outcome. `raw/correction.tar.gz`
contains the formal correction manifest, analysis source snapshots, input hashes,
old/new differences and review ledgers. The five original raw archives are unchanged;
`results/original/` preserves the previous references and B browsing view. Current
references come from the independently saved upstream correction, rather than from
the output being verified. The entry point rejects a dataset with an incompatible
scoring version, verifies all 2,792 utterance extractions and 377 changed extractions,
and checks the corrected B browsing view against the analysis.

To prepare the updated data, stage the formally verified correction inside this
checkout, then run `python3 release/import_correction.py --data dataset --correction
<staged-correction> --output <new-directory> --upstream-commit <commit>`. This
maintainer tool refuses existing outputs and compares correction input hashes to
the **source** hashes of the base release. Path-normalized archive members receive
separate exported hashes. The standalone `experiments.pp.revision.reanalyze` command
requires original development checkpoints and must not be used on transformed data.
