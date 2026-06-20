#!/usr/bin/env python3
"""drift_dialogic.py — two-dimensional drift reporting (stdlib only).

Edgar Morin's dialogic principle: hold two antagonistic-but-complementary
truths together instead of collapsing them into one. The single DRIFT% badge
already made a choice — it treats *helpfulness as the enemy*. A reply that is
verbose, hedge-heavy, and useful scores identically to one that is verbose,
hedge-heavy, and useless. The scalar cannot tell "drifted but still doing the
job" from "drifted into noise", nor "perfectly terse" from "terse and inert".

So we refuse the collapse and report two coordinates:

* **fidelity**   — how contract-compliant the turn is (100 = perfect, the
                   inverse of the existing drift score).
* **engagement** — how directly the turn is doing the task (code, answers,
                   commands, concrete recommendations push it up; an all-hedge
                   no-action reply sits at the bottom).

These two axes give four quadrants, the most important of which is the one the
scalar can never name: high fidelity + low engagement — *compliant but failing
the task*. Morin would call it dying of order. The badge surfaces it in red.

Design contract (mirrors drift_score.py)
---------------------------------------
* Pure stdlib, deterministic, side-effect free, total (never raises).
* Reuses drift_score's prose/code separation so code is excluded from lexical
  scoring but *counted* as an engagement signal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict

from drift_score import (
    DEFAULT_LEXICONS,
    _FENCE_RE,
    _INLINE_CODE_RE,
    sentences,
    strip_code,
    tokenize,
)

ENGINE_VERSION = "1.0.0"

# Engagement thresholds. fidelity uses the profile threshold (default 70);
# engagement has its own fixed gate per the dialogic spec.
ENGAGEMENT_GATE = 50.0

# --------------------------------------------------------------------------- #
# Engagement signal detectors
# --------------------------------------------------------------------------- #
# Each detector contributes additive points (capped at 100). These are *task*
# signals — evidence the turn is doing something rather than narrating around
# the work.

# A "direct answer" opener: a bare number, a date, a proper-noun-ish capital, or
# an imperative verb leading the prose. This catches "42." / "2026-06-20" /
# "Run the migration" / "Use psycopg2" rather than "Well, I think that...".
_LEADING_NUMBER_RE = re.compile(r"^\s*[\(\[\"']?\d")
_LEADING_DATE_RE = re.compile(r"^\s*\d{4}-\d{2}-\d{2}")
_IMPERATIVE_VERBS = frozenset(
    {
        "use", "run", "set", "add", "remove", "delete", "change", "edit", "open",
        "close", "fix", "check", "call", "import", "install", "create", "make",
        "move", "copy", "rename", "replace", "update", "build", "start", "stop",
        "restart", "enable", "disable", "configure", "deploy", "commit", "push",
        "pull", "merge", "revert", "apply", "patch", "see", "read", "write",
        "click", "select", "drop", "rename", "kill", "grep", "cat", "cd", "echo",
    }
)

# File paths, URLs, shell commands — concrete artifacts the user can act on.
_PATH_RE = re.compile(r"(?:(?:\.{0,2}/)[\w./\-]+|[\w\-]+\.[A-Za-z]{1,5}\b)")
_URL_RE = re.compile(r"https?://\S+")
_CMD_RE = re.compile(r"\$\s*\S+|\b(?:git|npm|pip|cargo|docker|kubectl|ssh|curl|make)\s+\S")

# A "recommendation" pattern: use X / run Y / change Z / try W / should ...
_RECO_RE = re.compile(
    r"\b(?:use|run|try|set|add|change|replace|switch to|prefer|"
    r"you (?:should|can|could|need to)|i'd recommend|recommend)\b",
    re.IGNORECASE,
)

# Hedges are pulled straight from drift_score's lexicon so the two engines stay
# in lockstep — an all-hedge reply is exactly what the drift engine punishes.
_HEDGE_TERMS = tuple(DEFAULT_LEXICONS["hedges"])


@dataclass
class DialogicScore:
    fidelity: float      # 0-100, higher = more contract-compliant (100 = perfect)
    engagement: float    # 0-100, higher = more task-engaged
    badge: str           # e.g. "fit:88 eng:72" or "DRIFT:45 eng:91"
    quadrant: str        # "good"|"drifted"|"disengaged"|"dead"

    def to_dict(self) -> dict:
        return asdict(self)


def _has_code_block(text: str) -> bool:
    return bool(_FENCE_RE.search(text) or _INLINE_CODE_RE.search(text))


def _starts_with_direct_answer(prose: str) -> bool:
    """True if the prose opens with a concrete answer rather than preamble."""
    stripped = prose.lstrip()
    if not stripped:
        return False
    if _LEADING_DATE_RE.match(stripped) or _LEADING_NUMBER_RE.match(stripped):
        return True
    first = tokenize(stripped[:40])
    if first and first[0] in _IMPERATIVE_VERBS:
        return True
    # A leading capitalized proper-noun-ish token ("Postgres handles ...") that
    # is not a generic hedge/filler opener also reads as a direct answer.
    m = re.match(r"\s*([A-Z][A-Za-z0-9_\-]+)", prose)
    if m:
        lead = m.group(1).lower()
        if lead not in {"perhaps", "maybe", "well", "so", "actually", "honestly",
                        "basically", "essentially", "i", "it", "there", "this",
                        "that", "the", "a", "an", "we", "you", "let"}:
            return True
    return False


def _count_hedges(text_lc: str) -> int:
    n = 0
    for term in _HEDGE_TERMS:
        start = 0
        while True:
            idx = text_lc.find(term, start)
            if idx < 0:
                break
            n += 1
            start = idx + len(term)
    return n


def score_engagement(text: str) -> float:
    """Measure task-engagement: how directly is this turn addressing a task?

    Additive signals (capped at 100):
    * code block present                          +30
    * opens with a direct answer (number/date/    +20
      imperative verb / proper noun)
    * contains a file path, URL, or command       +15
    * short and specific (<50 prose words, no      +15
      hedges)
    * contains a concrete recommendation           +20
      (use X / run Y / change Z)

    Penalty: an all-hedge, no-action reply (no code, no path/cmd, no direct
    answer, no recommendation, yet carrying hedges) is pushed firmly below the
    engagement gate — it is talk *about* the task, not the task.

    Code is stripped before lexical/length scoring but its *presence* is itself
    the strongest engagement signal.
    """
    raw = text if isinstance(text, str) else ("" if text is None else str(text))
    if not raw.strip():
        return 0.0

    has_code = _has_code_block(raw)

    prose = strip_code(raw)
    prose_tokens = tokenize(prose)
    word_count = len(prose_tokens)
    text_lc = prose.lower()
    hedge_count = _count_hedges(text_lc)

    has_path_or_cmd = bool(
        _URL_RE.search(prose) or _CMD_RE.search(prose) or _PATH_RE.search(prose)
    )
    direct = _starts_with_direct_answer(prose)
    short_specific = word_count < 50 and hedge_count == 0
    has_reco = bool(_RECO_RE.search(prose))

    score = 0.0
    if has_code:
        score += 30.0
    if direct:
        score += 20.0
    if has_path_or_cmd:
        score += 15.0
    if short_specific:
        score += 15.0
    if has_reco:
        score += 20.0

    # All-hedge, no-action: nothing concrete fired but the reply hedges. This is
    # the disengaged-prose case the scalar drift score also flags — keep it low.
    no_action = not (has_code or direct or has_path_or_cmd or has_reco)
    if no_action and hedge_count > 0:
        # Each hedge in a turn that does nothing drags engagement down further.
        score -= min(40.0, 10.0 * hedge_count)

    # A pure empty-of-signal reply (no action, no hedge — e.g. "I don't know")
    # is inert: it is not engaged with the task even if it's polite.
    if no_action and not short_specific:
        score = min(score, 25.0)

    return max(0.0, min(100.0, score))


def compute_dialogic_score(
    drift_score: float, text: str, threshold: float = 70.0
) -> DialogicScore:
    """Combine the existing scalar drift score with an engagement reading.

    fidelity   = 100 - drift_score  (0 drift => 100 fidelity)
    engagement = score_engagement(text)

    Quadrants (fidelity gated on `threshold`, engagement on ENGAGEMENT_GATE=50):
      * fidelity >= T and engagement >= 50  -> "good"
      * fidelity <  T and engagement >= 50  -> "drifted"     (off-contract but useful)
      * fidelity >= T and engagement <  50  -> "disengaged"  (compliant but inert)
      * fidelity <  T and engagement <  50  -> "dead"        (off-contract AND useless)

    badge: "fit:NN eng:MM" when on-contract, "DRIFT:NN eng:MM" when drifted,
    where the DRIFT number is the original drift score (alarming reading).
    """
    try:
        ds = float(drift_score)
    except (TypeError, ValueError):
        ds = 0.0
    ds = max(0.0, min(100.0, ds))
    fidelity = max(0.0, min(100.0, 100.0 - ds))
    engagement = score_engagement(text)

    try:
        thr = float(threshold)
    except (TypeError, ValueError):
        thr = 70.0
    thr = max(0.0, min(100.0, thr))

    fid_ok = fidelity >= thr
    eng_ok = engagement >= ENGAGEMENT_GATE

    if fid_ok and eng_ok:
        quadrant = "good"
    elif not fid_ok and eng_ok:
        quadrant = "drifted"
    elif fid_ok and not eng_ok:
        quadrant = "disengaged"
    else:
        quadrant = "dead"

    if fid_ok:
        badge = f"fit:{round(fidelity)} eng:{round(engagement)}"
    else:
        badge = f"DRIFT:{round(ds)} eng:{round(engagement)}"

    return DialogicScore(
        fidelity=round(fidelity, 2),
        engagement=round(engagement, 2),
        badge=badge,
        quadrant=quadrant,
    )


# --------------------------------------------------------------------------- #
# Status-line formatting
# --------------------------------------------------------------------------- #

_ANSI = {
    "reset": "\033[0m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "red": "\033[31m",
    "bold_red": "\033[1;31m",
}

# good        -> green     (on-contract and useful)
# drifted     -> yellow    (off-contract but still useful: less urgent)
# disengaged  -> red       (compliant but failing the task — Morin's "died of order")
# dead        -> bold red  (the worst: off-contract AND useless)
_QUADRANT_COLOR = {
    "good": "green",
    "drifted": "yellow",
    "disengaged": "red",
    "dead": "bold_red",
}


def format_dialogic_badge(d: DialogicScore) -> str:
    """Return the badge wrapped in the ANSI color for its quadrant."""
    color = _ANSI.get(_QUADRANT_COLOR.get(d.quadrant, ""), "")
    if not color:
        return d.badge
    return f"{color}{d.badge}{_ANSI['reset']}"


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #


def _selftest() -> int:
    # Good: low drift + concrete action.
    good = compute_dialogic_score(
        10.0, "Use psycopg2. Run `pip install psycopg2`. See db.py line 42."
    )
    assert good.fidelity == 90.0, good
    assert good.engagement >= 50.0, good
    assert good.quadrant == "good", good
    assert good.badge.startswith("fit:"), good

    # Drifted-but-useful: high drift, but it ships code => still engaged.
    drifted = compute_dialogic_score(
        80.0,
        "Certainly! I'd be delighted to help. Here is a robust, elegant "
        "solution:\n```python\ndef f():\n    return 1\n```\nLet me know!",
    )
    assert drifted.fidelity < 70.0, drifted
    assert drifted.engagement >= 50.0, drifted
    assert drifted.quadrant == "drifted", drifted
    assert drifted.badge.startswith("DRIFT:"), drifted

    # Disengaged: perfectly on-contract (terse) but doing nothing useful.
    disengaged = compute_dialogic_score(5.0, "Hmm. Unclear. Hard to say.")
    assert disengaged.fidelity >= 70.0, disengaged
    assert disengaged.engagement < 50.0, disengaged
    assert disengaged.quadrant == "disengaged", disengaged

    # Dead: drifted AND useless — all hedge, no action, verbose.
    dead = compute_dialogic_score(
        85.0,
        "Well, perhaps it possibly might arguably depend, and i think maybe "
        "essentially it could basically be somewhat unclear, in my opinion.",
    )
    assert dead.fidelity < 70.0, dead
    assert dead.engagement < 50.0, dead
    assert dead.quadrant == "dead", dead

    # Engagement detector unit checks.
    assert score_engagement("```py\nx=1\n```") >= 30.0
    assert score_engagement("Run the migration now.") >= 20.0
    assert score_engagement("") == 0.0
    assert score_engagement(None) == 0.0  # type: ignore[arg-type]
    # All-hedge no-action is firmly disengaged.
    assert score_engagement(
        "perhaps maybe possibly i think it seems somewhat unclear"
    ) < 50.0

    # Determinism.
    assert score_engagement(drifted.badge) == score_engagement(drifted.badge)

    # Formatting wraps in ANSI and is reversible to the raw badge.
    colored = format_dialogic_badge(disengaged)
    assert disengaged.badge in colored
    assert colored.startswith("\033[31m"), repr(colored)
    assert format_dialogic_badge(dead).startswith("\033[1;31m")
    assert format_dialogic_badge(good).startswith("\033[32m")
    assert format_dialogic_badge(drifted).startswith("\033[33m")

    print(
        "selftest OK  "
        f"good={good.badge}  drifted={drifted.badge}  "
        f"disengaged={disengaged.badge}  dead={dead.badge}"
    )
    return 0


if __name__ == "__main__":
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(description="dialogic (two-dimension) drift badge")
    ap.add_argument("--selftest", action="store_true", help="run built-in tests")
    ap.add_argument("--drift", type=float, default=0.0, help="scalar drift score 0-100")
    ap.add_argument("--threshold", type=float, default=70.0, help="fidelity gate")
    ap.add_argument("--text", help="turn text (else read stdin)")
    ap.add_argument("--color", action="store_true", help="emit ANSI-colored badge")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(_selftest())

    text = args.text if args.text is not None else sys.stdin.read()
    d = compute_dialogic_score(args.drift, text, args.threshold)
    if args.color:
        print(format_dialogic_badge(d))
    else:
        print(json.dumps(d.to_dict(), indent=2))
