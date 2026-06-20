#!/usr/bin/env python3
"""control.py — hook-facing bridge to the trajectory controller (stdlib only).

The bash hooks can score a turn (score.py) but cannot carry the *trajectory*
state Morin's strategy needs. This script is the thin, total, never-raising
bridge between the hooks and the stateful Python modules:

  * ``record``  — capture-stop.sh calls this after scoring. It records the
    turn's drift score into the per-session DriftController, computes the
    dialogic (fidelity/engagement) badge for the turn, and prints a single
    badge line the statusline consumes:

        <state>:<vel>|<pct>[|<dialogic>]

    where state is ok|warn|drift and vel is rising|falling|stable (the
    trajectory direction, classified by per-turn velocity with a +/-5/turn
    dead-band). E.g. ``drift:rising|72|DRIFT:72 eng:40`` (climbing into drift)
    or ``ok:stable|12|fit:88 eng:72`` (healthy and holding).

  * ``decide``  — inject-correction.sh calls this before the next prompt. It
    loads the controller, asks should_correct(), and on True prints the
    proportional correction text (from correction_texts) on stdout and marks
    the correction so the cooldown clock starts. On False it prints nothing.

Both subcommands exit 0 no matter what — drift control must never break a
session. State lives in a per-session JSON file under the controller dir.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Velocity dead-band (drift-points/turn) for the badge arrow. |velocity| within
# this reads as "stable" (->); above it as rising (^) / falling (v).
_VEL_BAND = 5.0

_HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in (
    os.path.join(_HERE, "..", "src", "lib"),
    os.path.join(_HERE, "..", "lib"),
    _HERE,
):
    if os.path.isfile(os.path.join(_cand, "drift_controller.py")):
        sys.path.insert(0, os.path.abspath(_cand))
        break

try:
    from drift_controller import DriftController
    from drift_dialogic import compute_dialogic_score
    from correction_texts import correction_text
except Exception:  # noqa: BLE001 — any import failure degrades to a no-op.
    DriftController = None  # type: ignore[assignment]
    compute_dialogic_score = None  # type: ignore[assignment]
    correction_text = None  # type: ignore[assignment]


def _read_text(transcript: str, text: str) -> str:
    """Best-effort last-assistant-turn text for the dialogic reading."""
    if text:
        return text
    if not transcript or not os.path.isfile(transcript):
        return ""
    try:
        # Reuse score.py's robust extractor without re-running the scorer.
        import score  # type: ignore

        return score.last_assistant_turn(transcript)
    except Exception:  # noqa: BLE001
        return ""


def _next_turn(ctrl) -> int:
    """A stable turn index: the DB rollup count when given, else history+1.

    capture-stop passes --turn from the session rollup so re-fired Stop hooks
    land on the same index and record_score dedupes them.
    """
    if ctrl.history:
        return int(ctrl.history[-1]["turn"]) + 1
    return 0


def cmd_record(args) -> int:
    if DriftController is None or compute_dialogic_score is None:
        # Degrade to the legacy badge so the statusline still gets something.
        print("{}:{}|{}".format(args.state or "ok", "stable", args.pct))
        return 0

    ctrl = DriftController(args.state_path)
    ctrl.load()

    turn = args.turn if args.turn is not None and args.turn >= 0 else _next_turn(ctrl)
    ctrl.record_score(args.score, turn)
    ctrl.save()

    status = ctrl.get_status(args.threshold)
    # alert_level: ok|recovering|watch|alert  -> badge state token.
    alert = status["alert_level"]
    state = {
        "ok": "ok",
        "recovering": "drift",
        "watch": "warn",
        "alert": "drift",
    }.get(alert, args.state or "ok")

    # Velocity token (the badge's trajectory arrow). The statusline maps
    # rising->up, falling->down, stable->flat. We classify by per-turn velocity
    # with a +/-5 points/turn dead-band so float noise reads as "stable" rather
    # than flapping the arrow. A "recovering" quadrant always reads "falling":
    # the controller has decided the series is pulling back off a high reading,
    # so the badge should show the down arrow regardless of the latest delta.
    velocity = ctrl.velocity()
    if alert == "recovering":
        vel = "falling"
    elif velocity > _VEL_BAND:
        vel = "rising"
    elif velocity < -_VEL_BAND:
        vel = "falling"
    else:
        vel = "stable"

    badge = "{}:{}|{}".format(state, vel, args.pct)

    text = _read_text(args.transcript, args.text)
    if text:
        try:
            d = compute_dialogic_score(args.score, text, args.threshold)
            badge = "{}|{}".format(badge, d.badge)
        except Exception:  # noqa: BLE001
            pass

    print(badge)
    return 0


def cmd_decide(args) -> int:
    if DriftController is None or correction_text is None:
        return 0  # no controller => never inject (fail safe / quiet).

    ctrl = DriftController(args.state_path)
    ctrl.load()

    should, _reason = ctrl.should_correct(args.threshold, args.cooldown)
    if not should:
        return 0

    severity = ctrl.correction_strength(args.threshold)
    velocity = ctrl.velocity()
    streak = ctrl.drift_streak(args.threshold)

    violations = []
    if args.offenders:
        violations = [v for v in args.offenders.split("\n") if v.strip()]

    msg = correction_text(
        args.profile,
        severity,
        velocity=velocity,
        streak=streak,
        violations=violations or None,
    )

    ctrl.mark_corrected()
    ctrl.save()
    sys.stdout.write(msg)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="drift controller hook bridge")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("record")
    r.add_argument("--state-path", required=True)
    r.add_argument("--score", type=float, default=0.0)
    r.add_argument("--pct", default="0")
    r.add_argument("--state", default="ok")
    r.add_argument("--turn", type=int, default=None)
    r.add_argument("--threshold", type=float, default=70.0)
    r.add_argument("--transcript", default="")
    r.add_argument("--text", default="")
    r.set_defaults(func=cmd_record)

    d = sub.add_parser("decide")
    d.add_argument("--state-path", required=True)
    d.add_argument("--profile", default="caveman")
    d.add_argument("--threshold", type=float, default=70.0)
    d.add_argument("--cooldown", type=int, default=3)
    d.add_argument("--offenders", default="")
    d.set_defaults(func=cmd_decide)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — last-resort guard; never break a hook.
        sys.stderr.write("control.py: {}\n".format(exc))
        sys.exit(0)
