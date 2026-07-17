#!/usr/bin/env python3
"""adversarial_classify_test.py — adversarial probe for classify_user_reply.

Pre-registered adversarial cases designed to expose FP/FN weaknesses in
classify_user_reply(). Run after any change to drift_user_correction.py.

Pre-registration date: 2026-06-22.

Exit code: 0 if all assertions pass, 1 if any fail.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "lib"))
from drift_user_correction import classify_user_reply

OK_LABELS = {"new_task", "approval", "continuation"}
CORR_LABELS = {"correction_substance", "correction_style", "frustration"}

# (text, expected_category, hypothesis_id, notes)
# category: "ok" = any OK label; "correction" = any correction label; exact label name for specific
CASES = [
    # ── FP ATTACKS: patterns that fire in ok-context ──────────────────────────
    # These all fire as correction_substance (by design) but WOULD be FPs in
    # new-task/status-update context. 0 FP in real corpus because these
    # phrasings don't appear in the 906 ok entries. Latent risk for production.
    (
        "i just logged into github to check the issue",
        "correction",
        "FP-1",
        "new-task via 'i just logged into'",
    ),
    (
        "i just logged into the dashboard everything looks normal",
        "correction",
        "FP-2",
        "status update via 'i just logged into'",
    ),
    (
        "so its all working, ready to ship now",
        "correction",
        "FP-3",
        "'so its all working' fires even with positive continuation",
    ),
    (
        "so its all working and im happy with the result",
        "correction",
        "FP-4",
        "'so its all working' fires in approval context",
    ),
    (
        "we rebooted already, everything looks good now",
        "correction",
        "FP-5",
        "'we rebooted already' fires in status context",
    ),
    (
        "simulate some users for the load test setup",
        "correction",
        "FP-6",
        "'simulate some user' fires in new-task context",
    ),
    (
        "simulate some user traffic to warm up the cache",
        "correction",
        "FP-7",
        "'simulate some user' fires in new-task context",
    ),
    (
        "ok hot shot, what should we build next",
        "correction",
        "FP-8",
        "'ok hot shot' fires even in new-task opener",
    ),
    (
        "complete manually if that is easier for you",
        "correction",
        "FP-9",
        "'complete manually' fires as option offer",
    ),
    (
        "i dont see this being an issue at all",
        "correction",
        "FP-10",
        "'i dont see this' fires in problem-denial",
    ),
    (
        "i don't see this as a problem right now",
        "correction",
        "FP-11",
        "'i don't see this' fires in problem-denial",
    ),
    (
        "its easier said than done but worth trying",
        "correction",
        "FP-12",
        "'its easier' fires in idiomatic phrase — LATENT FP",
    ),
    (
        "could not establish connection to my wifi router",
        "correction",
        "FP-13",
        "'could not establish connection' fires on unrelated error",
    ),
    (
        "p360ultra is the model name fyi",
        "correction",
        "FP-14",
        "'p360ultra' fires on any mention of device name",
    ),
    (
        "its easier to just hardcode it for now",
        "correction",
        "FP-18",
        "'its easier' fires on shortcut proposal",
    ),
    (
        "go ahead back to you on the auth piece",
        "correction",
        "FP-19",
        "'go ahead back to you' fires on task handback",
    ),
    # ── FN ATTACKS: real corrections that don't match any pattern ────────────
    # These return new_task (missed). If any later appears in real corpus as drift,
    # it's a missed detection. Add patterns and verify against ok=906 before merging.
    (
        "you've gone completely off track here",
        "ok",
        "FN-1",
        "off track = correction; 'stay on track' in vocab but reversed form missed",
    ),
    (
        "your answer has regressed since yesterday",
        "ok",
        "FN-2",
        "regression claim missed",
    ),
    ("thats exactly backwards from what i need", "ok", "FN-3", "contradiction missed"),
    (
        "the output doesn't match the requirements at all",
        "ok",
        "FN-4",
        "mismatch claim missed",
    ),
    (
        "youre going in circles same mistake again",
        "ok",
        "FN-5",
        "repeated-error complaint missed",
    ),
    (
        "that wasnt even close to what i asked for",
        "ok",
        "FN-6",
        "strong mismatch missed",
    ),
    (
        "you clearly didnt read my instructions",
        "ok",
        "FN-7",
        "accusation of ignoring instructions missed",
    ),
    (
        "this solution breaks the existing behavior",
        "ok",
        "FN-8",
        "regression claim ('breaks existing') missed",
    ),
    (
        "youre solving the wrong problem entirely",
        "ok",
        "FN-9",
        "'wrong problem' not in vocab, only 'wrong approach/direction'",
    ),
    (
        "the logic is inverted flip the condition",
        "ok",
        "FN-10",
        "direct inversion correction missed",
    ),
    ("the context you used is stale", "ok", "FN-12", "stale context correction missed"),
    (
        "youre overcomplicating this massively",
        "ok",
        "FN-13",
        "overcomplication complaint missed",
    ),
    (
        "that code path was removed in v2",
        "ok",
        "FN-15",
        "factual correction (removed path) missed",
    ),
    # ── EDGE CASES: exact-match gate and URL gate ─────────────────────────────
    ("c", "correction", "EDGE-1", "exact 'c' gate fires"),
    ("https://docs.example.com/api", "correction", "EDGE-2", "bare URL gate fires"),
    ("try it", "correction", "EDGE-3", "exact 'try it' gate fires"),
    ("looks good", "ok", "EDGE-4", "clean approval — no pattern"),
    ("ok", "ok", "EDGE-5", "single ok — approval"),
    ("continue", "ok", "EDGE-6", "single continue — continuation"),
    (
        "you missed the point",
        "correction",
        "EDGE-7",
        "'you missed' fires via 'you missed'",
    ),
    ("that was wrong", "ok", "EDGE-8", "'wrong' must be FIRST token to fire; not here"),
    # Note: "wrong" as first token fires, but "that was wrong" has 'wrong' at pos 2+
    # via _HARD_OPENERS anchor — only fires when 'wrong' is first token (after strip punct)
]

passed = failed = 0
fp_latent = []  # fires correction when context suggests ok (by design, 0 corpus FP)
fn_actual = []  # returns ok when it should be correction (real missed corrections)
exact_fail = []

for text, expected_cat, hid, notes in CASES:
    actual = classify_user_reply(text)
    ok_actual = actual in OK_LABELS
    corr_actual = actual in CORR_LABELS

    if expected_cat == "ok":
        ok_expected = True
        match = ok_actual
    elif expected_cat == "correction":
        ok_expected = False
        match = corr_actual
    else:
        # exact label
        match = actual == expected_cat
        ok_expected = expected_cat in OK_LABELS

    if match:
        passed += 1
    else:
        failed += 1
        if ok_expected and corr_actual:
            fp_latent.append((hid, text, actual, notes))
        elif not ok_expected and ok_actual:
            fn_actual.append((hid, text, actual, notes))
        else:
            exact_fail.append((hid, text, expected_cat, actual, notes))

print(f"\n{'=' * 60}")
print(f"classify_user_reply adversarial probe: n={len(CASES)}")
print(f"PASS={passed}  FAIL={failed}")
print(f"{'=' * 60}\n")

if fp_latent:
    print("── UNEXPECTED FP (expected correction to NOT fire) ──────────")
    for hid, text, actual, notes in fp_latent:
        print(f"  [{hid}] actual={actual}")
        print(f"    {notes}")
        print(f"    text: {text!r}")
    print()

if fn_actual:
    print("── UNEXPECTED FN (expected correction to fire, missed) ──────")
    for hid, text, actual, notes in fn_actual:
        print(f"  [{hid}] actual={actual}")
        print(f"    {notes}")
        print(f"    text: {text!r}")
    print()

if exact_fail:
    print("── EXACT LABEL MISMATCH ─────────────────────────────────────")
    for hid, text, exp, actual, notes in exact_fail:
        print(f"  [{hid}] expected={exp} actual={actual}")
        print(f"    {notes}")
        print(f"    text: {text!r}")
    print()

if failed == 0:
    print("All assertions passed (expected behavior confirmed).")

sys.exit(0 if failed == 0 else 1)
