#!/usr/bin/env python3
import json, sys, os, copy
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "lib"))
import drift_score as ds
import drift_trajectory as dt
import drift_type as dty
import drift_calibrate as dc

BASE = os.path.join(os.path.dirname(__file__), "..")
CORPUS = os.path.join(BASE, "eval_session_corpus.json")

# Load default caveman profile dict
with open(os.path.join(BASE, "profiles", "caveman.json")) as f:
    DEFAULT_PROFILE = json.load(f)

def score_turn(text, profile_dict=None):
    pd = profile_dict if profile_dict is not None else DEFAULT_PROFILE
    res = ds.score_text(text, pd)
    return res["score"]


def turn_register(text, profile_dict=None):
    """Return (words_per_sentence, word_count) for a turn — the register metadata
    the short-session early-warning gate in compute_session_score consumes."""
    pd = profile_dict if profile_dict is not None else DEFAULT_PROFILE
    st = ds.score_text(text, pd)["stats"]
    return (st["words_per_sentence"], st["word_count"])

sessions = json.load(open(CORPUS))
results = []
for s in sessions:
    sid = s["id"]
    turns = s["turns"]
    is_cal = sid.startswith("cal-")
    is_cal2 = sid.startswith("cal2_")

    if is_cal2:
        # cal2 sessions: derive baseline from first 2 turns, apply calibration to
        # every turn, THEN run the full trajectory/classify pipeline on the
        # calibrated scores (these sessions probe calibration *and* trajectory).
        cal = dc.estimate_baseline_from_turns(turns[:2])
        prof_dict = dc.apply_calibration(DEFAULT_PROFILE, cal)
        scores = [score_turn(t, prof_dict) for t in turns]
        register = [turn_register(t, prof_dict) for t in turns]
        thr = prof_dict.get("threshold", 70.0)
        vel = dt.compute_velocity(scores)
        adaptive = dt.is_adaptive_drift(scores, threshold=thr)
        sess = dt.compute_session_score(scores, threshold=thr, register=register)
        cls = dty.classify_drift(current_score=scores[-1], recent_scores=scores[:-1], threshold=thr)
        predicted_should_correct = (
            cls.should_correct
            or (sess["is_degenerative"] and not adaptive)
        )
        reasoning = (f"CAL2 baseline={cal} | calibrated scores={[round(x,1) for x in scores]} "
                     f"vel={vel:+.1f} adaptive={adaptive} drift_rate={sess['drift_rate']} "
                     f"max_streak={sess['max_streak']} is_degen={sess['is_degenerative']} "
                     f"classify={cls.drift_type} should_correct={cls.should_correct}")
    elif is_cal:
        # calibration sessions: derive baseline from first 2 turns, apply, score last turn
        cal = dc.estimate_baseline_from_turns(turns[:2])
        prof_dict = dc.apply_calibration(DEFAULT_PROFILE, cal)
        scores = [score_turn(t, prof_dict) for t in turns]
        last = scores[-1]
        # would it fire on last turn? drift if last >= threshold
        thr = prof_dict.get("threshold", 70.0)
        predicted_should_correct = last >= thr
        reasoning = (f"CAL baseline={cal} | calibrated scores={[round(x,1) for x in scores]} "
                     f"| last={last:.1f} thr={thr} fire={predicted_should_correct}")
    else:
        scores = [score_turn(t) for t in turns]
        register = [turn_register(t) for t in turns]
        vel = dt.compute_velocity(scores)
        adaptive = dt.is_adaptive_drift(scores)
        sess = dt.compute_session_score(scores, register=register)
        cls = dty.classify_drift(current_score=scores[-1], recent_scores=scores[:-1])
        # Fix wiring gap from MORIN_EVAL_REPORT §4:
        # classify_drift gates on final-turn score alone (Rule 1 short-circuits
        # on scores[-1] < threshold). Use the session-level is_degenerative flag
        # from compute_session_score so tapering degenerative runs (max_streak>=3
        # or drift_rate>=0.5) are correctly caught even when the last turn dips.
        #
        # repeating_spike_degenerate bypasses the `and not adaptive` gate: an
        # oscillating relapse (3+ threshold crossings over a 6+ turn session)
        # reads as adaptive turn-by-turn (each spike self-corrects) but is a
        # degenerative cycle at the session level. OR it in unconditionally.
        predicted_should_correct = (
            cls.should_correct
            or (sess["is_degenerative"] and not adaptive)
            or sess.get("repeating_spike_degenerate", False)
        )
        reasoning = (f"scores={[round(x,1) for x in scores]} vel={vel:+.1f} "
                     f"adaptive={adaptive} sess_score={sess['session_score']} "
                     f"drift_rate={sess['drift_rate']} max_streak={sess['max_streak']} "
                     f"is_degen={sess['is_degenerative']} | classify={cls.drift_type} "
                     f"should_correct={cls.should_correct}")

    expected = s["expected_should_correct"]
    correct = (predicted_should_correct == expected)
    results.append({
        "id": sid,
        "label": s.get("label"),
        "correct": correct,
        "predicted_should_correct": predicted_should_correct,
        "expected_should_correct": expected,
        "old_engine_correct": s.get("old_engine_correct"),
        "reasoning": reasoning,
    })

n = len(results)
acc = sum(1 for r in results if r["correct"]) / n
# FP: predicted correct but expected not (flagged clean as drift)
# expected_should_correct == False is the "clean/should not correct" class
neg = [r for r in results if r["expected_should_correct"] is False]
pos = [r for r in results if r["expected_should_correct"] is True]
fp = sum(1 for r in neg if r["predicted_should_correct"] is True) / len(neg) if neg else 0.0
fn = sum(1 for r in pos if r["predicted_should_correct"] is False) / len(pos) if pos else 0.0

print(json.dumps({
    "results": results,
    "accuracy": round(acc,4),
    "false_positive_rate": round(fp,4),
    "false_negative_rate": round(fn,4),
    "n": n,
}, indent=2))
