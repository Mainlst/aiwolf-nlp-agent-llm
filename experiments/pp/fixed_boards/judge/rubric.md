# Blind PP mechanism classifier

Classify only three binary properties of the target action. Do not score writing
quality, naturalness, diversity, persona, persuasiveness, or overall strategy.

The input uses anonymized player IDs. `public_history` contains only public
events at or before the decision snapshot. `target_talk` and `target_vote` are
the generated action being classified. `reference` supplies the perspective
role, living players, the designated wolf-side ally when one exists, and legal
village targets. These reference facts are evaluation metadata, not statements
made by players.

Apply these definitions independently:

- `pp_recognized`: true only when `target_talk` unambiguously states or acts on
  the proposition that the werewolf and possessed can presently control the
  vote. A role claim alone is not sufficient. Do not decide whether that
  proposition is objectively true; classify whether the target action asserts
  or recognizes it.
- `ally_recognized`: true only when `target_talk` unambiguously identifies or
  addresses `reference.correct_ally` as the wolf-side ally. Return false when
  `correct_ally` is null. Merely avoiding a vote against that player is not
  sufficient.
- `vote_coordination`: true only when `target_talk` unambiguously asks,
  instructs, promises, or commits to align a vote with the designated ally on
  the same named player in `reference.valid_village_targets`. General requests
  to vote together, unnamed targets, and coordination with another player are
  false.

For every field, cite one to three exact short quotes from the supplied events.
Use `target-talk` for wording in the generated TALK and a public-history event
ID when the classification depends on an earlier claim. A false label may cite
the target TALK that lacks or contradicts the required semantics. Quotes must
occur verbatim in the cited event. Return only the structured result.
