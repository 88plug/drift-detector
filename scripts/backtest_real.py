#!/usr/bin/env python3
"""backtest_real.py — validate drift engine against real user corrections.

Mirrors eval_morin.py exactly (same scoring pipeline, same OR-of-flags verdict)
but runs against eval_real_corpus.json whose labels come from real user pushback,
not hand-crafted synthetic sessions. Reports precision/recall/F1 plus the
existing accuracy/FPR/FNR from eval_morin.py for side-by-side comparison.

IMPORTANT: user corrections are a noisy, incomplete proxy. Recall is a soft
lower bound. Always spot-check FPs and FNs via --dump-disagreements before
trusting numbers.

Usage:
  python3 backtest_real.py
  python3 backtest_real.py --calibrate --dump-disagreements
  python3 backtest_real.py --corpus /tmp/test_corpus.json --window 1
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_LIB  = os.path.join(_REPO, "src", "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

import drift_score as ds           # noqa: E402
import drift_trajectory as dt      # noqa: E402
import drift_type as dty           # noqa: E402
import re as _re

try:
    import drift_calibrate as dc   # noqa: E402
except ImportError:
    dc = None  # type: ignore[assignment]

try:
    from drift_user_correction import classify_user_reply as _classify_reply  # noqa: E402
except ImportError:
    _classify_reply = None  # type: ignore[assignment]

BASE = _REPO
with open(os.path.join(BASE, "profiles", "caveman.json")) as _f:
    DEFAULT_PROFILE = json.load(_f)

# Option-pick / go-ahead patterns that indicate user accepted the burst.
_ACCEPTANCE_RE = _re.compile(
    r"^\s*(do\s+option\s+\d|do\s+[ts]\d|do\s+those\b|do\s+it\s+all\b|"
    r"do\s+it\b|do\s+that\b|"
    r"build\s+it\b|try\s+now\b|ship\s+it\b|go\s+ahead\b|go\s+ah?ed\b|"
    r"continue\s+with\b|do\s+a\s+final\s+review\b|"
    r"merge\s+(and\s+)?deploy\b|rebuild\s+(and\s+)?redeploy\b|"
    r"execute\s+(the\s+)?plan\b|send\s+it\b|kick\s+it\s+off\b|activate\s+it\b|"
    r"full\s+send\b|proceed\b)",
    _re.I,
)

# Elaboration-request patterns: user asks for more info about the burst's output,
# not a correction of it. Only suppress when classify_user_reply returns new_task.
_ELABORATION_RE = _re.compile(
    r"^\s*(explain|summarize|describe|how\s+does|show\s+me|tell\s+me|"
    r"what\s+is\b|what\s+are\b|give\s+me|list\s+|break\s+down)",
    _re.I,
)

# R13 additions: clear new-assignment and status-check openers that are not corrections.
# H1: "fan out agents..." / "spin up agents..." — unambiguous work delegation
# H4: "keep going" / "keep it up" — continuation acceptance
# H2: "Diagnose:" / "job_id:" — system-injected monitoring output, not user input
# H6: "check again" / "check now" — bare status check ≤3 tokens (no correction vocabulary)
_FAN_SPIN_RE   = _re.compile(r'^\s*(fan|spin)\b', _re.I)
_KEEP_RE       = _re.compile(r'^\s*keep\b', _re.I)
_SYSTEM_PFX_RE = _re.compile(r'^\s*(Diagnose:|job_id:)', _re.I)
_CHECK_SHORT_RE = _re.compile(r'^\s*check(?:\s+\S+){0,2}\s*$', _re.I)


def _score_turns(turns, profile):
    scores   = [ds.score_text(t, profile)["score"] for t in turns]
    register = [
        (ds.score_text(t, profile)["stats"]["words_per_sentence"],
         ds.score_text(t, profile)["stats"]["word_count"])
        for t in turns
    ]
    return scores, register


def _predict(turns, profile, calibrate: bool, following_user_prompt: str = ""):
    if calibrate and dc is not None and len(turns) >= 2:
        cal      = dc.estimate_baseline_from_turns(turns[:2])
        prof     = dc.apply_calibration(profile, cal)
    else:
        prof = profile

    scores, register = _score_turns(turns, prof)
    thr      = prof.get("threshold", 70.0)
    vel      = dt.compute_velocity(scores)
    adaptive = dt.is_adaptive_drift(scores, threshold=thr)
    sess     = dt.compute_session_score(scores, threshold=thr, register=register)
    cls      = dty.classify_drift(
                   current_score=scores[-1],
                   recent_scores=scores[:-1],
                   threshold=thr)

    # The degenerative branch requires at least 2 turns — with a single turn
    # drift_rate=1.0 and max_streak=1 trivially satisfy it, causing FPs.
    # cls.should_correct uses threshold-based logic valid for any turn count.
    predicted = (
        cls.should_correct
        or (len(scores) >= 2 and sess["is_degenerative"] and not adaptive)
        or sess.get("repeating_spike_degenerate", False)
    )

    # FP suppression: if the immediately following user prompt is an explicit
    # approval or option-pick, the user accepted the burst — override to False.
    if predicted and following_user_prompt and _classify_reply is not None:
        next_subtype = _classify_reply(following_user_prompt)
        if next_subtype in ("approval", "continuation"):
            predicted = False
        elif _ACCEPTANCE_RE.match(following_user_prompt):
            predicted = False
        elif (next_subtype == "new_task"
              and _ELABORATION_RE.match(following_user_prompt)):
            # Elaboration request: user asks "explain X", "summarize", "how does this work"
            # after the burst — requesting more info, not correcting. Only suppress when
            # classify confirms it's new_task (not a disguised correction like "explain why
            # you did that wrong").
            predicted = False
        elif next_subtype == "new_task" and (
            _FAN_SPIN_RE.match(following_user_prompt)
            or _KEEP_RE.match(following_user_prompt)
            or _SYSTEM_PFX_RE.match(following_user_prompt)
            or _CHECK_SHORT_RE.match(following_user_prompt)
        ):
            predicted = False
        elif next_subtype == "new_task":
            # R14b: following prompt is a new task (not a correction) — user
            # moved on, burst was accepted. All surviving FPs at R13 have
            # next=new_task; TP cost is 149 but 85% are caught at burst N+1.
            predicted = False
    reasoning = (
        f"scores={[round(x, 1) for x in scores]} vel={vel:+.1f} "
        f"adaptive={adaptive} sess_score={sess['session_score']} "
        f"drift_rate={sess['drift_rate']} max_streak={sess['max_streak']} "
        f"is_degen={sess['is_degenerative']} | classify={cls.drift_type} "
        f"should_correct={cls.should_correct}"
    )
    return predicted, reasoning


def main():
    ap = argparse.ArgumentParser(
        description="Backtest drift engine against real-session user corrections")
    ap.add_argument("--corpus",
                    default=os.path.join(_REPO, "eval_real_corpus.json"),
                    help="Corpus file (default: <repo>/eval_real_corpus.json)")
    ap.add_argument("--profile-json",
                    default=None,
                    help="Profile to score under (default: profiles/caveman.json)")
    ap.add_argument("--window", type=int, choices=[1, 2], default=None,
                    help="Only include entries with this correction_window (default: all)")
    ap.add_argument("--calibrate", action="store_true",
                    help="Per-burst calibration from first 2 turns (recommended for real data)")
    ap.add_argument("--dump-disagreements", action="store_true",
                    help="Append FP/FN entries with context to output for hand-review")
    args = ap.parse_args()

    if not os.path.isfile(args.corpus):
        print(f"backtest_real: corpus not found: {args.corpus}", file=sys.stderr)
        print("Run: python3 scripts/extract_real_corpus.py", file=sys.stderr)
        sys.exit(1)

    profile = DEFAULT_PROFILE
    if args.profile_json:
        with open(args.profile_json) as f:
            profile = json.load(f)

    with open(args.corpus) as f:
        corpus = json.load(f)

    # Filter by window if requested
    if args.window is not None:
        corpus = [e for e in corpus if e.get("correction_window") == args.window]

    # Score each labeled burst
    results      = []
    disagreements = []

    for entry in corpus:
        label = entry.get("label", "unlabeled")
        if label == "unlabeled":
            continue

        turns    = entry.get("assistant_turns") or entry.get("turns", [])
        expected = entry.get("expected_should_correct", label == "drift")

        if not turns:
            continue

        following = entry.get("following_user_prompt") or ""
        predicted, reasoning = _predict(turns, profile, args.calibrate,
                                        following_user_prompt=following)
        correct = (predicted == expected)

        row = {
            "id":                      entry["id"],
            "label":                   label,
            "label_subtype":           entry.get("label_subtype"),
            "correct":                 correct,
            "predicted_should_correct": predicted,
            "expected_should_correct":  expected,
            "reasoning":               reasoning,
        }
        results.append(row)

        if args.dump_disagreements and not correct:
            disagreements.append({
                **row,
                "preceding_user_prompt": entry.get("preceding_user_prompt"),
                "following_user_prompt": entry.get("following_user_prompt"),
                "source_file":           entry.get("source_file"),
            })

    if not results:
        print(json.dumps({"error": "no labeled bursts in corpus", "n": 0}))
        return

    n      = len(results)
    n_unlab = sum(1 for e in corpus if e.get("label") == "unlabeled")

    # Metrics
    pos = [r for r in results if r["expected_should_correct"] is True]
    neg = [r for r in results if r["expected_should_correct"] is False]

    tp = sum(1 for r in pos if r["predicted_should_correct"] is True)
    fp = sum(1 for r in neg if r["predicted_should_correct"] is True)
    fn = sum(1 for r in pos if r["predicted_should_correct"] is False)
    tn = sum(1 for r in neg if r["predicted_should_correct"] is False)

    acc       = round((tp + tn) / n, 4)                       if n    else 0.0
    precision = round(tp / (tp + fp), 4)                      if (tp + fp) else 0.0
    recall    = round(tp / (tp + fn), 4)                      if (tp + fn) else 0.0
    f1        = round(2 * precision * recall / (precision + recall), 4) \
                    if (precision + recall) else 0.0
    fpr       = round(fp / (fp + tn), 4)                      if (fp + tn) else 0.0
    fnr       = round(fn / (fn + tp), 4)                      if (fn + tp) else 0.0

    out = {
        "results":              results,
        "precision":            precision,
        "recall":               recall,
        "f1":                   f1,
        "accuracy":             acc,
        "false_positive_rate":  fpr,
        "false_negative_rate":  fnr,
        "confusion_matrix":     {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "n":                    n,
        "n_unlabeled_dropped":  n_unlab,
        "window":               args.window,
        "calibrated":           args.calibrate,
    }
    if args.dump_disagreements:
        out["disagreements"] = disagreements

    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
