#!/usr/bin/env python3
"""drift_trajectory.py — drift-as-a-vector (stdlib only).

The point scorer in `drift_score.py` answers "how bad is *this* turn?" with a
single 0-100 scalar. That is a snapshot, and a snapshot lies about dynamics.
Following Morin, drift is better modeled as a *vector*: it has a direction and a
velocity, not just a magnitude. A session sitting at 40% but climbing 12
points/turn is heading for the wall; a one-off spike to 80% that immediately
falls back to 20% is the system *self-correcting* — that is adaptive drift and
should be tolerated, not suppressed.

This module consumes a time-ordered list of per-turn drift scores (the same
0..100, higher==worse orientation as the point engine) and derives:

* velocity  — least-squares slope over a recent window (drift-points/turn).
* trend     — rising / stable / recovering.
* alert     — none / watch / warn / critical, combining level *and* motion.
* adaptivity — is a high reading a tolerable blip or a degenerative run?
* session   — an EWMA-weighted aggregate plus streak/rate diagnostics.

Design contract (mirrors drift_score.py)
---------------------------------------
* Pure stdlib. No numpy, no network, no clock, importable under Python 3.8+.
* Deterministic. Same scores in => same structure out, always.
* Total. Never raises on adversarial input (empty list, NaNs, junk); degrades
  to a neutral "no trend yet" result.
* Side-effect free. Computes only; persistence lives elsewhere.
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

ENGINE_VERSION = "1.0.0"

# Trend / alert tuning. These mirror the spec in the module's public contract;
# they are the only magic numbers and live here so they are easy to retune.
_RISING_EPS = 1.0      # |slope| <= this is "stable", not a real trend.
_WARN_VELOCITY = 8.0   # points/turn that counts as "worsening fast".
_RECOVER_VELOCITY = -5.0  # points/turn (negative) that counts as recovering.
_DEGENERATIVE_VELOCITY = 5.0  # session-level worsening slope.
_DEGENERATIVE_RATE = 0.30     # fraction of turns above threshold.
_CRITICAL_STREAK = 3   # consecutive turns >= threshold => critical.
_EWMA_ALPHA = 0.4      # recency weight for the session EWMA.


# --------------------------------------------------------------------------- #
# Input sanitation
# --------------------------------------------------------------------------- #


def _clean(scores) -> List[float]:
    """Coerce arbitrary input into a list of finite floats (clamped 0..100).

    Drops anything non-numeric or non-finite. Order is preserved — these are a
    time series, so reordering would be a lie. Never raises.
    """
    out: List[float] = []
    if not scores:
        return out
    try:
        it = list(scores)
    except TypeError:
        return out
    for v in it:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(f):
            continue
        out.append(max(0.0, min(100.0, f)))
    return out


# --------------------------------------------------------------------------- #
# Velocity (least-squares slope)
# --------------------------------------------------------------------------- #


def compute_velocity(scores: List[float], window: int = 5) -> float:
    """Linear-regression slope over the last `window` scores.

    Units: drift-points/turn. Positive == worsening, negative == recovering,
    zero == stable. Returns 0.0 on fewer than 2 usable data points (a single
    point has no direction yet). Pure stdlib (ordinary least squares closed
    form; no numpy).
    """
    clean = _clean(scores)
    if window is None or window < 2:
        window = 2
    recent = clean[-int(window):] if window else clean
    n = len(recent)
    if n < 2:
        return 0.0

    # x is the turn index 0..n-1; y is the score. Slope = cov(x,y)/var(x).
    # var(x) is constant and nonzero for n>=2, so this is always well-defined.
    mean_x = (n - 1) / 2.0
    mean_y = sum(recent) / n
    num = 0.0
    den = 0.0
    for i, y in enumerate(recent):
        dx = i - mean_x
        num += dx * (y - mean_y)
        den += dx * dx
    if den == 0.0:
        return 0.0
    return round(num / den, 4)


# --------------------------------------------------------------------------- #
# Streaks
# --------------------------------------------------------------------------- #


def _max_streak(scores: List[float], threshold: float) -> int:
    """Longest consecutive run of scores >= threshold."""
    best = run = 0
    for s in scores:
        if s >= threshold:
            run += 1
            if run > best:
                best = run
        else:
            run = 0
    return best


def _tail_streak(scores: List[float], threshold: float) -> int:
    """Length of the current (trailing) run of scores >= threshold."""
    run = 0
    for s in reversed(scores):
        if s >= threshold:
            run += 1
        else:
            break
    return run


# --------------------------------------------------------------------------- #
# Trajectory classification
# --------------------------------------------------------------------------- #


def classify_trajectory(
    scores: List[float], threshold: float = 70.0, window: int = 5
) -> dict:
    """Classify the *motion* of a drift series, not just its level.

    Returns ``{velocity, trend, alert_level, description}`` where:

    * ``trend``       — "rising" | "stable" | "recovering".
    * ``alert_level`` — "none" | "watch" | "warn" | "critical".

    Combination logic (a fast rise at a high level is worse than either alone):

    * velocity > 8/turn AND a recent score > threshold       => critical.
    * velocity > 8/turn AND a recent score > threshold*0.7    => warn.
    * velocity < -5/turn                                      => recovering, none.
    * >= 3 consecutive scores >= threshold                    => critical, always.
    """
    clean = _clean(scores)
    velocity = compute_velocity(clean, window)

    if not clean:
        return {
            "velocity": 0.0,
            "trend": "stable",
            "alert_level": "none",
            "description": "no data yet",
        }

    # Trend from the sign of the windowed slope, with a dead-band so float noise
    # near zero reads as "stable" rather than flapping rising/recovering.
    if velocity > _RISING_EPS:
        trend = "rising"
    elif velocity < -_RISING_EPS:
        trend = "recovering"
    else:
        trend = "stable"

    win = max(2, int(window) if window else 2)
    recent = clean[-win:]
    recent_max = max(recent)
    streak = _tail_streak(clean, threshold)
    watch_level = threshold * 0.7

    # Resolve the alert level. A degenerative consecutive run is critical
    # regardless of velocity (it may have plateaued *above* threshold — flat
    # slope, still on fire). Otherwise combine velocity with the level reached.
    if streak >= _CRITICAL_STREAK:
        alert = "critical"
    elif velocity > _WARN_VELOCITY and recent_max > threshold:
        alert = "critical"
    elif velocity > _WARN_VELOCITY and recent_max > watch_level:
        alert = "warn"
    elif velocity < _RECOVER_VELOCITY:
        # Actively recovering: stand down even if a recent reading was high.
        trend = "recovering"
        alert = "none"
    elif recent_max > threshold:
        # At/over the line but not climbing fast and not a sustained run —
        # a single spike. Worth watching, not yet worth warning.
        alert = "watch"
    elif velocity > _WARN_VELOCITY:
        # Climbing fast but still in safe territory: keep an eye on it.
        alert = "watch"
    else:
        alert = "none"

    description = _describe(trend, alert, velocity, recent_max, streak, threshold)
    return {
        "velocity": velocity,
        "trend": trend,
        "alert_level": alert,
        "description": description,
    }


def _describe(
    trend: str,
    alert: str,
    velocity: float,
    recent_max: float,
    streak: int,
    threshold: float,
) -> str:
    """Human-readable one-liner for logs/badges. Deterministic."""
    if alert == "none" and trend == "stable":
        return "stable; no action needed"
    if trend == "recovering":
        return f"recovering at {velocity:+.1f}/turn; self-correcting"
    if alert == "critical" and streak >= _CRITICAL_STREAK:
        return (
            f"degenerative: {streak} consecutive turns at/over {threshold:.0f}"
        )
    if alert == "critical":
        return (
            f"rising {velocity:+.1f}/turn into drift "
            f"(recent peak {recent_max:.0f})"
        )
    if alert == "warn":
        return f"rising {velocity:+.1f}/turn toward threshold"
    if alert == "watch":
        if velocity > _WARN_VELOCITY:
            return f"climbing {velocity:+.1f}/turn but still safe"
        return f"spike to {recent_max:.0f}; watching for a trend"
    return f"trend={trend} velocity={velocity:+.1f}/turn"


# --------------------------------------------------------------------------- #
# Adaptive vs degenerative drift (Morin: tolerate the self-correcting blip)
# --------------------------------------------------------------------------- #


def is_adaptive_drift(scores: List[float], threshold: float = 70.0) -> bool:
    """True when drift is adaptive (a one-off, self-correcting excursion).

    Adaptive  = an isolated turn above threshold, surrounded by turns below it
                (the system spiked and recovered on its own).
    Degenerative = two-plus consecutive turns above threshold, or a rising
                velocity carrying the series upward.

    Morin's principle: adaptive drift is the system exploring/recovering and
    should be *tolerated*, not clamped. Returns False when there is no drift at
    all (nothing to classify) and False on degenerative runs.
    """
    clean = _clean(scores)
    if not clean:
        return False

    over = [i for i, s in enumerate(clean) if s >= threshold]
    if not over:
        return False  # no excursion at all — not "adaptive drift", just fine.

    # Any two adjacent over-threshold turns => sustained => degenerative.
    for a, b in zip(over, over[1:]):
        if b - a == 1:
            return False

    # A rising overall velocity means the isolated spikes are trending upward —
    # the excursions are not self-correcting, they are the leading edge.
    if compute_velocity(clean, window=len(clean)) > _RISING_EPS:
        return False

    # Every excursion is isolated AND the series is not climbing: adaptive.
    # Require that each spike is genuinely surrounded by below-threshold turns
    # (or sits at a boundary with a below-threshold neighbor).
    n = len(clean)
    for i in over:
        left_ok = i == 0 or clean[i - 1] < threshold
        right_ok = i == n - 1 or clean[i + 1] < threshold
        if not (left_ok and right_ok):
            return False
    return True


# --------------------------------------------------------------------------- #
# Session-level aggregate
# --------------------------------------------------------------------------- #


def _ewma(scores: List[float], alpha: float = _EWMA_ALPHA) -> float:
    """Exponential weighted moving average; recent turns weigh more.

    Recurrence: e_0 = s_0; e_t = alpha*s_t + (1-alpha)*e_{t-1}. Returns 0.0 on
    empty input.
    """
    if not scores:
        return 0.0
    e = scores[0]
    for s in scores[1:]:
        e = alpha * s + (1.0 - alpha) * e
    return e


def compute_elevation_score(scores: List[float], threshold: float = 70.0) -> float:
    """Mean distance above the "no-concern zone" (threshold*0.5).

    For each turn, take max(0, score - threshold*0.5) and average over the
    series. 0 if all scores are below threshold*0.5. Higher == more sustained
    elevation. Used as a continuous signal for subclinical chronic drift: a
    session that never spikes over threshold but parks persistently in the
    50-70% band still accumulates a meaningful elevation score, whereas a single
    isolated spike surrounded by quiet turns is diluted toward zero.

    Pure stdlib; returns 0.0 on empty/junk input.
    """
    clean = _clean(scores)
    if not clean:
        return 0.0
    floor = threshold * 0.5
    return round(sum(max(0.0, s - floor) for s in clean) / len(clean), 4)


def compute_session_score(
    scores: List[float],
    threshold: float = 70.0,
    register: Optional[List[Tuple[float, float]]] = None,
) -> dict:
    """Aggregate a whole session into one drift verdict.

    Returns ``{session_score, drift_rate, max_streak, is_degenerative,
    chronic_subclinical, elevation, ewma}``:

    * ``session_score`` — the EWMA (alpha=0.4) rounded; recent turns dominate so
      a session that ended badly scores worse than its flat mean would suggest.
    * ``drift_rate``    — fraction of turns at/over threshold (0..1).
    * ``max_streak``    — longest consecutive run at/over threshold.
    * ``chronic_subclinical`` — sustained sub-threshold elevation with no clean
      recovery turns; the "death by a thousand cuts" pattern that never spikes
      hard enough to trip the velocity/streak gates but never lets up either.
    * ``is_degenerative`` — (velocity > 5/turn AND drift_rate > 0.3) OR
      chronic_subclinical. A flat plateau parked just under threshold is
      degenerative even though its slope is ~0.
    * ``elevation``     — mean distance above the no-concern zone (see
      ``compute_elevation_score``); a continuous chronic-drift signal.
    * ``ewma``          — the raw EWMA (== session_score before rounding).

    ``register`` (optional) is a per-turn list of ``(words_per_sentence,
    word_count)`` aligned 1:1 with ``scores``. When supplied it enables a
    short-session early-warning gate (see ``short_session_alarm`` below); when
    omitted the function behaves exactly as before. It is metadata only — it
    never changes ``session_score``, ``drift_rate``, ``elevation`` or any other
    field, only the degenerative verdict on short sessions.
    """
    clean = _clean(scores)
    if not clean:
        return {
            "session_score": 0.0,
            "drift_rate": 0.0,
            "max_streak": 0,
            "is_degenerative": False,
            "chronic_subclinical": False,
            "elevation": 0.0,
            "ewma": 0.0,
            "repeating_spike_degenerate": False,
        }

    ewma = _ewma(clean, _EWMA_ALPHA)
    n = len(clean)
    drift_rate = sum(1 for s in clean if s >= threshold) / n
    max_streak = _max_streak(clean, threshold)
    velocity = compute_velocity(clean, window=n)
    elevation = compute_elevation_score(clean, threshold)

    # Chronic subclinical drift: a sustained moderate hedge load that never
    # spikes over threshold (so streak/velocity gates miss it) but also never
    # produces a clean recovery turn. Look at the recent tail: if it is held
    # above 75% of threshold on average with no turn dropping into the
    # no-concern zone (below 45% of threshold), the session is degenerating
    # slowly rather than self-correcting.
    tail = clean[-5:]
    # Chronic subclinical: sustained moderate hedge load, never spikes, never
    # recovers. Require 80% of tail above the no-concern floor (lowered from
    # 100% to tolerate one outlier clean turn without masking a chronic pattern).
    tail_above_floor = sum(1 for s in tail if s > threshold * 0.35) / len(tail) if tail else 0
    chronic_subclinical = (
        len(clean) >= 5
        and (sum(tail) / len(tail)) > threshold * 0.72  # 72% of threshold
        and tail_above_floor >= 0.80                    # ≥80% of tail above floor
    )

    # Velocity-driven degenerative: fast rise toward threshold even without
    # crossing it yet. A session climbing >10 pts/turn with any turn already
    # at 80%+ of threshold is heading for the wall regardless of drift_rate.
    #
    # Use the *recent* (last-5) windowed slope, not the full-session slope, so a
    # late-onset rise is not diluted by a long clean prefix. On a 10-15 turn
    # session, seven flat turns followed by a sharp climb leave the full-window
    # slope well under 10 even when the recent turns are rocketing upward; the
    # windowed slope tracks the live trajectory the way the recency-weighted
    # session score does.
    recent_velocity = compute_velocity(clean, window=5)
    # Require *sustained* recent elevation (>=2 turns of the last 5 parked in the
    # 0.72+ band), not a single final spike. A lone late spike off a flat
    # baseline is the adaptive case (system explored once and will recover);
    # two-plus recent turns already elevated is a genuine late-onset climb into
    # drift. Counting over the last 5 (not just 3) keeps an oscillating
    # rise — high/clean/high — from being disqualified by the intervening dip.
    recent_elevated = sum(1 for s in clean[-5:] if s >= threshold * 0.72)
    velocity_alarm = recent_velocity > 10.0 and recent_elevated >= 2

    # Short-session emerging-drift alarm. On a very short session (<=3 turns) the
    # streak/chronic gates cannot fire (they need >=5 turns of history) and the
    # standard velocity_alarm needs two turns already in the 0.72+ band, which an
    # *emerging* drift has not reached yet. But a 2-3 turn session that rockets
    # off a near-zero opener into a moderate, verbose, formal-register turn is the
    # leading edge of a relapse — the assistant has abandoned the terse contract
    # and started writing long, high-words-per-sentence prose, even though the
    # point score is still mid-band. We catch it with a deliberately narrow gate:
    #   * short session (<=3 turns),
    #   * fast climb (recent slope > threshold*0.25 ~ +17.5/turn at thr=70),
    #   * the final turn sits in the moderate band (thr*0.35 < last < thr): high
    #     enough to be a real departure, still below the point threshold,
    #   * AND the final turn is verbose in *register* — words_per_sentence >= 10
    #     and word_count >= 35. This register gate is the discriminator that
    #     separates a genuine formal-prose relapse from an ordinary terse reply
    #     that merely carries one stray hedge/filler token (those land at low wps
    #     and short length and are correctly left alone).
    # The register data is only available when the caller supplies `register`;
    # without it this gate is disabled and behaviour is unchanged.
    short_session_alarm = False
    if register is not None and 0 < len(register) == len(clean) and len(clean) <= 3:
        last_wps, last_wc = register[-1]
        short_session_alarm = (
            recent_velocity > threshold * 0.25
            and threshold * 0.35 < clean[-1] < threshold
            and last_wps >= 10.0
            and last_wc >= 35.0
        )

    is_degenerative = (
        velocity > _DEGENERATIVE_VELOCITY and drift_rate > _DEGENERATIVE_RATE
    ) or chronic_subclinical or velocity_alarm or short_session_alarm

    # Narrow-band high (zero-masked chronic): a session whose *non-zero* turns
    # are clustered tightly just under threshold, but interleaved with clean
    # (zero) turns that drag the tail mean below the chronic_subclinical gate.
    # The zeros are not recoveries here — they are interspersed clean turns in a
    # session that, whenever it produces output, parks consistently at 85%+ of
    # threshold within a narrow band (<25% of threshold spread). That tight,
    # high, repeatable clustering is degenerative even though the zeros keep the
    # tail-mean gate unreachable. Requires >=3 non-zero turns so a single high
    # spike off zeros (the adaptive case) cannot trip it.
    nonzero = [s for s in clean if s > 0]
    if not is_degenerative and len(nonzero) >= 3:
        nz_mean = sum(nonzero) / len(nonzero)
        nz_range = max(nonzero) - min(nonzero)
        if nz_mean > threshold * 0.85 and nz_range < threshold * 0.25:
            is_degenerative = True

    # Repeating-spike degenerative (oscillating relapse): a longer session that
    # crosses threshold three-plus times, each spike sandwiched by a clean
    # recovery turn. is_adaptive_drift() reads this as "every excursion is
    # isolated and self-correcting" and tolerates it — but a system that keeps
    # bouncing back over the line on a 6+ turn run is not exploring once and
    # settling, it is relapsing on a cycle. Three crossings on a session of at
    # least six turns is the discriminator: two crossings is still plausibly
    # adaptive (try, recover, try, recover, done), three-plus is a pattern. This
    # flag is surfaced separately from is_degenerative so the eval can OR it in
    # *without* the adaptive gate that (correctly, for the single-spike case)
    # suppresses it.
    above_thr_count = sum(1 for s in clean if s >= threshold)
    high_sub_count = sum(1 for s in clean if s >= threshold * 0.90)
    repeating_spike_degenerate = (
        (above_thr_count >= 3 and len(clean) >= 6)
        or (above_thr_count >= 2 and high_sub_count >= 4 and len(clean) >= 8)
    )

    return {
        "session_score": round(ewma, 2),
        "drift_rate": round(drift_rate, 4),
        "max_streak": max_streak,
        "is_degenerative": is_degenerative,
        "chronic_subclinical": chronic_subclinical,
        "elevation": elevation,
        "ewma": round(ewma, 4),
        "repeating_spike_degenerate": repeating_spike_degenerate,
    }


# --------------------------------------------------------------------------- #
# CLI / self-test
# --------------------------------------------------------------------------- #


def _selftest() -> int:
    # --- velocity ---------------------------------------------------------- #
    assert compute_velocity([]) == 0.0
    assert compute_velocity([50.0]) == 0.0
    # perfect linear ramp of +10/turn over the window.
    assert compute_velocity([10, 20, 30, 40, 50]) == 10.0, compute_velocity(
        [10, 20, 30, 40, 50]
    )
    # flat => zero slope.
    assert compute_velocity([40, 40, 40, 40]) == 0.0
    # recovering => negative slope.
    assert compute_velocity([80, 60, 40, 20]) == -20.0
    # windowing: only the last `window` matter.
    assert compute_velocity([0, 0, 0, 10, 20, 30, 40, 50], window=5) == 10.0
    # junk is dropped, not fatal.
    assert compute_velocity(["x", None, 10, 20, 30, float("nan")], window=5) == 10.0

    # --- classify_trajectory ---------------------------------------------- #
    # rising fast into the danger zone => critical.
    c = classify_trajectory([30, 45, 60, 75, 90], threshold=70.0)
    assert c["trend"] == "rising", c
    assert c["alert_level"] == "critical", c
    assert c["velocity"] > _WARN_VELOCITY, c

    # rising fast but still below threshold, peak in the 0.7..1.0 band => warn.
    c = classify_trajectory([20, 30, 40, 50, 55], threshold=70.0)
    # peak 55 > 49 (0.7*70), velocity ~8.5 > 8 => warn.
    assert c["alert_level"] in ("warn", "watch"), c

    # recovering => trend recovering, no alert.
    c = classify_trajectory([90, 70, 50, 30, 10], threshold=70.0)
    assert c["trend"] == "recovering", c
    assert c["alert_level"] == "none", c

    # sustained run at/over threshold => critical regardless of (flat) velocity.
    c = classify_trajectory([85, 85, 85, 85], threshold=70.0)
    assert c["alert_level"] == "critical", c

    # one-off spike, not climbing => watch, not warn/critical.
    c = classify_trajectory([20, 20, 85, 20, 20], threshold=70.0)
    assert c["alert_level"] == "watch", c

    # empty => neutral.
    c = classify_trajectory([], threshold=70.0)
    assert c["alert_level"] == "none" and c["trend"] == "stable", c

    # --- is_adaptive_drift ------------------------------------------------- #
    # isolated spike, flat surroundings => adaptive (tolerate).
    assert is_adaptive_drift([20, 20, 85, 20, 20], threshold=70.0) is True
    # two consecutive over threshold => degenerative.
    assert is_adaptive_drift([20, 85, 85, 20], threshold=70.0) is False
    # isolated spikes but trending up => degenerative (leading edge).
    assert is_adaptive_drift([0, 75, 10, 80, 20, 85], threshold=70.0) is False
    # never drifts => not adaptive drift (nothing to tolerate).
    assert is_adaptive_drift([10, 20, 30], threshold=70.0) is False
    # empty => False.
    assert is_adaptive_drift([], threshold=70.0) is False

    # --- compute_session_score -------------------------------------------- #
    s = compute_session_score([90, 85, 80, 88, 92], threshold=70.0)
    assert s["drift_rate"] == 1.0, s
    assert s["max_streak"] == 5, s
    assert s["session_score"] > 80.0, s

    # ended-badly weighting: same multiset, the climbing one scores higher.
    flat = compute_session_score([50, 50, 50, 50], threshold=70.0)
    climbed = compute_session_score([20, 40, 60, 80], threshold=70.0)
    assert climbed["session_score"] > flat["session_score"], (flat, climbed)

    # degenerative gate: climbing AND drifting often.
    deg = compute_session_score([10, 30, 75, 80, 85], threshold=70.0)
    assert deg["is_degenerative"] is True, deg
    # a single late spike is not degenerative (low drift_rate).
    notdeg = compute_session_score([10, 10, 10, 10, 80], threshold=70.0)
    assert notdeg["is_degenerative"] is False, notdeg

    # --- chronic subclinical drift ---------------------------------------- #
    # death-by-a-thousand-cuts: never spikes over 70, never recovers below the
    # no-concern zone, slope ~flat. Old velocity/streak gates miss it; the
    # chronic-band gate catches it.
    chronic = compute_session_score(
        [49, 68, 72, 64, 72, 66, 68], threshold=70.0
    )
    assert chronic["chronic_subclinical"] is True, chronic
    assert chronic["is_degenerative"] is True, chronic
    assert chronic["max_streak"] == 1, chronic  # streak gate would have missed
    assert chronic["elevation"] > 0.0, chronic

    # adaptive spike: one turn over threshold, the rest at zero — the recovery
    # turns drag the tail mean down, so NOT chronic and NOT degenerative.
    spike = compute_session_score([0, 0, 80, 0, 0, 0, 0], threshold=70.0)
    assert spike["chronic_subclinical"] is False, spike
    assert spike["is_degenerative"] is False, spike

    # clearly-above-threshold plateau is degenerative via the chronic gate too.
    high = compute_session_score([75, 80, 85, 90, 82], threshold=70.0)
    assert high["chronic_subclinical"] is True, high
    assert high["is_degenerative"] is True, high

    # short sessions can't be chronic (need >=5 turns of history).
    short = compute_session_score([68, 68, 68, 68], threshold=70.0)
    assert short["chronic_subclinical"] is False, short

    # --- short-session emerging-drift alarm (register-gated) -------------- #
    # A 2-turn session rocketing off a near-zero opener into a moderate, VERBOSE
    # final turn (high words-per-sentence, substantial length) is the leading
    # edge of a formal-prose relapse: flag it as degenerative.
    emerging = compute_session_score(
        [13.3, 32.6], threshold=70.0, register=[(13.0, 13), (15.0, 45)]
    )
    assert emerging["is_degenerative"] is True, emerging
    # Same score trajectory but a TERSE final turn (low wps, short) is an ordinary
    # reply with maybe one stray token — must NOT trip the alarm.
    terse = compute_session_score(
        [13.3, 32.6], threshold=70.0, register=[(13.0, 13), (4.0, 12)]
    )
    assert terse["is_degenerative"] is False, terse
    # Without register metadata the gate is disabled => unchanged (not degenerative).
    no_reg = compute_session_score([13.3, 32.6], threshold=70.0)
    assert no_reg["is_degenerative"] is False, no_reg
    # The alarm is short-session only: a long verbose climb is handled by the
    # existing gates, not this one (register present but len>3 ⇒ this gate off).
    longish = compute_session_score(
        [10, 12, 14, 33], threshold=70.0,
        register=[(3.0, 5), (3.0, 6), (4.0, 7), (15.0, 45)],
    )
    assert longish["is_degenerative"] is False, longish

    # --- compute_elevation_score ------------------------------------------ #
    assert compute_elevation_score([]) == 0.0
    # all below the no-concern floor (threshold*0.5 = 35) => 0.
    assert compute_elevation_score([10, 20, 30], threshold=70.0) == 0.0
    # sustained elevation accumulates; one isolated spike is diluted.
    sustained = compute_elevation_score([60, 65, 62, 68, 64], threshold=70.0)
    isolated = compute_elevation_score([0, 0, 80, 0, 0], threshold=70.0)
    assert sustained > isolated, (sustained, isolated)

    # empty => neutral zeros.
    e = compute_session_score([], threshold=70.0)
    assert e == {
        "session_score": 0.0,
        "drift_rate": 0.0,
        "max_streak": 0,
        "is_degenerative": False,
        "chronic_subclinical": False,
        "elevation": 0.0,
        "ewma": 0.0,
        "repeating_spike_degenerate": False,
    }, e

    # determinism.
    series = [10, 25, 40, 55, 70, 85]
    assert classify_trajectory(series) == classify_trajectory(series)
    assert compute_session_score(series) == compute_session_score(series)

    print("selftest OK  velocity/classify/adaptive/session all green")
    return 0


if __name__ == "__main__":
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(description="drift trajectory scoring (vector)")
    ap.add_argument("--selftest", action="store_true", help="run built-in tests")
    ap.add_argument(
        "--scores",
        help="comma-separated drift scores in turn order, e.g. 10,25,40,70",
    )
    ap.add_argument("--threshold", type=float, default=70.0)
    ap.add_argument("--window", type=int, default=5)
    args = ap.parse_args()

    if args.selftest:
        sys.exit(_selftest())

    raw = args.scores if args.scores is not None else sys.stdin.read()
    parsed = []
    for tok in raw.replace("\n", ",").split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            parsed.append(float(tok))
        except ValueError:
            continue

    out = {
        "engine_version": ENGINE_VERSION,
        "trajectory": classify_trajectory(parsed, args.threshold, args.window),
        "session": compute_session_score(parsed, args.threshold),
        "is_adaptive": is_adaptive_drift(parsed, args.threshold),
    }
    print(json.dumps(out, indent=2))
