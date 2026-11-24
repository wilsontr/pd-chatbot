"""Deterministic claim checks for the generation eval (TASK-5.20).

The judge is an LLM and it is not reliable about ordering claims. On the run that
produced this module it scored a ``[line~]`` answer 5/5/5 while that answer's own
list example put the ramp time first — the exact error an earlier run had been
penalised for. A prompt rule lowers how often the wrong claim is made; a
deterministic check is what makes a recurrence visible in CI whether or not the
judge notices.

Two kinds of check live here:

* **Corpus-wide rules** — statements that are wrong in any Pd answer: a list
  filling an object's inlets right to left (that rule is about outlets), and a
  URL that no retrieved excerpt contains.
* **Pinned entries** — the specific wrong claim a golden entry has produced
  before, keyed by entry id: the ``[line~]`` list example's element order
  (``q_004``) and a ``[pack]``/``[unpack]`` claim with no excerpt covering those
  objects (``q_007``).

The pinned patterns come from recorded failing answers, so they detect a known
bad phrasing rather than proving an answer correct — that is the point. They are
offline and free, so they run on every scored answer inside
``run_generation_eval`` and in pytest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit

_URL_RE = re.compile(r"https?://[^\s)>\]]+")
# A right-to-left claim about *outlets* is correct Pd, and "from the right" on its
# own means nothing ("the time inlet is the one on the right"). Each alternative
# below therefore needs an inlet/list word next to the ordering phrase, so
# "the trigger sequences messages right to left" (about outlets) does not match.
_RIGHT_TO_LEFT_RE = re.compile(
    r"(?:\blists?\b|\bvalues?\b|\binlets?\b|\bfloats?\b|\bnumbers?\b)"
    r"[^.\n]{0,30}right[\s-]?to[\s-]?left"
    r"|(?:fills?|filling|distributes?|distributing|spreads?|spreading)"
    r"[^.\n]{0,40}from the right"
    r"|from the right\s+(?:first|to the left)"
    r"|rightmost inlet[^.\n]{0,25}\b(?:first|before)\b"
)
#: An ordering claim that only makes sense about outlets ("its outlets fire right
#: to left", "outlets appear right to left"); used by the q_007 rule below.
_OUTLET_ORDER_CLAIM_RE = re.compile(
    r"right[\s-]?to[\s-]?left"
    r"|rightmost outlet[^.\n]{0,25}\b(?:first|before)\b"
)
#: A retrieved section supports an ordering claim only if it states the ordering.
_ORDER_STATEMENT_RE = re.compile(r"right[\s-]?to[\s-]?left")

#: q_004: the recorded failure said the time comes first in the list
#: ("send the time and the target together, for example a list `100 500`"), and
#: a later run wrote the same thing as "(time, then target, ...)".
_TIME_THEN_TARGET_THEN_LIST_RE = re.compile(
    r"\btime\b[^.\n]{0,40}\btarget\b[^.\n]{0,40}\blists?\b"
)
_TIME_THEN_TARGET_PAREN_RE = re.compile(r"\((?:the\s+)?time\b[^)\n]{0,40}\btarget\b")
#: The correct advice is to send the two values separately, which mentions the
#: same words in the same order and must not be flagged.
_SEPARATE_ADVICE_RE = re.compile(
    r"separate|separately|individually|one at a time|"
    r"not as a (?:single )?list|instead of a (?:single )?list|rather than a list"
)
#: "ramp to <target> over <ms> ms" next to a two-number list literal: the literal
#: must be "<target> <ms>", because Pd fills inlets left to right.
_RAMP_RE = re.compile(
    r"ramp(?:s|ing)?\s+(?:it\s+|the\s+\w+\s+)?to\s+(-?\d+(?:\.\d+)?)\s+over\s+"
    r"(-?\d+(?:\.\d+)?)\s*(?:ms|milliseconds)"
)
_NUMBER_PAIR_RE = re.compile(r"(?<![\w.])(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)(?![\w.])")

#: Entry id -> object names the answer may only discuss if an excerpt covers them.
#: q_007's recorded failure stretched a "2.8 Subpatches" section (which never
#: mentions unpack) to support a claim about `[unpack]`'s outlets.
_COVERAGE_RULES: dict[str, tuple[str, ...]] = {
    "q_007": ("pack", "unpack"),
}

#: ``error`` fails the gate; ``warning`` is reported but does not. Only a claim
#: that is *false* or a fabricated citation is an error. A true claim the
#: retrieved excerpts happen not to state is a warning: the prompt asks for
#: attribution, but the model makes this one often enough (roughly one run in
#: three on q_007) that gating on it would make CI flaky.
ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class ClaimViolation:
    """One deterministic check that fired on one answer."""

    entry_id: str
    check: str
    detail: str
    evidence: str = ""
    severity: str = ERROR

    def as_dict(self) -> dict[str, str]:
        return {
            "entry_id": self.entry_id,
            "check": self.check,
            "detail": self.detail,
            "evidence": self.evidence,
            "severity": self.severity,
        }


def _sentences(text: str) -> list[str]:
    """Split into sentence-ish units so a check cannot fire across a paragraph.

    Newlines, periods and semicolons all end a unit: the checks look for two
    ideas in one sentence ("a list sends the time, then the target"), and
    matching across a boundary would flag correct answers.
    """
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+|;\s+", text) if s.strip()]


def normalise_url(url: str) -> str:
    """Compare URLs by host and path, ignoring scheme, fragment and trailing slash.

    The prompt asks for character-for-character copies, but the failure mode this
    guards is a *fabricated* path, and a real page cited as ``https`` where the
    excerpt says ``http`` (recorded on q_013, trial 2 of the first seeded run) is
    not a fabrication. Comparing ``host + path`` keeps the check aimed at invented
    pd.iem.sh links while keeping it out of the build's way.
    """
    cleaned = url.strip().rstrip(".,;:)]} ")
    parts = urlsplit(cleaned)
    host = (parts.netloc or "").lower().removeprefix("www.")
    path = (parts.path or "").rstrip("/")
    return f"{host}{path}" if host else cleaned.lower()


def _excerpt_urls(chunks: Iterable[Mapping[str, Any]]) -> set[str]:
    urls: set[str] = set()
    for chunk in chunks:
        url = chunk.get("url")
        if isinstance(url, str) and url.strip():
            urls.add(normalise_url(url))
    return urls


def check_outlet_order_supported(
    text: str, chunks: Sequence[Mapping[str, Any]]
) -> str | None:
    """Return the ordering claim when no retrieved excerpt states the ordering.

    Outlets firing right to left is true Pd, but it is a documented behaviour the
    corpus states in one place (§2.4.4). An answer that asserts it while the
    retrieved sections never do is an unsupported claim, which is what the TASK-5.1
    run was penalised for (it stretched a subpatches section). It is a warning
    rather than an error because the claim itself is not false.
    """
    match = _OUTLET_ORDER_CLAIM_RE.search(text.lower())
    if not match:
        return None
    for chunk in chunks:
        haystack = " ".join(
            str(chunk.get(key) or "") for key in ("heading_path", "text")
        ).lower()
        if _ORDER_STATEMENT_RE.search(haystack):
            return None
    return match.group(0)


#: Other software an answer may contrast Pd with. A sentence about Max's ordering
#: is not a claim about Pd's, and comparing the two is a natural thing to write
#: (recorded twice on q_011: "in Max, outputs ... go out right to left").
_OTHER_SOFTWARE_RE = re.compile(r"\bmax(?:/msp)?\b|\bsupercollider\b|\breaktor\b")


def check_inlet_order(text: str) -> str | None:
    """Return the offending sentence when an answer fills inlets right to left.

    Skipped sentences: one that also mentions outlets or output (the corpus's
    section title is literally "Hot and cold inlets and right to left outlet
    order", answers cite it, and the documented rule is phrased as "output from
    right to left"), and one that compares Pd's ordering with another program's.
    """
    for sentence in _sentences(text):
        lowered = sentence.lower()
        if not _RIGHT_TO_LEFT_RE.search(lowered):
            continue
        if re.search(r"\boutlets?\b", lowered):
            continue
        # "output from right to left", including a quoted reference sentence that
        # happens to mention the hot/cold inlet paradigm first (recorded on
        # q_011, agent arm, trial 1 of the TASK-5.23 run). Output order is the
        # outlet rule; the check is aimed at *inlet* filling.
        if re.search(r"\boutputs?\b", lowered):
            continue
        if _OTHER_SOFTWARE_RE.search(lowered):
            continue
        return sentence
    return None


def check_citations(text: str, chunks: Sequence[Mapping[str, Any]]) -> list[str]:
    """Return URLs cited by the answer that no retrieved excerpt contains."""
    allowed = _excerpt_urls(chunks)
    unknown: list[str] = []
    for raw in _URL_RE.findall(text):
        url = normalise_url(raw)
        if url and url not in allowed and url not in [normalise_url(u) for u in unknown]:
            unknown.append(raw)
    return unknown


def check_line_list_order(text: str) -> str | None:
    """Return the offending fragment when a ``[line~]`` list example is reversed.

    Three signals, all taken from recorded answers:

    * prose that puts the ramp time before the target *as a list*;
    * a numeric example that contradicts itself, e.g. "ramp to 0.5 over 1000 ms
      using `1000 0.5`" — the first list element must be the target;
    * a numeric example that *agrees* with its stated ramp (e.g. "to ramp to 0.8
      over 500 ms ... or use the list `0.8 500`") is a correct alternative, so it
      suppresses the prose signal in that sentence rather than being flagged by
      it. Recorded false positive on the agent arm, trial 2 of the TASK-5.23 run.

    Advice to send the two values as separate messages is correct and is skipped.
    """
    for sentence in _sentences(text.lower()):
        if _SEPARATE_ADVICE_RE.search(sentence):
            continue

        ramp = _RAMP_RE.search(sentence)
        if ramp:
            target, milliseconds = float(ramp.group(1)), float(ramp.group(2))
            pairs = list(_NUMBER_PAIR_RE.finditer(sentence))
            for pair in pairs:
                first, second = float(pair.group(1)), float(pair.group(2))
                if first == milliseconds and second == target:
                    return f"{ramp.group(0)} ... {pair.group(0)}"
            if any(
                float(pair.group(1)) == target
                and float(pair.group(2)) == milliseconds
                for pair in pairs
            ):
                # The literal matches the stated ramp, so the example is right
                # even if the sentence also talks about time before target.
                continue

        match = _TIME_THEN_TARGET_THEN_LIST_RE.search(sentence)
        if match:
            return match.group(0)
        match = _TIME_THEN_TARGET_PAREN_RE.search(sentence)
        if match:
            return match.group(0)
    return None


def _name_re(name: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w~+]){re.escape(name)}(?![\w~+])")


def _covers(name: str, chunks: Iterable[Mapping[str, Any]]) -> bool:
    pattern = _name_re(name)
    for chunk in chunks:
        if chunk.get("object_name") == name:
            return True
        haystack = " ".join(
            str(chunk.get(key) or "") for key in ("heading_path", "text")
        )
        if pattern.search(haystack):
            return True
    return False


def check_coverage(
    text: str, chunks: Sequence[Mapping[str, Any]], names: Sequence[str]
) -> list[str]:
    """Return the object names the answer discusses without an excerpt covering them."""
    missing: list[str] = []
    for name in names:
        if not _name_re(name).search(text):
            continue
        if not _covers(name, chunks):
            missing.append(name)
    return missing


def check_answer(
    entry_id: str,
    answer: str,
    chunks: Sequence[Mapping[str, Any]] = (),
) -> list[ClaimViolation]:
    """Run every applicable deterministic check on one scored answer."""
    violations: list[ClaimViolation] = []
    text = answer or ""

    sentence = check_inlet_order(text)
    if sentence:
        violations.append(ClaimViolation(
            entry_id=entry_id,
            check="inlet-order",
            detail="claims inlets/lists fill right to left (that rule is about outlets)",
            evidence=sentence[:300],
        ))

    for url in check_citations(text, chunks):
        violations.append(ClaimViolation(
            entry_id=entry_id,
            check="invented-source",
            detail="cites a URL that no retrieved excerpt contains",
            evidence=url[:300],
        ))

    if entry_id == "q_004":
        evidence = check_line_list_order(text)
        if evidence:
            violations.append(ClaimViolation(
                entry_id=entry_id,
                check="line-list-order",
                detail=(
                    "list example contradicts Pd's left-to-right inlet order "
                    "(the ramp time is not the first element)"
                ),
                evidence=evidence[:300],
            ))

    names = _COVERAGE_RULES.get(entry_id)
    if names:
        missing = check_coverage(text, chunks, names)
        if missing:
            violations.append(ClaimViolation(
                entry_id=entry_id,
                check="object-coverage",
                detail=(
                    "discusses an object the retrieved excerpts do not cover"
                ),
                evidence=", ".join(missing)[:300],
            ))
        evidence = check_outlet_order_supported(text, chunks)
        if evidence:
            violations.append(ClaimViolation(
                entry_id=entry_id,
                check="outlet-order-unsupported",
                detail=(
                    "states an outlet ordering that no retrieved excerpt states"
                ),
                evidence=evidence[:300],
                severity=WARNING,
            ))

    return violations


def violations_by_arm(entries: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, str]]]:
    """Group recorded violations by eval label, for the report and the gate."""
    grouped: dict[str, list[dict[str, str]]] = {}
    for entry in entries:
        label = str(entry.get("label") or entry.get("model") or "?")
        for violation in entry.get("claim_violations") or []:
            grouped.setdefault(label, []).append(dict(violation))
    return grouped


def describe(violation: Mapping[str, str]) -> str:
    """One-line rendering used by the report and the job summary."""
    return (
        f"{violation.get('entry_id', '?')} — {violation.get('check', '?')}: "
        f"{violation.get('detail', '')}"
    )
