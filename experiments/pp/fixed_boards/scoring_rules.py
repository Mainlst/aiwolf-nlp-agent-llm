"""Versioned English declaration extraction for offline PP scoring.

These rules use TALK and participant names only. They do not inspect submitted
votes, true roles, condition labels, outcomes, or scenario IDs. Unresolved
pronouns and unsupported formulations remain unextracted rather than guessed.
"""

# The patterns intentionally keep each grammar rule together for review.
# ruff: noqa: C901, E501, INP001, PLR0911, PLR0915

from __future__ import annotations

import re
from typing import Any

SCORING_RULES_VERSION = "english-declarations-v2-20260910"


def _unquoted(text: str) -> str:
    """Mask quoted speech without moving character offsets or contractions."""
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    for pattern in (r'"[^"\n]*"', r"“[^”\n]*”", r"`[^`\n]*`", r"(?<!\w)'(?:[^'\n]|'(?=\w))*?'(?!\w)"):
        text = re.sub(pattern, lambda m: " " * len(m.group()), text)
    return text


def _clause_start(text: str, position: int) -> int:
    boundaries = list(re.finditer(r"[.!?;\n]", text[:position]))
    return boundaries[-1].end() if boundaries else 0


def _embedded(text: str, position: int) -> bool:
    """Exclude indirect, denied, and conditional declarations in this clause."""
    prefix = text[_clause_start(text, position) : position]
    # A new explicit contrast/conclusion begins an independently asserted clause.
    prefix = re.split(r"\b(?:but|however|therefore|so)\b", prefix, flags=re.IGNORECASE)[-1]
    return bool(
        re.search(
            r"\b(?:if|unless|whether|suppos\w*|imagin\w*|pretend\w*|den(?:y|ies|ied)|reject\w*|"
            r"accus\w*|(?:your|his|her|their)\s+claim|claims?\s+that|says?|said|"
            r"thinks?|thought|believes?|believed|convinced|not\s+claiming|not\s+saying)\b",
            prefix,
            re.IGNORECASE,
        ),
    )


def affirmative_role_co(text: str) -> str | None:
    """Return the last explicit affirmative self-claim outside quoted speech."""
    clean = _unquoted(text)
    claims = []
    for match in re.finditer(r"\bI\s*(?:am|'m)\s+(?:(?:the|a)\s+)?(possessed|werewolf|wolf)\b", clean, re.IGNORECASE):
        if _embedded(clean, match.start()):
            continue
        sentence_tail = re.split(r"[.!;\n]", clean[match.end() :], maxsplit=1)[0]
        if "?" in sentence_tail or re.match(
            r"\s*,?\s*(?:according\s+to|is\s+(?:false|what))\b", sentence_tail, re.IGNORECASE,
        ):
            continue
        claims.append("POSSESSED" if match.group(1).lower() == "possessed" else "WEREWOLF")
    return claims[-1] if claims else None


def vote_target_details(text: str, names: list[str]) -> dict[str, Any]:
    """Extract the last supported affirmative declaration; expose its evidence.

    Supported forms name one's vote/target, declare a vote, or directly request
    coordinated voting. An explicitly named current voting preference may be
    provisional ("I lean toward voting X", "I may vote X"); extraction does not
    measure strength of commitment. Bare suspicions/alternatives, if-clauses,
    questions, negated choices, and lists of alternative targets are excluded.
    Descriptions of someone else's vote are not candidates. Pronouns require a
    local syntactic antecedent; names elsewhere are never used as a fallback.
    """
    if not names:
        return {"target": None, "candidates": [], "unresolved": []}
    clean = _unquoted(text)
    canonical = {name.lower(): name for name in names}
    alternatives = "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True))
    person = rf"(?:{alternatives})"
    referent = rf"(?:{person}|you|him|her|them)"
    target = rf"(?<![\w-])(?P<target>{referent})\b"
    prep = r"(?:(?:for|on|against|onto|to)\s+)?"
    candidate_noun = r"(?:(?:the\s+)?(?:villager(?:-side)?\s+)?candidate\s+)?"
    self_prefix = r"\bI(?:\s+(?:will|shall|may|might|would)|'ll|'d)?\s+"
    qualification = r"(?:(?:final|current|provisional|leading)\s+)?"
    patterns = [
        (
            "own_vote",
            self_prefix
            + r"(?:(?:still|now|finally|therefore|currently|provisionally|likely)\s+)?vote\s+"
            + prep
            + target,
        ),
        ("own_voting", r"\bI\s*(?:am|'m)\s+(?:(?:still|now)\s+)?voting\s+" + prep + target),
        ("own_intention", r"\bI\s+(?:intend|plan|commit)\s+to\s+vote\s+" + prep + target),
        (
            "keep_vote",
            r"\bI(?:'m|'ll|\s+am|\s+will)?\s+(?:keep(?:ing)?|maintain|cast|confirm|reaffirm|change|switch|lock)\s+my\s+(?:final\s+)?vote\s+"
            + prep
            + target,
        ),
        ("keep_voting", self_prefix + r"keep\s+voting\s+" + prep + target),
        (
            "my_vote",
            r"\bmy\s+"
            + qualification
            + r"(?:vote(?:\s+target)?|target)\s*(?:(?:is|remains|stays|will\s+be|goes\s+to)\s+|:\s*)"
            + prep
            + target,
        ),
        (
            "reverse_vote",
            target
            + r"(?:'s\s+(?:[a-z-]+\s+){0,3}claim)?\s+(?:is|are|remains|stays|currently)\s+(?:still\s+)?my\s+"
            + qualification
            + r"(?:vote(?:\s+(?:target|candidate))?|(?:execution\s+)?target)\b",
        ),
        (
            "make_vote",
            r"\b(?:make[sd]?|keep)\s+"
            + target
            + r"\s+(?:as\s+)?my\s+"
            + qualification
            + r"(?:vote|(?:execution\s+)?target)\b",
        ),
        ("possessive_vote", r"\b(?P<target>his|her|their)\s+claim\s+is\s+(?:still\s+)?my\s+vote\s+target\b"),
        (
            "vote_preference",
            r"\bI(?:'m|\s+am)?\s+(?:currently\s+)?(?:lean(?:ing)?\s+(?:(?:toward|towards)\s+)?|favor\s+|agree\s+with\s+)voting\s+"
            + prep
            + target,
        ),
        (
            "preference_as_vote",
            r"\bI(?:'m|\s+am)?\s+(?:currently\s+)?(?:favor|prefer|lean(?:ing)?)\s+(?:(?:toward|towards)\s+)?"
            + target + r"\s+as\s+(?:today's|my)\s+(?:vote|execution)\b",
        ),
        (
            "continued_vote_preference",
            r"\bI\s+[^.!?;\n]*?\band\s+(?:lean\s+(?:(?:toward|towards)\s+)?|favor\s+)voting\s+"
            + prep + target,
        ),
        (
            "suspect_then_vote",
            r"\bI\s+(?:suspect|favor|prefer|choose)\s+" + target + r"\s+(?:most\s+)?and\s+will\s+vote\s+there\b",
        ),
        ("join_vote", self_prefix + rf"join\s+(?:your|{person}'s)\s+vote\s+" + prep + target),
        ("joint_vote", r"\b(?:we(?:\s+will|'ll)?|let's)\s+(?:both\s+)?vote\s+" + prep + target),
        ("named_joint_vote", rf"\b{person}\s+and\s+I\s+(?:should|will|shall)\s+vote\s+" + prep + target),
    ]
    # Imperatives must start a clause or follow a vocative/conjunction. This
    # blocks "her immediate vote on X" and "he rushed to vote X".
    lead = rf"(?:^|[.!?;:\n,]|-(?=(?:vote|declare|match)\b)|\b(?:and|so|then)\b)\s*(?:@?{person}\s*[,.]?\s*)?(?:(?:please|wolf|werewolf)\s*[,.]?\s*)?"
    patterns += [
        ("request_vote", lead + r"(?:declare\s+and\s+)?(?:cast\s+your\s+)?vote\s+" + prep + candidate_noun + target),
        (
            "declare_vote",
            lead + r"declare\s+(?:and\s+cast\s+)?(?:(?:a|your|the)\s+)?vote\s+" + prep + candidate_noun + target,
        ),
        (
            "declare_candidate",
            lead
            + r"declare\s+"
            + target
            + r"(?=\s*(?:[.!;]|$)|\s+(?:as\s+(?:your\s+|the\s+|a\s+)?(?:villager(?:-side)?\s+)?(?:vote|target|candidate)|and\s+(?:match|vote)))",
        ),
        (
            "coordinate",
            lead
            + r"(?:let's\s+)?(?:match\s+(?:(?:my|our|your)\s+votes?|me)|unite\s+(?:our\s+)?votes?)\s+(?:on|onto|for|against)\s+"
            + target,
        ),
        ("joint_coordination", r"\blet's\s+match\s+votes\s+(?:on|onto|for)\s+" + target),
        (
            "coordinate_execution",
            lead + r"match\s+(?:(?:my|our)\s+votes?|me)\s+and\s+(?:execute|vote(?:\s+for)?)\s+" + target,
        ),
        ("stay_vote", lead + r"(?:please\s+)?(?:stay|keep\s+your\s+vote)\s+(?:on|with)\s+" + target),
    ]

    def resolve(word: str, position: int) -> str | None:
        word = word.lower()
        if word in canonical:
            return canonical[word]
        before = clean[:position]
        if word == "you":
            # An explicit address may continue into the immediately following
            # sentence. Several addressed people leave singular "you" unresolved.
            for sentence in reversed(re.split(r"[.!?\n]", before)[-2:]):
                addressed = {
                    canonical[m.group(1).lower()]
                    for m in re.finditer(rf"@({person})\b", sentence, re.IGNORECASE)
                }
                if len(addressed) == 1:
                    return addressed.pop()
                if len(addressed) > 1:
                    return None
                m = re.match(rf"\s*({person})\s*,", sentence, re.IGNORECASE)
                if m:
                    return canonical[m.group(1).lower()]
            return None
        # Pronouns refer only to a named subject in the current or preceding
        # sentence, never to a later addressee or a name in an object position.
        recent = [
            part
            for part in re.split(r"[.!?\n]", before)
            if not re.fullmatch(rf"\s*(?:@?{person}|wolf|werewolf)\s*", part, re.IGNORECASE)
        ][-2:]
        for sentence in reversed(recent):
            subjects = list(
                re.finditer(
                    rf"(?:^|[;,]|\b(?:but|while|and|so)\b)\s*({person})"
                    r"(?:'s\s+(?=[a-z])|\s+(?:(?:only|still|already)\s+)?"
                    r"(?:is|was|remains|has|had|gave|cleared|claimed|claim|vote|report)\b)",
                    sentence,
                    re.IGNORECASE,
                ),
            )
            distinct = {canonical[m.group(1).lower()] for m in subjects}
            if len(distinct) == 1:
                return distinct.pop()
            if len(distinct) > 1:
                return None
            without_address = re.sub(rf"@{person}\b", "", sentence, flags=re.IGNORECASE)
            if re.search(rf"\b{person}\b", without_address, re.IGNORECASE):
                # A different named referent in the current clause makes a
                # fallback to a previous sentence unsafe even if its syntax
                # falls outside the supported subject patterns.
                return None
        return None

    candidates, unresolved = [], []
    seen = set()
    for rule, pattern in patterns:
        for match in re.finditer(pattern, clean, re.IGNORECASE):
            start, end = match.span("target")
            if (start, end) in seen:
                continue
            seen.add((start, end))
            assertion_start = match.start() + len(re.match(r"[\s.!?;:,\n-]*", match.group()).group())
            if _embedded(clean, assertion_start):
                continue
            if rule == "declare_candidate" and not re.search(r"\b(?:vote|votes|voting|match)\b", clean, re.IGNORECASE):
                continue
            # Questions, conditional alternatives, and local negation do not
            # make an affirmative choice. A following explanation is harmless.
            clause_tail = re.split(r"[.!;\n]", clean[match.end() :], maxsplit=1)[0]
            alternative_subject = rule == "reverse_vote" and re.search(
                rf"\b{person}\s+(?:or|and)\s*$", clean[:start], re.IGNORECASE,
            )
            if alternative_subject or "?" in clause_tail or re.match(r"\s+(?:or|if)\b", clause_tail, re.IGNORECASE):
                unresolved.append(
                    {
                        "span": [start, end],
                        "text": text[match.start() : match.end()],
                        "reason": "conditional_or_alternative",
                    },
                )
                continue
            local_prefix = clean[max(_clause_start(clean, assertion_start), assertion_start - 20) : assertion_start]
            if re.search(r"\b(?:not|don't|never)\s*$", local_prefix, re.IGNORECASE):
                continue
            value = resolve(match.group("target"), start)
            event = {"target": value, "span": [start, end], "text": text[match.start() : match.end()], "rule": rule}
            if value is None:
                unresolved.append({**event, "reason": "unresolved_pronoun"})
            else:
                candidates.append(event)
    candidates.sort(key=lambda x: (x["span"][0], x["span"][1]))
    unresolved.sort(key=lambda x: x["span"])
    # A later unresolved affirmative choice supersedes an earlier named choice;
    # preserving the earlier choice would hide an ambiguous target change.
    last = candidates[-1] if candidates else None
    value = last["target"] if last else None
    if unresolved and (last is None or unresolved[-1]["span"][0] > last["span"][0]):
        value = None
    # A possible new target alongside another named choice has no clear
    # priority. A later definite selection, by contrast, supersedes it.
    if (
        last
        and re.search(r"\b(?:may|might|would)\b|\bI'd\b", last["text"], re.IGNORECASE)
        and len({event["target"] for event in candidates}) > 1
    ):
        value = None
        unresolved.append({**last, "reason": "multiple_possible_choices"})
    return {"target": value, "candidates": candidates, "unresolved": unresolved}


def declared_vote_target(text: str, names: list[str]) -> str | None:
    """Return the target of the last supported unambiguous voting declaration."""
    return vote_target_details(text, names)["target"]
