"""pytest suite for the drift scoring engine (src/lib/drift_score.py).

Pure-stdlib engine, so these tests have no third-party deps beyond pytest
itself. We locate the engine by adding src/lib to sys.path.
"""

import os
import sys

# Make the stdlib-only engine importable without installing the plugin.
_SRC_LIB = os.path.join(os.path.dirname(__file__), "..", "src", "lib")
sys.path.insert(0, os.path.abspath(_SRC_LIB))

from drift_score import Profile, analyze  # noqa: E402


def test_empty_string_scores_zero():
    r = analyze("", Profile())
    assert r.score == 0.0
    assert r.verdict == "ok"


def test_none_input_scores_zero():
    r = analyze(None, Profile())  # type: ignore[arg-type]
    assert r.score == 0.0
    assert r.verdict == "ok"


def test_terse_compliant_turn_is_ok():
    r = analyze("Bug fixed.", Profile())
    assert r.score < 30
    assert r.verdict == "ok"


def test_hedge_heavy_turn_is_drift():
    # Under a twitchier profile this default-assistant relapse must register as
    # drift (score > 70). The phrase is dense with filler/politeness markers.
    twitchy = Profile.from_dict({"sensitivity": 1.5})
    r = analyze("Sure! I'd be happy to help you with that!", twitchy)
    assert r.score > 70, r.score
    assert r.verdict == "drift"


def test_code_block_hedges_are_excluded():
    # Hedge-laden text inside a fenced code block must not count: a caveman is
    # allowed to emit normal code. Only the prose ("fixed it.") is scored.
    codey = (
        "```python\n"
        "perhaps maybe basically essentially i think it seems arguably\n"
        "```\n"
        "fixed it."
    )
    r = analyze(codey, Profile())
    assert r.score < 30, r.score
    assert r.verdict == "ok"


def test_determinism_same_text_same_score():
    text = "Certainly! Let me walk you through this powerful, seamless solution."
    first = analyze(text, Profile()).score
    second = analyze(text, Profile()).score
    assert first == second


def test_from_dict_none_returns_default_profile():
    prof = Profile.from_dict(None)
    default = Profile()
    assert prof.name == default.name
    assert prof.threshold == default.threshold
    assert prof.sensitivity == default.sensitivity


def test_custom_profile_threshold_changes_verdict():
    text = "Sure! I'd be happy to help you with that!"
    score = analyze(text, Profile()).score
    # Same text, two thresholds straddling the score => opposite verdicts.
    lenient = analyze(text, Profile.from_dict({"threshold": score + 10})).verdict
    strict = analyze(text, Profile.from_dict({"threshold": score - 10})).verdict
    assert lenient == "ok"
    assert strict == "drift"
