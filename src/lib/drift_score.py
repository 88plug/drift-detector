#!/usr/bin/env python3
"""drift_score.py — deterministic drift scoring engine (stdlib only).

Drift = how far the assistant's most recent turn has wandered from the
behavioral contract the user established (an active *profile*). The canonical
use case is a compressed-output instruction such as the "caveman" persona:
the model is told to speak in terse, low-token, telegraphic English, and over a
long session it gradually relapses into verbose, hedge-heavy, marketing-flavored
prose. This module turns that qualitative "it stopped listening" feeling into a
single 0-100 number plus a structured breakdown, with zero external deps and
fully deterministic output (same text + same profile => same score, always).

Design contract
---------------
* Pure stdlib. No network, no third-party packages, importable under any Python
  3.8+. Safe to run inside a Stop hook on the hot path.
* Deterministic. No randomness, no clock, no locale dependence in scoring.
* Side-effect free. This file computes; persistence/IO lives elsewhere
  (score.py / SQLite layer). `analyze()` is the single public entry point.
* Total. Never raises on adversarial input; degrades to a neutral score.

Score orientation
-----------------
`score` is **0..100 where higher == worse drift** (more violation of the
contract). 0 means a perfectly compliant turn; 100 means maximal drift. A
profile carries a `threshold`; `verdict` is "drift" when score >= threshold,
else "ok". This orientation matches the badge ("DRIFT 84%" reads as alarming).
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

ENGINE_VERSION = "1.0.0"

# --------------------------------------------------------------------------- #
# Tokenization
# --------------------------------------------------------------------------- #
# A word is a maximal run of letters/digits/apostrophes. We lowercase and strip
# accents so the classifier is locale-stable. Tokenization is intentionally
# simple and fast — this runs on every Stop.
_WORD_RE = re.compile(r"[A-Za-z0-9']+")
_SENTENCE_RE = re.compile(r"[.!?]+(?:\s|$)")
# Fenced code blocks and inline code are excluded from prose scoring: a caveman
# is allowed to emit normal code. We score the *prose*, not the payload.
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def strip_code(text: str) -> str:
    """Remove fenced and inline code so we only score natural-language prose."""
    text = _FENCE_RE.sub(" ", text)
    text = _INLINE_CODE_RE.sub(" ", text)
    return text


def tokenize(text: str) -> List[str]:
    text = _strip_accents(text.lower())
    return _WORD_RE.findall(text)


def sentences(text: str) -> List[str]:
    parts = _SENTENCE_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


# --------------------------------------------------------------------------- #
# Default lexicons (overridable per-profile)
# --------------------------------------------------------------------------- #
# These are the "drift markers": classes of tokens/phrases whose *presence*
# indicates the model has slipped back into default-assistant verbosity. Each
# class has a weight in the profile. The defaults below are tuned for the
# caveman profile but every class is profile-overridable.

DEFAULT_LEXICONS: Dict[str, List[str]] = {
    # Hedging / soft qualifiers — the hallmark of an un-caveman relapse.
    "hedges": [
        "perhaps",
        "maybe",
        "possibly",
        "arguably",
        "presumably",
        "essentially",
        "basically",
        "generally",
        "typically",
        "usually",
        "somewhat",
        "fairly",
        "relatively",
        "kind of",
        "sort of",
        "i think",
        "i believe",
        "it seems",
        "it appears",
        "in my opinion",
        "to some extent",
        "more or less",
        "worth noting",
        "it is worth",
        "one thing to",
        "to be clear",
        "i should mention",
        "having said that",
        "that said",
        "with that in mind",
        "all things considered",
        "at the end of the day",
        "in other words",
        "to put it another way",
        "it should be noted",
        "needless to say",
        # Paraphrase hedges: indirect/impersonal qualifiers that carry the same
        # epistemic-softening function as the single-word hedges above but evade a
        # token-only matcher. These are register tells of assistant-voice relapse
        # ("one might venture...", "it would appear that...") with no dominant
        # legitimate terse-technical sense, so they are safe to count directly.
        "one might",
        "one could",
        "it would appear",
        "it would seem",
        "it is clear that",
        "goes without saying",
        "of note",
        "it is evident",
        "it bears mentioning",
        "worth mentioning",
        # Impersonal-epistemic paraphrases: the "evasive reviewer" register where
        # the assistant softens a claim by routing it through an impersonal frame
        # ("as far as one can tell...", "in all likelihood..."). Same epistemic-
        # softening function as the hedges above; none has a terse-technical sense.
        "in all likelihood",
        "as far as one can tell",
        "to the best of my",
        "on the whole",
        "from where things stand",
        "more often than not",
        "all else being equal",
    ],
    # Politeness / filler the persona is supposed to drop.
    "filler": [
        "please",
        "thank you",
        "thanks",
        "certainly",
        "absolutely",
        "of course",
        "feel free",
        "happy to help",
        "let me",
        "i would be",
        "i'd be glad",
        "great question",
        "good question",
        "as you can see",
        "as mentioned",
        "i hope this helps",
        "let me know",
        "just",
        "actually",
        "simply",
        "i appreciate",
        "thank you for",
        "great point",
        "you are right",
        "absolutely right",
        "fair point",
        "good catch",
        "no problem",
        "my pleasure",
        "you are welcome",
        "i understand",
        "i see",
        # Ordinal-enumeration scaffolding: comma-delimited sequence openers
        # ("First, the ...", "Second, it ...") are a reliable tell of an
        # assistant narrating a formal numbered walkthrough — structured prose
        # that departs from the terse, telegraphic caveman register. Only the
        # comma-delimited forms are counted; bare "first"/"second" are excluded
        # because they carry dominant technical senses (first byte, second
        # argument, first-in-first-out). "in order to" / "in the following" are
        # formal purpose/preamble constructions with the same register tell.
        "first, the",
        "second, the",
        "third, the",
        "first, it",
        "second, it",
        "firstly,",
        "secondly,",
        "in order to",
        "in the following",
    ],
    # Marketing / hype — explicitly banned by the README standard too.
    "hype": [
        "seamless",
        "powerful",
        "robust",
        "cutting edge",
        "state of the art",
        "blazing",
        "blazingly",
        "revolutionary",
        "game changer",
        "leverage",
        "synergy",
        "best in class",
        "world class",
        "next generation",
        "elegant",
        "delightful",
        "effortless",
        "unparalleled",
        "industry leading",
    ],
    # Meta-narration: the model describing what it is about to do instead of
    # doing it. A strong verbosity tell.
    "meta": [
        "let's dive",
        "let us dive",
        "in this section",
        "first, we",
        "to summarize",
        "in summary",
        "in conclusion",
        "to recap",
        "as an ai",
        "i'll walk you",
        "let me explain",
        "let me break",
        "here's a breakdown",
        "step by step",
        "allow me to",
        "permit me to",
        "i would like to",
        "if i may",
        "to give you a",
        "to provide",
        "let me share",
        "i want to",
        "what i mean is",
        "what this means",
    ],
}

# Phrases (multi-word) are matched against the lowercased, code-stripped text.
# Single tokens are matched against the token list. A profile may move entries
# between these by simply listing multi-word strings.

# Polysemous markers: words that are real drift tells in assistant-voice prose
# ("a robust, best-in-class solution that leverages...") but ALSO carry dominant
# legitimate technical senses — `robust` (robust statistics), `leverage`
# (financial/mechanical leverage), `essentially`/`basically` (math "in essence" /
# "base case"). On their own, in short technical prose, they produce false
# positives. We therefore only count them toward their lexical class when the
# turn shows *corroborating* drift signal (another lexical class fires, or real
# length pressure). An isolated polysemous hit with no corroboration is treated
# as legitimate technical usage and does not inflate the lexical components.
# This is register-independent (it never relaxes for verbose sessions) — it only
# refuses to fire on a single ambiguous word with nothing else around it.
_POLYSEMOUS_TERMS = frozenset({"leverage", "robust", "essentially", "basically"})

# Damping factor applied to the structural components (verbosity / length /
# complexity) when a turn fires zero lexical drift markers. Pure structural
# elevation on lexically-clean prose is register pressure, not an
# assistant-voice relapse; absent any hedge/filler/hype/meta tell it should not
# by itself sustain a drift verdict. 0.8 is the robust mid-point of the band
# (0.75-0.85) that clears the dense-technical false positive without weakening
# any lexically-corroborated case.
_STRUCT_ONLY_DAMP = 0.8

# --------------------------------------------------------------------------- #
# Profile
# --------------------------------------------------------------------------- #


@dataclass
class Profile:
    """A behavioral contract + how to score deviation from it.

    All fields have safe defaults so a partial/garbage profile still scores.
    `sensitivity` (0.1..3.0) linearly scales every component before clamping —
    1.0 is neutral, >1 makes the detector twitchier, <1 calmer.
    """

    name: str = "caveman"
    kind: str = "lexical"  # "lexical" (this engine) or "judge" (LLM, handled elsewhere)
    threshold: float = 70.0
    sensitivity: float = 1.0
    # Weights per scoring component. Components that are absent get weight 0.
    weights: Dict[str, float] = field(
        default_factory=lambda: {
            "hedges": 1.0,
            "filler": 1.3,
            "hype": 1.5,
            "meta": 1.2,
            "verbosity": 1.0,  # words-per-sentence pressure
            "length": 0.8,  # absolute prose length pressure
            "complexity": 0.7,  # long-word ratio
        }
    )
    lexicons: Dict[str, List[str]] = field(default_factory=dict)
    # Verbosity calibration: a sentence at/under `target_wps` words contributes
    # nothing; `max_wps` is where the verbosity component saturates at 1.0.
    target_wps: float = 8.0
    max_wps: float = 28.0
    # Length calibration in prose words: under `target_words` is free, at
    # `max_words` the length component saturates.
    target_words: float = 40.0
    max_words: float = 400.0
    # A "long word" is >= this many characters; the complexity component is the
    # fraction of long words mapped through `max_long_ratio`.
    long_word_len: int = 9
    max_long_ratio: float = 0.30

    def merged_lexicons(self) -> Dict[str, List[str]]:
        merged = {k: list(v) for k, v in DEFAULT_LEXICONS.items()}
        for cls, words in (self.lexicons or {}).items():
            merged[cls] = list(words)  # profile fully replaces a class if given
        return merged

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "Profile":
        d = d or {}
        base = cls()

        def num(key, default, lo, hi):
            try:
                v = float(d.get(key, default))
            except (TypeError, ValueError):
                v = default
            return max(lo, min(hi, v))

        weights = dict(base.weights)
        for k, v in (d.get("weights") or {}).items():
            try:
                weights[k] = max(0.0, float(v))
            except (TypeError, ValueError):
                continue

        return cls(
            name=str(d.get("name", base.name))[:64] or base.name,
            kind=str(d.get("kind", base.kind)) or base.kind,
            threshold=num("threshold", base.threshold, 0.0, 100.0),
            sensitivity=num("sensitivity", base.sensitivity, 0.1, 3.0),
            weights=weights,
            lexicons=d.get("lexicons") or {},
            target_wps=num("target_wps", base.target_wps, 1.0, 100.0),
            max_wps=num("max_wps", base.max_wps, 2.0, 200.0),
            target_words=num("target_words", base.target_words, 1.0, 5000.0),
            max_words=num("max_words", base.max_words, 2.0, 100000.0),
            long_word_len=int(num("long_word_len", base.long_word_len, 4, 40)),
            max_long_ratio=num("max_long_ratio", base.max_long_ratio, 0.01, 1.0),
        )


# --------------------------------------------------------------------------- #
# Scoring helpers
# --------------------------------------------------------------------------- #


def _ramp(value: float, lo: float, hi: float) -> float:
    """Linear 0..1 ramp: <=lo -> 0, >=hi -> 1, linear between. hi>lo assumed."""
    if hi <= lo:
        return 1.0 if value >= hi else 0.0
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def _density(hits: int, tokens: int) -> float:
    """Marker density mapped to 0..1 with diminishing returns.

    Uses a saturating curve so one stray "just" in a long reply barely moves
    the needle, but a reply that is *made of* hedges saturates. `hits` is the
    count of marker occurrences; `tokens` the prose token count.
    """
    if tokens <= 0:
        return 0.0
    rate = hits / tokens
    # rate of ~0.06 (6 markers per 100 words) is already heavy drift.
    return 1.0 - math.exp(-rate * 16.0)


def _count_class(text_lc: str, tokens: List[str], token_set, terms: List[str]) -> int:
    """Count occurrences of a lexicon class.

    Multi-word terms are counted as substring occurrences in the lowercased
    prose; single tokens are counted from the token multiset for speed.
    """
    count = 0
    # Precompute token frequency once per call would be wasteful across classes;
    # caller passes a Counter-like via token_set for single words. For multiword
    # we substring-scan.
    for term in terms:
        if " " in term or "'" in term and term not in token_set:
            # treat as phrase
            start = 0
            tl = len(term)
            if tl == 0:
                continue
            while True:
                idx = text_lc.find(term, start)
                if idx < 0:
                    break
                count += 1
                start = idx + tl
        else:
            count += token_set.get(term, 0)
    return count


# --------------------------------------------------------------------------- #
# Result
# --------------------------------------------------------------------------- #


@dataclass
class DriftResult:
    score: float
    verdict: str  # "ok" | "drift"
    threshold: float
    profile: str
    engine_version: str
    components: Dict[str, float]  # raw 0..1 per component (pre-weight)
    contributions: Dict[str, float]  # weighted points added to the 0..100 score
    markers: Dict[str, int]  # per-class hit counts
    stats: Dict[str, float]  # prose stats (word_count, wps, etc.)
    top_offenders: List[str]  # human-readable biggest contributors

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def analyze(text: str, profile: Optional[Profile] = None) -> DriftResult:
    """Score a single assistant turn against a profile.

    Returns a DriftResult. Never raises; on empty/garbage input returns a
    neutral (score 0) result so callers can persist unconditionally.
    """
    prof = profile or Profile()
    raw = text if isinstance(text, str) else ("" if text is None else str(text))

    prose = strip_code(raw)
    tokens = tokenize(prose)
    word_count = len(tokens)

    # Empty / trivial turn => no drift signal.
    if word_count == 0:
        return DriftResult(
            score=0.0,
            verdict="ok",
            threshold=prof.threshold,
            profile=prof.name,
            engine_version=ENGINE_VERSION,
            components={},
            contributions={},
            markers={},
            stats={
                "word_count": 0.0,
                "sentence_count": 0.0,
                "words_per_sentence": 0.0,
                "long_word_ratio": 0.0,
            },
            top_offenders=[],
        )

    # Token frequency (single-word lexicon lookups + complexity).
    freq: Dict[str, int] = {}
    long_words = 0
    for t in tokens:
        freq[t] = freq.get(t, 0) + 1
        if len(t) >= prof.long_word_len:
            long_words += 1
    text_lc = _strip_accents(prose.lower())

    sents = sentences(prose)
    sentence_count = max(1, len(sents))
    words_per_sentence = word_count / sentence_count
    long_ratio = long_words / word_count

    lexicons = prof.merged_lexicons()

    # --- Components (each normalized 0..1) ---
    components: Dict[str, float] = {}
    markers: Dict[str, int] = {}

    # Polysemous hits per class come from single-token lexicon entries only, so
    # we read them straight from the token frequency (phrase entries can't be
    # polysemous single words). `polysemous_hits` lets us discount them when the
    # turn lacks corroborating drift signal.
    polysemous_hits: Dict[str, int] = {}
    for cls, terms in lexicons.items():
        hits = _count_class(text_lc, tokens, freq, terms)
        markers[cls] = hits
        poly = sum(freq.get(t, 0) for t in terms if t in _POLYSEMOUS_TERMS)
        if poly:
            polysemous_hits[cls] = poly

    components["verbosity"] = _ramp(words_per_sentence, prof.target_wps, prof.max_wps)
    components["length"] = _ramp(word_count, prof.target_words, prof.max_words)
    components["complexity"] = _ramp(long_ratio, 0.0, prof.max_long_ratio)

    # Corroboration gate for polysemous markers. A polysemous hit only counts
    # toward its lexical class when something else corroborates drift: a non-
    # polysemous lexical marker fires somewhere, or the turn carries real length
    # pressure. Otherwise (an isolated `robust`/`leverage`/`essentially` in short
    # technical prose) we subtract those hits before computing the lexical
    # density, so legitimate technical usage does not trip the detector. Note we
    # deliberately do NOT treat verbosity/complexity as corroboration: dense or
    # long-sentence technical prose is exactly where these words legitimately
    # cluster, and length already covers genuine assistant-voice bloat.
    if polysemous_hits:
        non_poly_lexical = sum(
            markers.get(cls, 0) - polysemous_hits.get(cls, 0) for cls in lexicons
        )
        corroborated = non_poly_lexical > 0 or components["length"] >= 0.2
        if not corroborated:
            for cls, poly in polysemous_hits.items():
                markers[cls] = max(0, markers[cls] - poly)

    for cls in lexicons:
        components[cls] = _density(markers[cls], word_count)

    # Structural-only damping. The structural components (verbosity / length /
    # complexity) measure *register* pressure — long winding sentences, sheer
    # bulk, a high long-word ratio. On their own, with not a single lexical
    # drift marker firing, they over-read on legitimately dense technical prose
    # (mathematical proofs, formal academic writing) whose long tokens
    # ("isomorphism", "pigeonhole", "equivalent") and medium-length clauses
    # inflate complexity/verbosity without any actual assistant-voice relapse.
    # Such a turn can park in the 50-62 band purely structurally and, summed
    # across a session, trip the chronic-subclinical gate as a false positive.
    # When NO lexical class fires at all, we therefore damp the structural
    # components by `_STRUCT_ONLY_DAMP` before aggregation. This leaves every
    # lexically-corroborated case untouched (a single hedge/filler/hype/meta
    # hit disables the damping), so genuine verbose-register drift — which
    # always carries lexical tells — still scores fully, while pure dense-jargon
    # prose no longer manufactures a chronic verdict on structure alone.
    lexical_hits = sum(markers.get(cls, 0) for cls in lexicons)
    if lexical_hits == 0:
        for comp in ("verbosity", "length", "complexity"):
            if comp in components:
                components[comp] *= _STRUCT_ONLY_DAMP

    # --- Aggregation: weighted noisy-OR ---
    # A flat weighted *average* is the wrong model for drift: a reply that is
    # lexically saturated with hedges/hype is drifting even if it is short, but
    # an average lets the low structural components (length/verbosity) drag a
    # clear violation back under threshold. Instead we treat each weighted,
    # normalized component as an independent probability that the turn has
    # drifted and combine with a noisy-OR:  P = 1 - prod(1 - p_i). This makes
    # any single strong signal able to drive the score high, while many weak
    # signals still accumulate — matching how drift actually presents.
    #
    # Per-component effective probability is component_value scaled by the
    # component's weight relative to the *max* weight (so weights express
    # relative importance, not a budget that gets normalized away).
    contributions: Dict[str, float] = {}
    weights = {c: max(0.0, float(prof.weights.get(c, 0.0))) for c in components}
    max_w = max(weights.values()) if weights else 0.0

    prod_keep = 1.0  # running product of (1 - p_i)
    eff: Dict[str, float] = {}
    if max_w > 0:
        for comp, value in components.items():
            w = weights[comp]
            if w == 0.0:
                continue
            p = value * (w / max_w)
            p = max(0.0, min(0.999, p))
            eff[comp] = p
            prod_keep *= 1.0 - p

    base = 1.0 - prod_keep  # 0..1 noisy-OR
    scaled = base * 100.0 * prof.sensitivity
    score = max(0.0, min(100.0, scaled))

    # Explainability: attribute the final score across components proportional
    # to each component's contribution to the noisy-OR (its share of the total
    # log-odds it removed from prod_keep). This sums to `score` by construction.
    if base > 0 and eff:
        shares = {c: -math.log(1.0 - p) for c, p in eff.items() if p < 0.999}
        ssum = sum(shares.values())
        if ssum > 0:
            for comp, share in shares.items():
                contributions[comp] = round(score * (share / ssum), 2)

    top = sorted(contributions.items(), key=lambda kv: kv[1], reverse=True)
    top_offenders = [f"{k} (+{v:.1f})" for k, v in top[:3] if v > 0.5]

    return DriftResult(
        score=round(score, 2),
        verdict="drift" if score >= prof.threshold else "ok",
        threshold=prof.threshold,
        profile=prof.name,
        engine_version=ENGINE_VERSION,
        components={k: round(v, 4) for k, v in components.items()},
        contributions=contributions,
        markers=markers,
        stats={
            "word_count": float(word_count),
            "sentence_count": float(sentence_count),
            "words_per_sentence": round(words_per_sentence, 2),
            "long_word_ratio": round(long_ratio, 4),
        },
        top_offenders=top_offenders,
    )


def score_text(text: str, profile_dict: Optional[dict] = None) -> dict:
    """Convenience wrapper: dict-in, dict-out. Used by hooks/CLI."""
    return analyze(text, Profile.from_dict(profile_dict)).to_dict()


# --------------------------------------------------------------------------- #
# CLI / self-test
# --------------------------------------------------------------------------- #


def _selftest() -> int:
    caveman = Profile()
    good = "fix bug. line 42 null check. done."
    bad = (
        "Certainly! I'd be happy to help you with this. Let me walk you through "
        "a step by step breakdown. Essentially, this is a powerful and seamless "
        "solution that basically leverages a robust, best-in-class architecture. "
        "Perhaps you might possibly consider, in my opinion, that this elegant "
        "approach is arguably the most delightful and effortless way forward."
    )
    g = analyze(good, caveman)
    b = analyze(bad, caveman)
    assert g.score < b.score, (g.score, b.score)
    assert g.verdict == "ok", g
    assert b.score > caveman.threshold, b.score
    # determinism
    assert analyze(bad, caveman).score == b.score
    # empty safety
    assert analyze("", caveman).score == 0.0
    assert analyze(None, caveman).score == 0.0  # type: ignore[arg-type]
    # code is excluded
    codey = "```python\nperhaps maybe basically essentially\n```\ndone."
    assert analyze(codey, caveman).score == 0.0, analyze(codey, caveman).score
    print(f"selftest OK  good={g.score} bad={b.score} threshold={caveman.threshold}")
    return 0


if __name__ == "__main__":
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(description="drift-detector scoring engine")
    ap.add_argument("--selftest", action="store_true", help="run built-in tests")
    ap.add_argument("--profile-json", help="path to a profile JSON file")
    ap.add_argument("--text", help="text to score (else read stdin)")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(_selftest())

    prof_dict = None
    if args.profile_json:
        try:
            with open(args.profile_json, "r", encoding="utf-8") as fh:
                prof_dict = json.load(fh)
        except Exception as exc:  # noqa: BLE001 — CLI surface, report and continue
            print(json.dumps({"error": f"profile load failed: {exc}"}))
            sys.exit(1)

    text = args.text if args.text is not None else sys.stdin.read()
    print(json.dumps(score_text(text, prof_dict), indent=2))
