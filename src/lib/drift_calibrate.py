#!/usr/bin/env python3
"""drift_calibrate.py — self-calibrating baseline for drift scoring (stdlib only).

Morin improvement #2: the reference point for "normal" should be *estimated from
the session's own early in-contract turns*, not frozen into hardcoded constants.

The hardcoded `Profile.target_wps`/`target_words` defaults assume one register
(the caveman persona). But a session establishes its *own* register in its first
few clean turns — terse for one task, denser for another. If the baseline is
fixed, the detector either nags a naturally-dense-but-stable session or sleeps
through drift in a naturally-terse one. So we read the session's first N turns —
sampled *before* drift could have corrupted them — and derive the verbosity
reference from their median.

This closes the slow recursive loop Morin warns about: the baseline itself drifts
*with* the session it is measuring, so "normal" tracks the contract the session
actually established rather than a constant guessed in advance.

Design contract
---------------
* Pure stdlib. No network, no third-party packages, importable under Python 3.8+.
* Side-effect free and total: never raises on garbage input; degrades to None
  (caller falls back to Profile defaults).
* Only the verbosity/length reference (target_wps/max_wps/target_words/max_words)
  is calibrated. Lexical drift markers (hedges/hype/...) are register-independent
  and stay hardcoded — a "blazing-fast synergy" is drift no matter the session.
* Prose only: code is stripped before measuring, reusing drift_score.strip_code
  so the calibration and the scorer agree on what counts as words.
"""

from __future__ import annotations

import json
import os
from statistics import median
from typing import List, Optional

try:  # normal import when run as part of the package
    from .drift_score import strip_code, tokenize, sentences
except ImportError:  # direct execution (python drift_calibrate.py)
    from drift_score import strip_code, tokenize, sentences


# --------------------------------------------------------------------------- #
# Calibration constants — multipliers applied to the session's own median.
# These are headroom factors, NOT register assumptions: they say "allow this
# much above what the session already does" rather than "this many words".
# --------------------------------------------------------------------------- #
_WPS_TARGET_MULT = 1.3   # 30% headroom above established words-per-sentence
_WPS_MAX_MULT = 3.0      # verbosity saturates at 3x the target
_WORDS_TARGET_MULT = 1.5  # 50% headroom above established turn length
_WORDS_MAX_MULT = 4.0     # length saturates at 4x the target

# Clamps — keep calibration sane even on a pathological early sample. These
# bracket the same ranges Profile.from_dict accepts.
_CLAMP = {
    "target_wps": (4.0, 20.0),
    "max_wps": (10.0, 60.0),
    "target_words": (10.0, 200.0),
    "max_words": (50.0, 1000.0),
}


def _clamp(value: float, key: str) -> float:
    lo, hi = _CLAMP[key]
    return max(lo, min(hi, value))


def _turn_stats(turn: str) -> Optional[tuple]:
    """Return (words_per_sentence, word_count) for one turn, or None if empty.

    Code is stripped first so we measure prose the same way the scorer does.
    """
    if not isinstance(turn, str):
        return None
    prose = strip_code(turn)
    tokens = tokenize(prose)
    word_count = len(tokens)
    if word_count == 0:
        return None
    sentence_count = max(1, len(sentences(prose)))
    return word_count / sentence_count, word_count


def estimate_baseline_from_turns(turns: List[str], n_sample: int = 5) -> Optional[dict]:
    """Estimate the session's established register from its first N clean turns.

    Uses only the first ``n_sample`` turns (before drift could corrupt them).
    Returns ``{target_wps, max_wps, target_words, max_words}`` to override the
    Profile defaults, or ``None`` when there is nothing usable to measure (the
    caller should then keep the Profile defaults).
    """
    if not turns:
        return None

    sample = turns[: max(0, int(n_sample))]
    stats = [s for s in (_turn_stats(t) for t in sample) if s is not None]
    if not stats:
        return None

    median_wps = median(wps for wps, _ in stats)
    median_words = median(wc for _, wc in stats)

    target_wps = _clamp(median_wps * _WPS_TARGET_MULT, "target_wps")
    max_wps = _clamp(target_wps * _WPS_MAX_MULT, "max_wps")
    target_words = _clamp(median_words * _WORDS_TARGET_MULT, "target_words")
    max_words = _clamp(target_words * _WORDS_MAX_MULT, "max_words")

    return {
        "target_wps": round(target_wps, 2),
        "max_wps": round(max_wps, 2),
        "target_words": round(target_words, 2),
        "max_words": round(max_words, 2),
    }


def apply_calibration(profile_dict: dict, calibration: Optional[dict]) -> dict:
    """Return a new profile dict with calibrated verbosity parameters.

    Does NOT mutate the input. Only overrides
    target_wps/max_wps/target_words/max_words. If ``calibration`` is None the
    input is returned unchanged (a fresh copy).
    """
    out = dict(profile_dict or {})
    if not calibration:
        return out
    for key in ("target_wps", "max_wps", "target_words", "max_words"):
        if key in calibration:
            out[key] = calibration[key]
    return out


def _extract_assistant_text(obj) -> Optional[str]:
    """Pull assistant prose out of one parsed JSONL record, or None.

    Tolerates two shapes:
      * flat:   {"role": "assistant", "content": ...}
      * nested: {"message": {"role": "assistant", "content": ...}}
    ``content`` may be a string or a list of content blocks (Anthropic-style),
    in which case the text of all ``text`` blocks is concatenated.
    """
    if not isinstance(obj, dict):
        return None

    msg = obj.get("message") if isinstance(obj.get("message"), dict) else obj
    role = msg.get("role") or obj.get("role")
    if role != "assistant":
        return None

    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") in (None, "text") and isinstance(
                    block.get("text"), str
                ):
                    parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts) if parts else None
    return None


def calibrate_from_transcript(
    transcript_path: str, n_sample: int = 5
) -> Optional[dict]:
    """Read first ``n_sample`` assistant turns from a JSONL transcript.

    Returns a calibration dict (see :func:`estimate_baseline_from_turns`) or
    ``None`` if the transcript is missing, unreadable, or has no usable turns.
    Stops reading once ``n_sample`` assistant turns are collected so a huge
    transcript is cheap to calibrate from.
    """
    if not transcript_path or not os.path.isfile(transcript_path):
        return None

    want = max(0, int(n_sample))
    turns: List[str] = []
    try:
        with open(transcript_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (ValueError, TypeError):
                    continue
                text = _extract_assistant_text(obj)
                if text is not None:
                    turns.append(text)
                    if len(turns) >= want:
                        break
    except OSError:
        return None

    return estimate_baseline_from_turns(turns, n_sample=n_sample)


# --------------------------------------------------------------------------- #
# CLI / self-test
# --------------------------------------------------------------------------- #


def _selftest() -> int:
    import tempfile

    # Empty / garbage inputs fall back to None.
    assert estimate_baseline_from_turns([]) is None
    assert estimate_baseline_from_turns(["", "   ", "\n"]) is None
    assert estimate_baseline_from_turns(None) is None  # type: ignore[arg-type]

    # A terse session calibrates to a tight (clamped-low) baseline.
    terse = ["fix bug.", "line 42 null check.", "done.", "ran tests. pass.",
             "shipped it."]
    cal_terse = estimate_baseline_from_turns(terse)
    assert cal_terse is not None
    # All four keys present.
    assert set(cal_terse) == {"target_wps", "max_wps", "target_words", "max_words"}
    # Clamps respected.
    assert 4.0 <= cal_terse["target_wps"] <= 20.0, cal_terse
    assert 10.0 <= cal_terse["max_wps"] <= 60.0, cal_terse
    assert 10.0 <= cal_terse["target_words"] <= 200.0, cal_terse
    assert 50.0 <= cal_terse["max_words"] <= 1000.0, cal_terse
    # Ordering invariants from the multipliers.
    assert cal_terse["max_wps"] >= cal_terse["target_wps"]
    assert cal_terse["max_words"] >= cal_terse["target_words"]

    # A verbose session calibrates to a looser baseline than a terse one.
    verbose_turn = (
        "Certainly, I would be delighted to walk you through this in considerable "
        "detail, because there are many interlocking considerations that each "
        "deserve a thorough and carefully reasoned explanation before we proceed."
    )
    verbose = [verbose_turn] * 5
    cal_verbose = estimate_baseline_from_turns(verbose)
    assert cal_verbose is not None
    assert cal_verbose["target_words"] >= cal_terse["target_words"], (
        cal_verbose, cal_terse
    )

    # Determinism.
    assert estimate_baseline_from_turns(terse) == cal_terse

    # Only the first n_sample turns are used: a drifted tail must not move it.
    polluted = terse + [verbose_turn] * 50
    assert estimate_baseline_from_turns(polluted, n_sample=5) == cal_terse

    # apply_calibration does not mutate and overrides only the four keys.
    prof = {"name": "caveman", "threshold": 70.0, "target_wps": 8.0,
            "max_wps": 28.0, "target_words": 40.0, "max_words": 400.0,
            "sensitivity": 1.0}
    snapshot = dict(prof)
    merged = apply_calibration(prof, cal_terse)
    assert prof == snapshot, "input was mutated"
    assert merged is not prof
    assert merged["target_wps"] == cal_terse["target_wps"]
    assert merged["name"] == "caveman"  # untouched
    assert merged["threshold"] == 70.0  # untouched
    # None calibration returns an unchanged (copied) dict.
    passthru = apply_calibration(prof, None)
    assert passthru == prof and passthru is not prof

    # Transcript: missing path -> None.
    assert calibrate_from_transcript("/nonexistent/path.jsonl") is None
    assert calibrate_from_transcript("") is None

    # Transcript: both flat and nested shapes, content as str and as blocks.
    lines = [
        {"role": "user", "content": "do the thing"},
        {"role": "assistant", "content": "fix bug."},
        {"message": {"role": "assistant", "content": "line 42 null check."}},
        {"message": {"role": "assistant", "content": [
            {"type": "text", "text": "done."}]}},
        {"role": "assistant", "content": "ran tests. pass."},
        {"role": "assistant", "content": "shipped it."},
        {"role": "assistant", "content": "later verbose drift " * 30},
    ]
    with tempfile.NamedTemporaryFile(
        "w", suffix=".jsonl", delete=False, encoding="utf-8"
    ) as fh:
        for rec in lines:
            fh.write(json.dumps(rec) + "\n")
        tpath = fh.name
    try:
        cal_tx = calibrate_from_transcript(tpath, n_sample=5)
        assert cal_tx is not None
        # Same five terse assistant turns as the list-based test -> same result.
        assert cal_tx == cal_terse, (cal_tx, cal_terse)
        # A transcript with no assistant turns -> None.
        with tempfile.NamedTemporaryFile(
            "w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as fh2:
            fh2.write(json.dumps({"role": "user", "content": "hi"}) + "\n")
            fh2.write("not json at all\n")
            upath = fh2.name
        try:
            assert calibrate_from_transcript(upath) is None
        finally:
            os.unlink(upath)
    finally:
        os.unlink(tpath)

    print(
        "selftest OK  terse={} verbose_target_words={}".format(
            cal_terse, cal_verbose["target_words"]
        )
    )
    return 0


if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser(
        description="drift-detector self-calibrating baseline"
    )
    ap.add_argument("--selftest", action="store_true", help="run built-in tests")
    ap.add_argument("--transcript", help="JSONL transcript path to calibrate from")
    ap.add_argument("--n-sample", type=int, default=5, help="early turns to sample")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(_selftest())

    if args.transcript:
        cal = calibrate_from_transcript(args.transcript, n_sample=args.n_sample)
        print(json.dumps(cal, indent=2))
        sys.exit(0)

    ap.print_help()
    sys.exit(0)
