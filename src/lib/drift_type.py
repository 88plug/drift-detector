"""Adaptive vs degenerative drift classifier (Edgar Morin improvement #3).

Morin principle: "well-ordered perturbation" -- not all drift is decay.

  Adaptive drift    = context-appropriate, reversible departure that serves
                      the task. An isolated spike surrounded by compliance, or
                      a minor transgression off a strong baseline. Tolerate it.
  Degenerative drift = slow entropic relapse into generic verbosity carrying
                      no information; sustained departure with no return to
                      baseline. Suppress this.

Only degenerative drift is corrected. Correction is further gated by an
"ecology of action" cooldown so intervention stays occasional and
proportional -- a strategy, not a program.

Pure stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean


@dataclass
class DriftClassification:
    drift_type: str  # "none" | "adaptive" | "degenerative"
    should_correct: bool  # True only for degenerative
    confidence: float  # 0.0-1.0
    rationale: str


def classify_drift(
    current_score: float,
    recent_scores: list[float],  # last 5 turns before current
    threshold: float = 70.0,
) -> DriftClassification:
    """Classify drift as adaptive or degenerative.

    Higher score == more drift; ``threshold`` is the line above which a turn
    counts as drifting.

    Rules (in priority order):
      1. current_score < threshold -> "none", should_correct=False.
      2. current_score >= threshold AND >=2 turns of history:
         - both of the last 2 turns below threshold -> ISOLATED spike,
           "adaptive" (one-off, surrounded by compliance).
         - any of the last 2 turns at/above threshold -> SUSTAINED,
           "degenerative".
         - strong baseline (mean(recent) < threshold*0.6) and only a minor
           transgression now (current < threshold*1.1) -> "adaptive".
      3. current_score >= threshold AND <2 turns of history -> "degenerative"
         (insufficient history, default to correct).

    confidence = abs(current_score - threshold) / threshold, clamped to
    [0.0, 1.0]: distance from the decision boundary.
    """
    confidence = min(1.0, abs(current_score - threshold) / threshold)

    # Rule 1: not drifting at all.
    if current_score < threshold:
        return DriftClassification(
            drift_type="none",
            should_correct=False,
            confidence=confidence,
            rationale=(
                f"current {current_score:.1f} < threshold {threshold:.1f}: "
                "compliant, no drift"
            ),
        )

    # Rule 2: drifting, with enough history to judge the trajectory.
    if len(recent_scores) >= 2:
        last_two = recent_scores[-2:]
        baseline = mean(recent_scores)

        if all(s < threshold for s in last_two):
            # Severity override: an extreme spike (>=1.35x threshold) is never a
            # benign one-off. Force correction regardless of surrounding
            # compliance -- the magnitude itself is the signal.
            if current_score >= threshold * 1.35:
                return DriftClassification(
                    drift_type="degenerative",
                    should_correct=True,
                    confidence=confidence,
                    rationale=(
                        f"severity override: score {current_score:.1f} >= "
                        f"{threshold * 1.35:.1f} (1.35x threshold), extreme spike "
                        "forces correction regardless of isolation"
                    ),
                )
            # Strong baseline + minor transgression refines an isolated spike
            # into a higher-confidence adaptive call.
            if baseline < threshold * 0.6 and current_score < threshold * 1.1:
                return DriftClassification(
                    drift_type="adaptive",
                    should_correct=False,
                    confidence=confidence,
                    rationale=(
                        f"isolated spike (last 2 {last_two} below threshold) "
                        f"and minor transgression (current {current_score:.1f} "
                        f"< {threshold * 1.1:.1f}) off strong baseline "
                        f"(mean {baseline:.1f} < {threshold * 0.6:.1f}): "
                        "well-ordered perturbation, tolerate"
                    ),
                )
            return DriftClassification(
                drift_type="adaptive",
                should_correct=False,
                confidence=confidence,
                rationale=(
                    f"isolated spike: last 2 turns {last_two} both below "
                    f"threshold {threshold:.1f}, one-off departure surrounded "
                    "by compliance, tolerate"
                ),
            )

        # any() of the last two at/above threshold -> sustained.
        return DriftClassification(
            drift_type="degenerative",
            should_correct=True,
            confidence=confidence,
            rationale=(
                f"sustained drift: last 2 turns {last_two}, at least one "
                f"at/above threshold {threshold:.1f}, entropic relapse, correct"
            ),
        )

    # Rule 3: drifting but too little history to tell adaptive from
    # degenerative -- default to correcting.
    return DriftClassification(
        drift_type="degenerative",
        should_correct=True,
        confidence=confidence,
        rationale=(
            f"current {current_score:.1f} >= threshold {threshold:.1f} with "
            f"only {len(recent_scores)} prior turn(s): insufficient history, "
            "default to correct"
        ),
    )


def should_inject_correction(
    current_score: float,
    recent_scores: list[float],
    last_correction_turn: int,
    current_turn: int,
    cooldown_turns: int = 3,
    threshold: float = 70.0,
) -> bool:
    """Ecology of action: inject a correction only when warranted AND cooled down.

    Returns True only if the drift is degenerative AND at least
    ``cooldown_turns`` have elapsed since ``last_correction_turn``. This keeps
    intervention occasional and proportional -- Morin's "strategy not program".

    A ``last_correction_turn`` of a negative value (e.g. -1) means no prior
    correction; the cooldown is then trivially satisfied.
    """
    classification = classify_drift(current_score, recent_scores, threshold)
    if not classification.should_correct:
        return False

    cooled_down = (current_turn - last_correction_turn) >= cooldown_turns
    return cooled_down


if __name__ == "__main__":
    threshold = 70.0

    def show(label: str, c: DriftClassification) -> None:
        print(f"  [{label}]")
        print(
            f"    type={c.drift_type} correct={c.should_correct} "
            f"conf={c.confidence:.2f}"
        )
        print(f"    {c.rationale}")

    print("=== classify_drift selftest ===\n")

    # 1. No drift: compliant turn.
    show("none", classify_drift(40.0, [30.0, 35.0, 42.0, 38.0, 41.0]))

    # 2. Adaptive: isolated spike surrounded by compliance.
    show(
        "adaptive (isolated spike)",
        classify_drift(85.0, [30.0, 35.0, 40.0, 38.0, 33.0]),
    )

    # 3. Adaptive: minor transgression off a strong baseline.
    show(
        "adaptive (minor off strong baseline)",
        classify_drift(74.0, [20.0, 25.0, 22.0, 28.0, 24.0]),
    )

    # 4. Degenerative: sustained drift, no return to baseline.
    show(
        "degenerative (sustained)", classify_drift(88.0, [72.0, 78.0, 81.0, 85.0, 84.0])
    )

    # 5. Degenerative: drifting with too little history.
    show("degenerative (no history)", classify_drift(90.0, [88.0]))

    assert classify_drift(40.0, [30.0, 35.0]).drift_type == "none"
    assert classify_drift(85.0, [30.0, 33.0]).drift_type == "adaptive"
    assert classify_drift(74.0, [20.0, 25.0, 22.0]).drift_type == "adaptive"
    assert classify_drift(88.0, [72.0, 85.0]).drift_type == "degenerative"
    assert classify_drift(90.0, [88.0]).drift_type == "degenerative"
    assert not classify_drift(85.0, [30.0, 33.0]).should_correct
    assert classify_drift(88.0, [72.0, 85.0]).should_correct

    print("\n=== should_inject_correction selftest ===\n")

    # Adaptive drift -> never inject, regardless of cooldown.
    r1 = should_inject_correction(
        current_score=85.0,
        recent_scores=[30.0, 33.0],
        last_correction_turn=-1,
        current_turn=10,
        cooldown_turns=3,
    )
    print(f"  adaptive drift, no prior correction -> inject={r1} (want False)")
    assert r1 is False

    # Degenerative drift, cooldown elapsed -> inject.
    r2 = should_inject_correction(
        current_score=88.0,
        recent_scores=[72.0, 85.0],
        last_correction_turn=5,
        current_turn=10,
        cooldown_turns=3,
    )
    print(f"  degenerative drift, cooldown elapsed -> inject={r2} (want True)")
    assert r2 is True

    # Degenerative drift, still in cooldown -> suppress.
    r3 = should_inject_correction(
        current_score=88.0,
        recent_scores=[72.0, 85.0],
        last_correction_turn=9,
        current_turn=10,
        cooldown_turns=3,
    )
    print(f"  degenerative drift, in cooldown    -> inject={r3} (want False)")
    assert r3 is False

    # Degenerative drift, no prior correction -> inject.
    r4 = should_inject_correction(
        current_score=90.0,
        recent_scores=[88.0],
        last_correction_turn=-1,
        current_turn=10,
        cooldown_turns=3,
    )
    print(f"  degenerative drift, no prior corr  -> inject={r4} (want True)")
    assert r4 is True

    print("\nall selftests passed")
