"""pytest suite for the detector features added during the lap 1-10 tuning rounds.

Each test pins one behavior that a scientific-method lap introduced and that the
170-session Morin eval now depends on. Like ``test_drift_score.py`` these are
stdlib-only — we add ``src/lib`` to ``sys.path`` so the engine is importable
without installing the plugin, and load the shipped caveman profile from disk so
the lexical test scores against the same contract the plugin uses in production.
"""

import json
import os
import sys

# Make the stdlib-only engine importable without installing the plugin.
_HERE = os.path.dirname(__file__)
_SRC_LIB = os.path.abspath(os.path.join(_HERE, "..", "src", "lib"))
sys.path.insert(0, _SRC_LIB)

from drift_score import score_text  # noqa: E402
from drift_trajectory import compute_session_score  # noqa: E402
from drift_type import classify_drift  # noqa: E402

_PROFILES = os.path.abspath(os.path.join(_HERE, "..", "profiles"))


def _caveman_profile():
    with open(os.path.join(_PROFILES, "caveman.json"), encoding="utf-8") as fh:
        return json.load(fh)


def test_severity_spike_override():
    """Lap: an extreme spike (>= 1.35x threshold) forces a degenerative verdict
    even when the last two prior turns are clean. Magnitude is its own signal —
    the isolated-spike adaptive path must not swallow a 96.5 reading.
    """
    c = classify_drift(96.5, [0.0, 15.6, 0.0])
    assert c.drift_type == "degenerative", c
    assert c.should_correct is True, c


def test_consistent_high_subclinical():
    """Lap 7: a session whose non-zero turns cluster tightly just under threshold
    (nonzero_mean > 85% of threshold, range < 25% of threshold) is degenerative
    even though interleaved clean (zero) turns keep the tail-mean chronic gate
    unreachable. This is the zero-masked narrow-band-high case (sev_04b).
    """
    s = compute_session_score([64.0, 0.0, 65.5, 65.5, 0.0], threshold=70.0)
    assert s["is_degenerative"] is True, s


def test_repeating_spike_degenerate():
    """Lap 8: >= 3 threshold crossings over a >= 6 turn session is an oscillating
    relapse — adaptive turn-by-turn, degenerative as a cycle. The
    repeating_spike_degenerate flag surfaces it past the adaptive gate
    (rep_01 + rep_02).
    """
    s = compute_session_score([77.8, 0.0, 74.2, 0.0, 77.8, 0.0], threshold=70.0)
    assert s["repeating_spike_degenerate"] is True, s


def test_long_session_repeating_spike():
    """Lap 9: extended repeating-spike condition for longer sessions — >= 2 turns
    over threshold AND >= 4 high-subclinical turns (>= 90% of threshold) over a
    >= 8 turn run also reads as a repeating-spike degenerate cycle (long_04).
    """
    s = compute_session_score(
        [67.9, 0.0, 67.1, 0.0, 71.4, 0.0, 67.9, 0.0, 75.6, 0.0, 55.9, 24.9]
    )
    assert s["repeating_spike_degenerate"] is True, s


def test_ordinal_enumeration_scored():
    """Lap: comma-delimited ordinal-enumeration scaffolding ("First, the ...",
    "Second, it ...") is a filler tell of an assistant narrating a formal numbered
    walkthrough — structured prose that departs from the terse caveman register.
    It must register positive drift against the shipped caveman profile.
    """
    pd = _caveman_profile()
    r = score_text(
        "First, the migration acquires an advisory lock on the target table. "
        "Second, it copies the rows.",
        pd,
    )
    assert r["score"] > 0, r["score"]
