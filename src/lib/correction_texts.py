#!/usr/bin/env python3
"""correction_texts.py — the words the nudge actually says (stdlib only).

The controller (drift_controller.py) decides *whether* and *how hard* to nudge;
this module decides *what the nudge says*. Phrasing is itself an action in
Morin's "ecology" — too blunt and the model gets defensive or over-corrects,
too vague and it does nothing. So the text is parameterized on three axes:

  * profile   — which behavioral contract is being violated (caveman, strict…),
                so the reminder names the right target.
  * severity  — gentle / firm / strong, matching controller.correction_strength.
                gentle nudges, strong commands.
  * velocity  — if the trajectory is already recovering (velocity < 0) we switch
                to an *encouraging* register ("maintain direction") instead of
                pushing, because pushing on an in-progress recovery boomerangs.

The text is also slotted with run-time specifics — the consecutive-drift count
([N]) and the concrete offenders the scorer flagged ([specific violations]) — so
the model gets actionable signal, not a generic scold.

Design contract:
  * Pure stdlib. No deps. Total — never raises; unknown profile/severity falls
    back to a sensible default.
  * Side-effect free. Returns strings; emits nothing.
"""

from __future__ import annotations

from typing import Dict, List, Optional

# A short, plain label per profile used inside the prose. Falls back to the raw
# profile name (so a user-defined profile still reads sensibly) via .get().
PROFILE_LABEL: Dict[str, str] = {
    "caveman": "caveman",
    "strict": "strict",
    "strict-instructions": "strict-instructions",
    "relaxed": "relaxed",
    "persona": "persona",
    "custom": "custom",
}

# profile -> severity -> template. {profile}, {n}, {violations} are slotted in.
# Templates are intentionally short: this rides in additionalContext on every
# corrected turn, so token economy is part of staying on-contract itself.
CORRECTION_TEXTS: Dict[str, Dict[str, str]] = {
    "caveman": {
        "gentle": "Drift detected. Minor tightening needed. Back to {profile}: "
        "short words, no filler.",
        "firm": "Drifting from {profile} contract. Re-tighten: {violations}. "
        "Caveman talk: terse, telegraphic, no hedging.",
        "strong": "SUSTAINED DRIFT {n} turns. Return to {profile} immediately. "
        "Drop all of: {violations}. One idea per line. Grunt, don't lecture.",
    },
    "strict": {
        "gentle": "Drift detected. Minor tightening needed. Hold the {profile} "
        "output contract.",
        "firm": "Drifting from {profile} contract. Re-tighten: {violations}. "
        "Cut preamble and qualifiers.",
        "strong": "SUSTAINED DRIFT {n} turns. Return to {profile} immediately. "
        "Eliminate: {violations}.",
    },
    "persona": {
        "gentle": "Drift detected. Minor tightening needed. Stay in the "
        "{profile} voice.",
        "firm": "Drifting from {profile} contract. Re-tighten: {violations}. "
        "Hold the persona's register.",
        "strong": "SUSTAINED DRIFT {n} turns. Return to {profile} immediately. "
        "The persona broke on: {violations}.",
    },
}

# Used when the profile has no bespoke entry. {profile} keeps it on-target.
DEFAULT_TEXTS: Dict[str, str] = {
    "gentle": "Drift detected. Minor tightening needed. Re-align to the "
    "{profile} contract.",
    "firm": "Drifting from {profile} contract. Re-tighten: {violations}.",
    "strong": "SUSTAINED DRIFT {n} turns. Return to {profile} immediately. "
    "Fix: {violations}.",
}

# When velocity < 0 the trajectory is already improving. We acknowledge it and
# ask the model to hold course rather than push harder — pushing on a recovery
# is the boomerang Morin warns about.
RECOVERING_TEXTS: Dict[str, str] = {
    "gentle": "On the {profile} contract and recovering — maintain direction.",
    "firm": "Recovering toward the {profile} contract — maintain direction. "
    "Still loose on: {violations}.",
    "strong": "Recovering after {n} drift turns — maintain direction, don't "
    "snap back. Watch: {violations}.",
}

_FALLBACK = "Drift detected. Re-align to the active output contract."


def _norm_severity(severity: Optional[str]) -> str:
    s = (severity or "").strip().lower()
    return s if s in ("gentle", "firm", "strong") else "gentle"


def _fmt_violations(violations: Optional[List[str]]) -> str:
    """Render the scorer's top offenders into a compact clause.

    Accepts the engine's `top_offenders` (e.g. ["hype (+18.2)", "filler (+9)"])
    or a plain list of category names. Empty/None => a generic stand-in so the
    {violations} slot never renders awkwardly.
    """
    if not violations:
        return "verbosity, hedging, filler"
    cleaned: List[str] = []
    for v in violations:
        if v is None:
            continue
        text = str(v).strip()
        if not text:
            continue
        # Drop the "(+12.3)" contribution suffix the engine appends, keep label.
        label = text.split(" (")[0].strip()
        if label and label not in cleaned:
            cleaned.append(label)
    if not cleaned:
        return "verbosity, hedging, filler"
    return ", ".join(cleaned[:4])


def correction_text(
    profile: str,
    severity: str,
    *,
    velocity: float = 0.0,
    streak: int = 0,
    violations: Optional[List[str]] = None,
) -> str:
    """Build the nudge string for a given profile/severity/trajectory.

    profile    — active profile name (keys CORRECTION_TEXTS; unknown => default).
    severity   — "gentle"|"firm"|"strong" (from controller.correction_strength).
    velocity   — latest score delta; < 0 switches to the recovering register.
    streak     — consecutive drift turns; fills [N] in strong/recovering text.
    violations — scorer offenders (top_offenders or category names) for [violations].
    """
    prof_name = (str(profile).strip() or "active")[:64]
    label = PROFILE_LABEL.get(prof_name, prof_name)
    sev = _norm_severity(severity)
    viol = _fmt_violations(violations)
    try:
        n = int(streak)
    except (TypeError, ValueError):
        n = 0

    try:
        vel = float(velocity)
    except (TypeError, ValueError):
        vel = 0.0

    if vel < 0:
        template = RECOVERING_TEXTS.get(sev, RECOVERING_TEXTS["gentle"])
    else:
        template = CORRECTION_TEXTS.get(prof_name, {}).get(sev) or DEFAULT_TEXTS.get(
            sev, DEFAULT_TEXTS["gentle"]
        )

    try:
        return template.format(profile=label, n=n, violations=viol)
    except (KeyError, IndexError, ValueError):
        return _FALLBACK


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #


def _selftest() -> int:
    # Known profile, each severity, rising trajectory.
    g = correction_text("caveman", "gentle", velocity=2.0, streak=1)
    f = correction_text(
        "caveman",
        "firm",
        velocity=4.0,
        streak=3,
        violations=["hype (+18)", "filler (+9)"],
    )
    s = correction_text(
        "caveman",
        "strong",
        velocity=1.0,
        streak=6,
        violations=["verbosity (+30)"],
    )
    assert "Minor tightening" in g, g
    assert "hype" in f and "filler" in f, f
    assert "6 turns" in s and "verbosity" in s, s

    # Recovering register kicks in on negative velocity regardless of severity.
    r = correction_text("caveman", "strong", velocity=-8.0, streak=4)
    assert "recovering" in r.lower() and "maintain direction" in r.lower(), r

    # Unknown profile falls back to DEFAULT_TEXTS but still names the profile.
    u = correction_text("my-weird-profile", "firm", velocity=3.0)
    assert "my-weird-profile" in u, u

    # Garbage severity normalizes to gentle; never raises.
    x = correction_text("strict", "WAT", velocity=0.0)
    assert "Minor tightening" in x or "Re-align" in x, x

    # Empty violations => generic stand-in, no dangling slot.
    e = correction_text("strict", "firm", velocity=1.0, violations=[])
    assert "{" not in e and "verbosity" in e, e

    print("correction_texts selftest OK")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_selftest())
