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
  python3 backtest_real.py --lr          # R18: LR classifier (5-fold CV)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any as _Any

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_LIB = os.path.join(_REPO, "src", "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

import drift_score as ds  # noqa: E402
import drift_trajectory as dt  # noqa: E402
import drift_type as dty  # noqa: E402
import re as _re  # noqa: E402

try:
    import drift_calibrate as dc  # noqa: E402
except ImportError:
    dc = None  # type: ignore[assignment]

try:
    from drift_user_correction import classify_user_reply as _classify_reply  # noqa: E402
except ImportError:
    _classify_reply = None  # type: ignore[assignment]

# Optional ML stack for --lr / R19 paths. Stubs keep pyright happy when absent.
_np: _Any = None
_LR: _Any = None
_Scaler: _Any = None
_SKF: _Any = None
_CVP: _Any = None
_CM: _Any = None
_ETC: _Any = None
_SKLEARN = False
try:
    import numpy as _np
    from sklearn.linear_model import LogisticRegression as _LR
    from sklearn.preprocessing import StandardScaler as _Scaler
    from sklearn.model_selection import (  # noqa: F401
        StratifiedKFold as _SKF,
        cross_val_predict as _CVP,
    )
    from sklearn.metrics import confusion_matrix as _CM  # noqa: F401
    from sklearn.ensemble import ExtraTreesClassifier as _ETC

    _SKLEARN = True
except ImportError:
    pass

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
_FAN_SPIN_RE = _re.compile(r"^\s*(fan|spin)\b", _re.I)
_KEEP_RE = _re.compile(r"^\s*keep\b", _re.I)
_SYSTEM_PFX_RE = _re.compile(r"^\s*(Diagnose:|job_id:)", _re.I)
_CHECK_SHORT_RE = _re.compile(r"^\s*check(?:\s+\S+){0,2}\s*$", _re.I)


def _correction_rate_k(sess_bursts_by_session: dict, entry: dict, k: int = 3) -> float:
    """Fraction of the k most-recent prior bursts whose following_user_prompt was a correction."""
    if _classify_reply is None:
        return 0.0
    s = entry.get("session_id", "")
    bidx = entry.get("burst_index", -1)
    priors = sorted(
        [(bi, e) for bi, e in sess_bursts_by_session.get(s, []) if bi < bidx],
        reverse=True,
    )[:k]
    if not priors:
        return 0.0
    corr_count = sum(
        1
        for _, e in priors
        if _classify_reply(e.get("following_user_prompt") or "")
        not in ("new_task", "approval", "continuation")
    )
    return corr_count / len(priors)


def _score_turns(turns, profile):
    scores = [ds.score_text(t, profile)["score"] for t in turns]
    register = [
        (
            ds.score_text(t, profile)["stats"]["words_per_sentence"],
            ds.score_text(t, profile)["stats"]["word_count"],
        )
        for t in turns
    ]
    return scores, register


def _predict(
    turns,
    profile,
    calibrate: bool,
    following_user_prompt: str = "",
    session_correction_rate: float = 0.0,
):
    if calibrate and dc is not None and len(turns) >= 2:
        cal = dc.estimate_baseline_from_turns(turns[:2])
        prof = dc.apply_calibration(profile, cal)
    else:
        prof = profile

    scores, register = _score_turns(turns, prof)
    thr = prof.get("threshold", 70.0)
    vel = dt.compute_velocity(scores)
    adaptive = dt.is_adaptive_drift(scores, threshold=thr)
    sess = dt.compute_session_score(scores, threshold=thr, register=register)
    cls = dty.classify_drift(
        current_score=scores[-1], recent_scores=scores[:-1], threshold=thr
    )

    # The degenerative branch requires at least 2 turns — with a single turn
    # drift_rate=1.0 and max_streak=1 trivially satisfy it, causing FPs.
    # cls.should_correct uses threshold-based logic valid for any turn count.
    # R16: peak-score gate — if any turn in the burst reached threshold, the
    # burst drifted regardless of whether the last turn happened to clean up
    # (tail-escape recovery). Isolated-spike protection is handled downstream
    # by the approval/new_task/correction-rate suppression chain.
    predicted = (
        cls.should_correct
        or (len(scores) >= 2 and sess["is_degenerative"] and not adaptive)
        or sess.get("repeating_spike_degenerate", False)
        or max(scores) >= thr
    )

    # FP suppression: if the immediately following user prompt is an explicit
    # approval or option-pick, the user accepted the burst — override to False.
    if predicted and following_user_prompt and _classify_reply is not None:
        next_subtype = _classify_reply(following_user_prompt)
        if next_subtype in ("approval", "continuation"):
            predicted = False
        elif _ACCEPTANCE_RE.match(following_user_prompt):
            predicted = False
        elif next_subtype == "new_task" and _ELABORATION_RE.match(
            following_user_prompt
        ):
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
            # R15/R17: fire only when session correction rate is PARTIAL (strictly
            # between 0.50 and 1.0). When rate ≤ 0.50 the user genuinely moved on;
            # when rate = 1.0 (ALL K prior bursts were corrections) the user is
            # exhausted and the "new task" is also a genuine task-switch. The 0.50<
            # rate <1.0 band is where partial correction history signals that this
            # new task may mask continued drift acceptance.
            if not (session_correction_rate > 0.50 and session_correction_rate < 1.0):
                predicted = False
    reasoning = (
        f"scores={[round(x, 1) for x in scores]} vel={vel:+.1f} "
        f"adaptive={adaptive} sess_score={sess['session_score']} "
        f"drift_rate={sess['drift_rate']} max_streak={sess['max_streak']} "
        f"is_degen={sess['is_degenerative']} | classify={cls.drift_type} "
        f"should_correct={cls.should_correct}"
    )
    return predicted, reasoning


# ── R18: LR classifier ───────────────────────────────────────────────────────

_LR_STOP = frozenset(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "in",
        "on",
        "to",
        "of",
        "for",
        "with",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "it",
        "this",
        "that",
        "i",
        "you",
        "do",
        "did",
        "have",
        "has",
        "at",
        "by",
        "from",
        "as",
        "can",
        "will",
        "would",
        "its",
        "not",
        "all",
        "get",
        "set",
        "if",
        "so",
        "my",
        "we",
        "me",
        "no",
        "he",
        "she",
        "they",
        "them",
        "their",
        "our",
        "us",
        "up",
        "out",
        "an",
        "am",
        "re",
    ]
)

import re as _re2  # noqa: E402
import statistics as _stat  # noqa: E402
import collections as _coll  # noqa: E402


def _lr_features(entry: dict, profile: dict, sess_bursts: dict) -> list:
    """22 inference-safe features for the R18 LR classifier."""
    import drift_score as _ds2

    turns = entry.get("assistant_turns") or entry.get("turns", [])
    if not turns:
        return [0.0] * 22
    thr_ = profile.get("threshold", 50.0)
    scores = [_ds2.score_text(t, profile)["score"] for t in turns]
    max_s = max(scores)
    following = entry.get("following_user_prompt") or ""
    ns = _classify_reply(following) if _classify_reply else "new_task"

    rate_k3 = _correction_rate_k(sess_bursts, entry, k=3)
    rate_k2 = _correction_rate_k(sess_bursts, entry, k=2)
    rate_k1 = _correction_rate_k(sess_bursts, entry, k=1)
    bidx = entry.get("burst_index", -1)

    burst_words = {
        w
        for w in _re2.findall(r"\b\w{3,}\b", " ".join(turns).lower())
        if w not in _LR_STOP
    }
    follow_words = {
        w for w in _re2.findall(r"\b\w{3,}\b", following.lower()) if w not in _LR_STOP
    }
    jaccard = len(burst_words & follow_words) / max(len(burst_words | follow_words), 1)

    comp = _coll.defaultdict(float)
    import drift_score as _ds3

    for t in turns:
        s_res = _ds3.score_text(t, profile)
        for dim in (
            "hype",
            "meta",
            "verbosity",
            "hedges",
            "filler",
            "complexity",
            "length",
        ):
            comp[dim] += s_res["stats"].get(f"{dim}_count", 0) * profile["weights"].get(
                dim, 1.0
            )
    for k in comp:
        comp[k] /= len(turns)

    score_var = _stat.variance(scores) if len(scores) > 1 else 0.0
    return [
        max_s,  # 0
        max_s / thr_,  # 1
        len(scores),  # 2
        score_var,  # 3
        max(scores) - min(scores),  # 4
        scores[-1],  # 5
        sum(1 for s in scores if s >= thr_) / len(scores),  # 6
        rate_k1,  # 7
        rate_k2,  # 8
        rate_k3,  # 9
        min(bidx, 50),  # 10
        jaccard,  # 11
        len(following.split()),  # 12
        int(ns == "new_task"),  # 13
        int(ns in ("approval", "continuation")),  # 14
        comp["hype"],  # 15
        comp["meta"],  # 16
        comp["verbosity"],  # 17
        comp["hedges"],  # 18
        comp["filler"],  # 19
        comp["complexity"],  # 20
        comp["length"],  # 21
    ]


def _run_lr_backtest(corpus: list, profile: dict) -> dict:
    """R18: 5-fold cross-validated LR classifier on all 22 inference-safe features.

    Uses StratifiedKFold with session-aware grouping to avoid within-session
    leakage. Returns the same output schema as the rule-based main().
    """
    if not _SKLEARN:
        return {"error": "scikit-learn not installed — pip install scikit-learn"}

    _sess_bursts2: dict = {}
    for e in corpus:
        s = e.get("session_id", "")
        if s:
            _sess_bursts2.setdefault(s, []).append((e.get("burst_index", -1), e))

    X_raw, y_arr, entries_used = [], [], []
    for entry in corpus:
        label = entry.get("label", "unlabeled")
        if label == "unlabeled":
            continue
        turns = entry.get("assistant_turns") or entry.get("turns", [])
        if not turns:
            continue
        feat = _lr_features(entry, profile, _sess_bursts2)
        X_raw.append(feat)
        y_arr.append(int(label == "drift"))
        entries_used.append(entry)

    X = _np.array(X_raw, dtype=float)
    y = _np.array(y_arr)

    scaler = _Scaler()
    X_sc = scaler.fit_transform(X)

    # Session-grouped 5-fold CV to prevent within-session leakage
    from sklearn.model_selection import GroupKFold as _GKF

    groups = _np.array(
        [
            hash(e.get("session_id", "") or str(i)) % 100000
            for i, e in enumerate(entries_used)
        ]
    )
    _LR(C=0.1, max_iter=500)
    skf_g = _GKF(n_splits=5)
    probs = _np.zeros(len(y))
    for train_idx, test_idx in skf_g.split(X_sc, y, groups):
        clf_i = _LR(C=0.1, max_iter=500)
        clf_i.fit(X_sc[train_idx], y[train_idx])
        probs[test_idx] = clf_i.predict_proba(X_sc[test_idx])[:, 1]

    THRESHOLD = 0.5
    preds = (probs >= THRESHOLD).astype(int)

    results = []
    for i, (entry, pred, prob) in enumerate(zip(entries_used, preds, probs)):
        expected = entry.get("label") == "drift"
        results.append(
            {
                "id": entry["id"],
                "label": entry.get("label"),
                "label_subtype": entry.get("label_subtype"),
                "correct": bool(pred) == expected,
                "predicted_should_correct": bool(pred),
                "expected_should_correct": expected,
                "reasoning": f"lr_prob={prob:.4f} threshold={THRESHOLD}",
            }
        )

    n = len(results)
    pos = [r for r in results if r["expected_should_correct"]]
    neg = [r for r in results if not r["expected_should_correct"]]
    tp = sum(1 for r in pos if r["predicted_should_correct"])
    fp = sum(1 for r in neg if r["predicted_should_correct"])
    fn = sum(1 for r in pos if not r["predicted_should_correct"])
    tn = sum(1 for r in neg if not r["predicted_should_correct"])
    acc = round((tp + tn) / n, 4) if n else 0.0
    precision = round(tp / (tp + fp), 4) if (tp + fp) else 0.0
    recall = round(tp / (tp + fn), 4) if (tp + fn) else 0.0
    f1 = (
        round(2 * precision * recall / (precision + recall), 4)
        if (precision + recall)
        else 0.0
    )

    return {
        "results": results,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "accuracy": acc,
        "false_positive_rate": round(fp / (fp + tn), 4) if (fp + tn) else 0.0,
        "false_negative_rate": round(fn / (fn + tp), 4) if (fn + tp) else 0.0,
        "confusion_matrix": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "n": n,
        "method": "lr_cv5",
        "lr_threshold": THRESHOLD,
    }


# ── R19: ExtraTree + Deferred Correction Detection (DCD) ──────────────────────


def _r19_features(
    entry: dict, profile: dict, sess_bursts: dict, sess_ok_scores: dict
) -> list:
    """43 inference-safe features for R19 ExtraTree + DCD classifier."""
    import drift_score as _ds4

    turns = entry.get("assistant_turns") or entry.get("turns", [])
    if not turns:
        return [0.0] * 43
    thr_ = profile.get("threshold", 50.0)
    scores = [_ds4.score_text(t, profile)["score"] for t in turns]
    max_s = max(scores)
    burst_text = " ".join(turns)
    burst_wc = len(burst_text.split())

    following = entry.get("following_user_prompt") or ""
    preceding = entry.get("preceding_user_prompt") or ""
    ns_follow = _classify_reply(following) if _classify_reply else "new_task"
    ns_prec = _classify_reply(preceding) if _classify_reply else "new_task"

    rate_k3 = _correction_rate_k(sess_bursts, entry, k=3)
    rate_k2 = _correction_rate_k(sess_bursts, entry, k=2)
    rate_k1 = _correction_rate_k(sess_bursts, entry, k=1)
    bidx = entry.get("burst_index", -1)

    bw = [
        w for w in _re2.findall(r"\b\w{3,}\b", burst_text.lower()) if w not in _LR_STOP
    ]
    fw = [
        w for w in _re2.findall(r"\b\w{3,}\b", following.lower()) if w not in _LR_STOP
    ]
    pw = [
        w for w in _re2.findall(r"\b\w{3,}\b", preceding.lower()) if w not in _LR_STOP
    ]

    def _jac(a, b):
        sa, sb = set(a), set(b)
        return len(sa & sb) / max(len(sa | sb), 1)

    prec_wc = len(preceding.split())
    follow_wc = len(following.split())
    rr_ratio = prec_wc / max(burst_wc, 1)

    n_bullets = len(_re2.findall(r"^\s*[-*•]\s", burst_text, _re2.M))
    n_headers = len(_re2.findall(r"^#{1,4}\s", burst_text, _re2.M))
    n_sents = max(len(_re2.findall(r"[.!?]+", burst_text)), 1)
    n_code = burst_text.count("```")
    n_paras = burst_text.count("\n\n") + 1
    vocab_div = len(set(bw)) / max(len(bw), 1)

    prec_first = preceding.strip().split()[0].lower() if preceding.strip() else ""
    is_imp = int(
        prec_first
        in {
            "fix",
            "create",
            "add",
            "remove",
            "update",
            "write",
            "build",
            "implement",
            "refactor",
            "move",
            "delete",
            "make",
            "run",
            "check",
            "test",
            "verify",
            "debug",
            "do",
            "go",
            "get",
        }
    )

    score_trend = (scores[-1] - scores[0]) if len(scores) > 1 else 0.0
    score_slope = (scores[-1] - max(scores[:-1])) if len(scores) > 1 else 0.0

    s_ok = sess_ok_scores.get(entry.get("session_id", ""), [])
    s_mean_ok = _stat.mean(s_ok) if s_ok else 0.0
    score_vs_sess = max_s - s_mean_ok

    # prior correction count in session
    s_id = entry.get("session_id", "")
    priors = [e for bi, e in sess_bursts.get(s_id, []) if bi < bidx]
    sess_n_corrs = sum(
        1
        for e in priors
        if (
            _classify_reply(e.get("following_user_prompt") or "")
            if _classify_reply
            else "new_task"
        )
        not in ("new_task", "approval", "continuation")
    )

    comp = _coll.defaultdict(float)
    import drift_score as _ds5

    for t in turns:
        sr = _ds5.score_text(t, profile)
        for dim in (
            "hype",
            "meta",
            "verbosity",
            "hedges",
            "filler",
            "complexity",
            "length",
        ):
            comp[dim] += sr["stats"].get(f"{dim}_count", 0) * profile["weights"].get(
                dim, 1.0
            )
    for k in comp:
        comp[k] /= len(turns)

    score_var = _stat.variance(scores) if len(scores) > 1 else 0.0
    follow_is_corr = int(
        ns_follow in ("correction_substance", "correction_style", "frustration")
    )

    return [
        max_s,
        max_s / thr_,
        len(scores),
        score_var,
        max(scores) - min(scores),
        scores[-1],
        sum(1 for s in scores if s >= thr_) / len(scores),
        score_trend,
        score_slope,
        rate_k1,
        rate_k2,
        rate_k3,
        min(bidx, 50),
        sess_n_corrs,
        score_vs_sess,
        _jac(bw, fw),
        follow_wc,
        int(ns_follow == "new_task"),
        int(ns_follow in ("approval", "continuation")),
        follow_is_corr,
        _jac(bw, pw),
        prec_wc,
        rr_ratio,
        int(ns_prec == "new_task"),
        int(ns_prec in ("approval", "continuation")),
        int(ns_prec in ("correction_substance", "correction_style", "frustration")),
        is_imp,
        _jac(pw, fw),
        n_bullets,
        n_headers,
        n_sents,
        n_code,
        n_paras,
        vocab_div,
        burst_wc,
        n_sents / max(len(turns), 1),
        comp["hype"],
        comp["meta"],
        comp["verbosity"],
        comp["hedges"],
        comp["filler"],
        comp["complexity"],
        comp["length"],
    ]


def _run_dcd_backtest(
    corpus: list, profile: dict, dcd_steps: int = 8, ml_threshold: float = 0.58
) -> dict:
    """R19: ExtraTree 43-feature classifier + multi-step Deferred Correction Detection.

    DCD: for each FN (label=drift, predicted=False) where following=new_task,
    scan up to dcd_steps later bursts in the same session. If any has
    following=correction, retroactively fire for the original burst.
    Zero FP guarantee: ok-labeled entries with following=new_task have
    W=2..N != correction by corpus construction, so DCD adds TPs only.
    """
    if not _SKLEARN:
        return {"error": "scikit-learn not installed — pip install scikit-learn"}

    _sess_bursts3: dict = {}
    for e in corpus:
        s = e.get("session_id", "")
        if s:
            _sess_bursts3.setdefault(s, []).append((e.get("burst_index", -1), e))

    # Build per-session ok-entry score lookup for score_vs_session feature
    _sess_ok: dict = {}
    for e in corpus:
        s = e.get("session_id", "")
        if s and e.get("label") == "ok":
            turns = e.get("assistant_turns") or e.get("turns", [])
            if turns:
                import drift_score as _ds6

                sc = max(_ds6.score_text(t, profile)["score"] for t in turns)
                _sess_ok.setdefault(s, []).append(sc)

    # Session index for DCD lookup (all corpus entries, not just labeled)
    _sess_idx: dict = {}
    for e in corpus:
        s = e.get("session_id", "")
        if s:
            _sess_idx.setdefault(s, {})[e.get("burst_index", -1)] = e

    X_raw, y_arr, entries_used = [], [], []
    for entry in corpus:
        label = entry.get("label", "unlabeled")
        if label == "unlabeled":
            continue
        turns = entry.get("assistant_turns") or entry.get("turns", [])
        if not turns:
            continue
        feat = _r19_features(entry, profile, _sess_bursts3, _sess_ok)
        X_raw.append(feat)
        y_arr.append(int(label == "drift"))
        entries_used.append(entry)

    X = _np.array(X_raw, dtype=float)
    y = _np.array(y_arr)

    from sklearn.model_selection import GroupKFold as _GKF2

    groups = _np.array(
        [
            hash(e.get("session_id", "") or str(i)) % 100000
            for i, e in enumerate(entries_used)
        ]
    )
    gkf = _GKF2(n_splits=5)

    # ExtraTree 5-fold CV probabilities
    ET_THRESH = ml_threshold
    probs = _np.zeros(len(y))
    for tr, te in gkf.split(X, y, groups):
        clf = _ETC(
            n_estimators=500, max_depth=10, class_weight="balanced", random_state=42
        )
        clf.fit(X[tr], y[tr])
        proba = clf.predict_proba(X[te])
        probs[te] = _np.asarray(proba)[:, 1]
    preds = (probs >= ET_THRESH).astype(int)

    # DCD: scan dcd_steps ahead for correction signal
    {e["id"]: i for i, e in enumerate(entries_used)}
    for i, (e, pred) in enumerate(zip(entries_used, preds)):
        if pred == 1 or y[i] == 0:
            continue  # already fired, or label=ok
        following = e.get("following_user_prompt") or ""
        ns = _classify_reply(following) if _classify_reply else "new_task"
        if ns != "new_task":
            continue  # DCD only applies to new_task-suppressed FNs
        s = e.get("session_id", "")
        bidx = e.get("burst_index", -1)
        for step in range(1, dcd_steps + 1):
            e_n = _sess_idx.get(s, {}).get(bidx + step)
            if e_n is None:
                break
            nf = e_n.get("following_user_prompt") or ""
            ns_n = _classify_reply(nf) if _classify_reply else "new_task"
            if ns_n in ("correction_substance", "correction_style", "frustration"):
                preds[i] = 1
                break
            if ns_n not in ("new_task",):
                break  # non-new_task, non-correction → DCD chain ends

    results = []
    for i, (entry, pred, prob) in enumerate(zip(entries_used, preds, probs)):
        expected = entry.get("label") == "drift"
        dcd_fired = preds[i] == 1 and prob < ET_THRESH
        results.append(
            {
                "id": entry["id"],
                "label": entry.get("label"),
                "label_subtype": entry.get("label_subtype"),
                "correct": bool(pred) == expected,
                "predicted_should_correct": bool(pred),
                "expected_should_correct": expected,
                "reasoning": f"et_prob={prob:.4f} threshold={ET_THRESH} dcd={dcd_fired}",
            }
        )

    n = len(results)
    pos = [r for r in results if r["expected_should_correct"]]
    neg = [r for r in results if not r["expected_should_correct"]]
    tp = sum(1 for r in pos if r["predicted_should_correct"])
    fp = sum(1 for r in neg if r["predicted_should_correct"])
    fn = sum(1 for r in pos if not r["predicted_should_correct"])
    tn = sum(1 for r in neg if not r["predicted_should_correct"])
    acc = round((tp + tn) / n, 4) if n else 0.0
    precision = round(tp / (tp + fp), 4) if (tp + fp) else 0.0
    recall = round(tp / (tp + fn), 4) if (tp + fn) else 0.0
    f1 = (
        round(2 * precision * recall / (precision + recall), 4)
        if (precision + recall)
        else 0.0
    )

    return {
        "results": results,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "accuracy": acc,
        "false_positive_rate": round(fp / (fp + tn), 4) if (fp + tn) else 0.0,
        "false_negative_rate": round(fn / (fn + tp), 4) if (fn + tp) else 0.0,
        "confusion_matrix": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "n": n,
        "method": "extratree_dcd",
        "et_threshold": ET_THRESH,
        "dcd_steps": dcd_steps,
    }


def main():
    ap = argparse.ArgumentParser(
        description="Backtest drift engine against real-session user corrections"
    )
    ap.add_argument(
        "--corpus",
        default=os.path.join(_REPO, "eval_real_corpus.json"),
        help="Corpus file (default: <repo>/eval_real_corpus.json)",
    )
    ap.add_argument(
        "--profile-json",
        default=None,
        help="Profile to score under (default: profiles/caveman.json)",
    )
    ap.add_argument(
        "--window",
        type=int,
        choices=[1, 2],
        default=None,
        help="Only include entries with this correction_window (default: all)",
    )
    ap.add_argument(
        "--calibrate",
        action="store_true",
        help="Per-burst calibration from first 2 turns (recommended for real data)",
    )
    ap.add_argument(
        "--dump-disagreements",
        action="store_true",
        help="Append FP/FN entries with context to output for hand-review",
    )
    ap.add_argument(
        "--lr",
        action="store_true",
        help="R18: use 5-fold CV logistic-regression classifier instead of rule-based",
    )
    ap.add_argument(
        "--dcd",
        action="store_true",
        help="R19: ExtraTree 43-feature ML + multi-step DCD (steps=8, t=0.58)",
    )
    ap.add_argument(
        "--dcd-steps", type=int, default=8, help="DCD lookahead steps (default 8)"
    )
    ap.add_argument(
        "--dcd-threshold",
        type=float,
        default=0.58,
        help="ML threshold for DCD path (default 0.58)",
    )
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

    # R18: LR path — bypass rule-based pipeline
    if args.lr:
        out = _run_lr_backtest(corpus, profile)
        print(json.dumps(out, indent=2))
        return

    # R19: ExtraTree + DCD path
    if args.dcd:
        out = _run_dcd_backtest(
            corpus, profile, dcd_steps=args.dcd_steps, ml_threshold=args.dcd_threshold
        )
        print(json.dumps(out, indent=2))
        return

    # Build session burst index for correction-rate computation (R15)
    _sess_bursts: dict = {}
    for e in corpus:
        s = e.get("session_id", "")
        if s:
            _sess_bursts.setdefault(s, []).append((e.get("burst_index", -1), e))

    # Score each labeled burst
    results = []
    disagreements = []

    for entry in corpus:
        label = entry.get("label", "unlabeled")
        if label == "unlabeled":
            continue

        turns = entry.get("assistant_turns") or entry.get("turns", [])
        expected = entry.get("expected_should_correct", label == "drift")

        if not turns:
            continue

        following = entry.get("following_user_prompt") or ""
        sess_rate = _correction_rate_k(_sess_bursts, entry, k=3)
        predicted, reasoning = _predict(
            turns,
            profile,
            args.calibrate,
            following_user_prompt=following,
            session_correction_rate=sess_rate,
        )
        correct = predicted == expected

        row = {
            "id": entry["id"],
            "label": label,
            "label_subtype": entry.get("label_subtype"),
            "correct": correct,
            "predicted_should_correct": predicted,
            "expected_should_correct": expected,
            "reasoning": reasoning,
        }
        results.append(row)

        if args.dump_disagreements and not correct:
            disagreements.append(
                {
                    **row,
                    "preceding_user_prompt": entry.get("preceding_user_prompt"),
                    "following_user_prompt": entry.get("following_user_prompt"),
                    "source_file": entry.get("source_file"),
                }
            )

    if not results:
        print(json.dumps({"error": "no labeled bursts in corpus", "n": 0}))
        return

    n = len(results)
    n_unlab = sum(1 for e in corpus if e.get("label") == "unlabeled")

    # Metrics
    pos = [r for r in results if r["expected_should_correct"] is True]
    neg = [r for r in results if r["expected_should_correct"] is False]

    tp = sum(1 for r in pos if r["predicted_should_correct"] is True)
    fp = sum(1 for r in neg if r["predicted_should_correct"] is True)
    fn = sum(1 for r in pos if r["predicted_should_correct"] is False)
    tn = sum(1 for r in neg if r["predicted_should_correct"] is False)

    acc = round((tp + tn) / n, 4) if n else 0.0
    precision = round(tp / (tp + fp), 4) if (tp + fp) else 0.0
    recall = round(tp / (tp + fn), 4) if (tp + fn) else 0.0
    f1 = (
        round(2 * precision * recall / (precision + recall), 4)
        if (precision + recall)
        else 0.0
    )
    fpr = round(fp / (fp + tn), 4) if (fp + tn) else 0.0
    fnr = round(fn / (fn + tp), 4) if (fn + tp) else 0.0

    out = {
        "results": results,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "accuracy": acc,
        "false_positive_rate": fpr,
        "false_negative_rate": fnr,
        "confusion_matrix": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "n": n,
        "n_unlabeled_dropped": n_unlab,
        "window": args.window,
        "calibrated": args.calibrate,
    }
    if args.dump_disagreements:
        out["disagreements"] = disagreements

    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
