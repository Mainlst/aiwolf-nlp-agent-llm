# Deterministic scoring correction, 2026-09-10

This is a disclosed correction after observing the original results and the
2026-09-09 audit. It is not a new preregistered experiment or an independent
human-label validation. No model responses or semantic judgments are regenerated.

The two corrected measurements are (1) the final explicitly stated current vote
candidate or direct request for coordinated voting, and (2) an affirmative
first-person possessed/werewolf claim. The extractor receives only the utterance
and participant names, never the experimental condition, submitted vote, true
role, winner, old score, or hand-picked record IDs. The same rules apply to every
condition, positive board and near-control in the original fixed-board and A
samples, and to every eligible pre-vote wolf/possessed utterance in B.

An account of another player's vote is not the speaker's declaration. Names
before "my vote/target" are allowed. A unique present voting preference may be
tentative (for example "my provisional vote" or "I lean toward voting X"); this
measurement does not estimate certainty or recover a latent intention. Negated,
hypothetical, and quoted commitments
and role claims are excluded. Direct addressees are not automatically targets.
Pronouns require a unique local referent; ambiguous statements are not resolved
using the actual vote or hidden roles. The implementation and regression cases
document the supported constructions and limitations.

Every previously included record remains included. Existing generation, language
validation, truth labels, semantic-majority labels, conditions, paired units,
repetition rules, bootstrap seeds and iteration counts, primary comparisons and
multiplicity rules remain the same. The original 400-game post-hoc heuristic has
a different implementation and retains its original definition; its reanalysis
updates the link to the corrected fixed-board statistics used for Holm control.

The correction verifies saved source snapshots and frozen input/output hashes,
without requiring that today's source equal the archived source or requiring a
game server to analyze existing logs. Original manifests, sources, judgments and
analysis files are never overwritten. Corrected analyses go to a distinct output
directory, together with input hashes, corrected source snapshots, per-utterance
extractions, every changed score, and an explicit old/new comparison.

Validation includes adversarial regression tests, application to all eligible
utterances rather than only the known errors, independent inspection of changed
and unresolved extractions, and reproduction of the corrected aggregates. This
provides an auditable deterministic correction; it does not establish perfect
natural-language extraction or replace independent human annotation.
