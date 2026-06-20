#!/usr/bin/env python3
"""drift_controller.py — trajectory-aware, low-gain drift control (stdlib only).

The scoring engine (drift_score.py) answers "did *this* turn drift?". That is a
point measurement. Acting on a single point is a *program*: assert the correction
once and trust it. Edgar Morin's "ecology of action" warns that the nudge is
itself an action thrown into the system — it can boomerang (over-correct, nag,
make the model defensive, or whipsaw the persona). The remedy is *strategy*:
keep a short trajectory, read its shape, and intervene proportionally and
occasionally rather than reflexively on every spike.

This controller holds the per-session trajectory and decides *whether*, *how
hard*, and *with what framing* to nudge. It is deliberately low-gain:

  * One-off spikes (adaptive drift) are tolerated — a single high turn inside an
    otherwise compliant session is signal the model adapted to a real demand,
    not relapse. Correcting it boomerangs.
  * Only *sustained* drift past a cooldown earns a correction, so corrections
    don't stack turn-on-turn.
  * If the trajectory is already *recovering* (velocity falling), we stay quiet
    and let the system settle — intervening would fight a correction already
    underway.
  * Correction strength scales with how long drift has persisted, not with the
    raw score — pressure proportional to entrenchment.

Design contract (mirrors drift_score.py):
  * Pure stdlib. No third-party deps. Importable under Python 3.8+.
  * Total. Never raises on corrupt state; degrades to an empty trajectory.
  * Side-effect free except for explicit load()/save() against `state_path`.
  * Deterministic given the same recorded history.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Dict, List, Tuple

CONTROLLER_VERSION = "1.0.0"

# How many recent scores we keep. A short window keeps the controller responsive
# (a long-stale tail would mute a genuine recent relapse) while still giving
# velocity/trend something to chew on.
HISTORY_LEN = 10

# A turn counts as "drift" for streak/quadrant purposes when its score is at or
# above this fraction of the active threshold. We don't require the full
# verdict==drift here: a turn at 0.9*threshold is materially drifting and should
# extend a streak even if it hasn't tripped the binary verdict yet.
DRIFT_FRACTION = 0.85

# Velocity (score delta vs the previous turn) below this means the turn improved
# enough that we read the trajectory as actively recovering and hold fire.
RECOVERING_VELOCITY = -3.0

# A single spike is "adaptive" (tolerated) when only the latest turn is in drift
# and the run before it was compliant — i.e. drift streak length == 1.
ADAPTIVE_STREAK = 1


class DriftController:
    """Strategy-based controller. Proportional, occasional nudges.

    State stored as JSON in the data dir. One instance per session.
    """

    def __init__(self, state_path: str):
        self.state_path = state_path
        # history: list of {"score": float, "turn": int}, oldest first.
        self.history: List[Dict[str, float]] = []
        # Number of corrections actually emitted, and the turn of the last one.
        self.corrections_emitted: int = 0
        self.last_correction_turn: int = -(10 ** 9)
        self.version: str = CONTROLLER_VERSION

    # --- persistence ------------------------------------------------------- #

    def load(self) -> None:
        """Load state from `state_path`. Missing/corrupt state => empty start."""
        try:
            with open(self.state_path, "r", encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            return
        if not isinstance(d, dict):
            return
        hist = d.get("history")
        clean: List[Dict[str, float]] = []
        if isinstance(hist, list):
            for item in hist:
                if not isinstance(item, dict):
                    continue
                try:
                    score = float(item.get("score"))
                    turn = int(item.get("turn"))
                except (TypeError, ValueError):
                    continue
                clean.append({"score": score, "turn": turn})
        self.history = clean[-HISTORY_LEN:]
        try:
            self.corrections_emitted = int(d.get("corrections_emitted", 0))
        except (TypeError, ValueError):
            self.corrections_emitted = 0
        try:
            self.last_correction_turn = int(
                d.get("last_correction_turn", -(10 ** 9))
            )
        except (TypeError, ValueError):
            self.last_correction_turn = -(10 ** 9)

    def save(self) -> None:
        """Atomically persist state. Best-effort; never raises on IO failure."""
        payload = {
            "version": self.version,
            "history": self.history,
            "corrections_emitted": self.corrections_emitted,
            "last_correction_turn": self.last_correction_turn,
        }
        try:
            d = os.path.dirname(self.state_path) or "."
            os.makedirs(d, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".dc.", dir=d)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, separators=(",", ":"))
                os.replace(tmp, self.state_path)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError:
            return

    # --- recording --------------------------------------------------------- #

    def record_score(self, score: float, turn_num: int) -> None:
        """Add new score to history. Maintain last 10 scores."""
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = 0.0
        try:
            turn_num = int(turn_num)
        except (TypeError, ValueError):
            turn_num = (self.history[-1]["turn"] + 1) if self.history else 0
        score = max(0.0, min(100.0, score))
        # Idempotency: re-recording the same turn replaces rather than appends,
        # so a re-fired Stop hook doesn't double-count and inflate the streak.
        if self.history and int(self.history[-1]["turn"]) == turn_num:
            self.history[-1] = {"score": score, "turn": turn_num}
        else:
            self.history.append({"score": score, "turn": turn_num})
        if len(self.history) > HISTORY_LEN:
            self.history = self.history[-HISTORY_LEN:]

    # --- trajectory readouts ----------------------------------------------- #

    def _scores(self) -> List[float]:
        return [float(h["score"]) for h in self.history]

    def velocity(self) -> float:
        """Score change on the latest turn vs the one before it.

        Positive => drifting further; negative => recovering. 0 with <2 points.
        """
        s = self._scores()
        if len(s) < 2:
            return 0.0
        return round(s[-1] - s[-2], 2)

    def trend(self) -> str:
        """Coarse direction of the trajectory: 'rising' | 'falling' | 'flat'.

        Uses the average of the recent half vs the older half so a single point
        doesn't dominate — a smoother read than raw velocity for the badge.
        """
        s = self._scores()
        if len(s) < 3:
            v = self.velocity()
            if v > 1.0:
                return "rising"
            if v < -1.0:
                return "falling"
            return "flat"
        mid = len(s) // 2
        older = s[:mid]
        recent = s[mid:]
        delta = (sum(recent) / len(recent)) - (sum(older) / len(older))
        if delta > 2.0:
            return "rising"
        if delta < -2.0:
            return "falling"
        return "flat"

    def drift_streak(self, threshold: float) -> int:
        """Number of consecutive most-recent turns at/above the drift band.

        The band is DRIFT_FRACTION * threshold so a near-miss still extends the
        streak; this is what makes the controller read *sustained* slippage.
        """
        band = DRIFT_FRACTION * float(threshold)
        streak = 0
        for score in reversed(self._scores()):
            if score >= band:
                streak += 1
            else:
                break
        return streak

    def drift_rate(self, threshold: float) -> float:
        """Fraction of the kept window that sits in the drift band (0..1)."""
        s = self._scores()
        if not s:
            return 0.0
        band = DRIFT_FRACTION * float(threshold)
        return round(sum(1 for x in s if x >= band) / len(s), 3)

    def is_degenerative(self, threshold: float) -> bool:
        """Sustained, non-recovering drift — the only state that earns a nudge.

        Degenerative = a drift streak longer than a one-off spike AND the
        trajectory is not currently falling. A spike or an active recovery is
        explicitly NOT degenerative (Morin: don't fight a correction already
        underway, don't punish a one-off adaptation).
        """
        if self.drift_streak(threshold) <= ADAPTIVE_STREAK:
            return False
        if self.velocity() <= RECOVERING_VELOCITY:
            return False
        return True

    def quadrant(self, threshold: float) -> str:
        """2x2 read of (level: low/high drift) x (motion: improving/worsening).

        Returns one of: 'stable', 'recovering', 'emerging', 'entrenched'.
          * stable      — low drift, not rising. Healthy.
          * recovering  — was/at drift but velocity is pulling it down.
          * emerging    — low-ish drift but rising. Early warning.
          * entrenched  — high drift and not recovering. Worst quadrant.
        """
        s = self._scores()
        if not s:
            return "stable"
        band = DRIFT_FRACTION * float(threshold)
        high = s[-1] >= band
        recovering = self.velocity() <= RECOVERING_VELOCITY
        rising = self.trend() == "rising" or self.velocity() > 1.0
        if high and recovering:
            return "recovering"
        if high:
            return "entrenched"
        if rising:
            return "emerging"
        return "stable"

    # --- decisions --------------------------------------------------------- #

    def should_correct(
        self, threshold: float = 70.0, cooldown: int = 3
    ) -> Tuple[bool, str]:
        """Returns (should_correct, reason).

        Morin strategy rules, evaluated in order:
          1. Adaptive drift (one-off spike): tolerate, don't nudge.
          2. Recovering (velocity falling fast): hold fire, let it settle.
          3. Degenerative AND cooldown elapsed since last nudge: correct.
          4. Otherwise: don't.
        """
        if not self.history:
            return False, "no history"

        streak = self.drift_streak(threshold)

        # Rule 1: a lone spike inside an otherwise compliant run is adaptation,
        # not relapse. Correcting it boomerangs, so tolerate it.
        if 0 < streak <= ADAPTIVE_STREAK:
            return False, "adaptive drift tolerated"

        # No active drift at all => nothing to do.
        if streak == 0:
            return False, "on contract"

        # Rule 2: the trajectory is already pulling down on its own. A nudge here
        # fights a correction underway and risks over-correction.
        if self.velocity() <= RECOVERING_VELOCITY:
            return False, "recovering"

        # Rule 3: sustained, non-recovering drift. Only fire if we haven't just
        # fired — corrections must not stack turn-on-turn.
        if self.is_degenerative(threshold):
            latest_turn = int(self.history[-1]["turn"])
            since = latest_turn - self.last_correction_turn
            if since >= cooldown:
                return True, "sustained drift {} turns".format(streak)
            return False, "cooldown ({} of {} turns)".format(since, cooldown)

        return False, "below action threshold"

    def mark_corrected(self) -> None:
        """Record that a correction was emitted on the latest turn.

        Caller invokes this when it acts on a True from should_correct(), so the
        cooldown clock starts. Kept separate so the decision stays side-effect
        free and testable.
        """
        self.corrections_emitted += 1
        if self.history:
            self.last_correction_turn = int(self.history[-1]["turn"])

    def correction_strength(self, threshold: float = 70.0) -> str:
        """Returns "gentle"|"firm"|"strong" based on streak length.

        gentle = 1-2 consecutive drift turns
        firm   = 3-4 consecutive
        strong = 5+ consecutive
        """
        streak = self.drift_streak(threshold)
        if streak >= 5:
            return "strong"
        if streak >= 3:
            return "firm"
        return "gentle"

    # --- status (statusline badge) ---------------------------------------- #

    def get_status(self, threshold: float = 70.0) -> dict:
        """Returns the trajectory readout the statusline badge consumes.

        {velocity, trend, alert_level, drift_rate, is_degenerative, quadrant}
        alert_level mirrors the quadrant in badge-friendly terms:
          stable->'ok', recovering->'recovering', emerging->'watch',
          entrenched->'alert'.
        """
        quad = self.quadrant(threshold)
        alert = {
            "stable": "ok",
            "recovering": "recovering",
            "emerging": "watch",
            "entrenched": "alert",
        }.get(quad, "ok")
        return {
            "velocity": self.velocity(),
            "trend": self.trend(),
            "alert_level": alert,
            "drift_rate": self.drift_rate(threshold),
            "is_degenerative": self.is_degenerative(threshold),
            "quadrant": quad,
        }


# --------------------------------------------------------------------------- #
# CLI / self-test
# --------------------------------------------------------------------------- #


def _selftest() -> int:
    import io  # noqa: F401 — kept for parity with stdlib-only contract

    th = 70.0

    # 1) Single spike => adaptive, no correction.
    c = DriftController("/tmp/_dc_selftest_ignored")
    for i, sc in enumerate([10, 12, 9, 95]):
        c.record_score(sc, i)
    ok, reason = c.should_correct(th)
    assert ok is False and reason == "adaptive drift tolerated", (ok, reason)
    assert c.correction_strength(th) == "gentle"

    # 2) Sustained drift, cooldown elapsed => correct.
    c = DriftController("/tmp/_dc_selftest_ignored")
    for i, sc in enumerate([90, 91, 92, 93]):
        c.record_score(sc, i)
    ok, reason = c.should_correct(th, cooldown=3)
    assert ok is True and reason.startswith("sustained drift"), (ok, reason)
    assert c.correction_strength(th) == "firm"  # 4-long streak
    assert c.is_degenerative(th) is True
    assert c.quadrant(th) == "entrenched"

    # 3) After firing, cooldown blocks the next turn.
    c.mark_corrected()
    c.record_score(94, 4)
    ok, reason = c.should_correct(th, cooldown=3)
    assert ok is False and reason.startswith("cooldown"), (ok, reason)

    # 4) Recovering => hold fire even though scores are still high.
    c = DriftController("/tmp/_dc_selftest_ignored")
    for i, sc in enumerate([90, 91, 92, 80]):  # last delta -12
        c.record_score(sc, i)
    ok, reason = c.should_correct(th)
    assert ok is False and reason == "recovering", (ok, reason)
    assert c.quadrant(th) == "recovering"
    assert c.get_status(th)["alert_level"] == "recovering"

    # 5) Strong strength at a 5+ streak.
    c = DriftController("/tmp/_dc_selftest_ignored")
    for i, sc in enumerate([88, 89, 90, 91, 92]):
        c.record_score(sc, i)
    assert c.correction_strength(th) == "strong", c.correction_strength(th)

    # 6) record_score idempotent on a repeated turn number.
    c = DriftController("/tmp/_dc_selftest_ignored")
    c.record_score(50, 7)
    c.record_score(60, 7)
    assert len(c.history) == 1 and c.history[-1]["score"] == 60.0

    # 7) History capped at 10.
    c = DriftController("/tmp/_dc_selftest_ignored")
    for i in range(25):
        c.record_score(i, i)
    assert len(c.history) == HISTORY_LEN

    # 8) Round-trip persistence.
    import tempfile as _tf

    path = _tf.mktemp(prefix="dc_rt_", suffix=".json")
    c = DriftController(path)
    for i, sc in enumerate([90, 91, 92, 93]):
        c.record_score(sc, i)
    c.mark_corrected()
    c.save()
    c2 = DriftController(path)
    c2.load()
    assert c2.history == c.history
    assert c2.last_correction_turn == c.last_correction_turn
    os.unlink(path)

    # 9) Corrupt state loads as empty.
    path = _tf.mktemp(prefix="dc_bad_", suffix=".json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    c = DriftController(path)
    c.load()
    assert c.history == []
    os.unlink(path)

    print("drift_controller selftest OK")
    return 0


if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="drift-detector trajectory controller")
    ap.add_argument("--selftest", action="store_true", help="run built-in tests")
    ap.add_argument("--state", help="state JSON path to inspect")
    ap.add_argument("--threshold", type=float, default=70.0)
    args = ap.parse_args()

    if args.selftest:
        sys.exit(_selftest())

    if args.state:
        ctrl = DriftController(args.state)
        ctrl.load()
        print(json.dumps(ctrl.get_status(args.threshold), indent=2))
        sys.exit(0)

    ap.print_help()
