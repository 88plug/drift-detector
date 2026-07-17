#!/usr/bin/env python3
"""profiles.py — profile load / validate / merge for drift-detector (stdlib).

A profile is a JSON document describing a behavioral contract and how to score
deviation from it. Profiles resolve in priority order:

    1. user dir   ($CLAUDE_PLUGIN_DATA/profiles/<name>.json)   — writable
    2. bundled    (<plugin>/profiles/<name>.json)              — read-only

A user profile may declare  "extends": "<name>"  to inherit a bundled profile
and override only some fields (deep-merged). `sensitivity` is applied by the
scoring engine, not here — this module only validates/normalizes it.

Used by the /drift-detector:profile command and (indirectly) by score.py via the JSON
file it loads. Kept dependency-free.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Tuple

VALID_KINDS = ("lexical", "judge")


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _candidate_paths(name: str, user_dir: Optional[str], bundled_dir: str) -> List[str]:
    paths = []
    if user_dir:
        paths.append(os.path.join(user_dir, f"{name}.json"))
    paths.append(os.path.join(bundled_dir, f"{name}.json"))
    return paths


def _load_raw(name: str, user_dir: Optional[str], bundled_dir: str) -> Optional[dict]:
    for p in _candidate_paths(name, user_dir, bundled_dir):
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    d = json.load(fh)
                if isinstance(d, dict):
                    return d
            except (OSError, json.JSONDecodeError):
                continue
    return None


def load(
    name: str,
    user_dir: Optional[str] = None,
    bundled_dir: str = "",
    _seen: Optional[set] = None,
) -> Tuple[Optional[dict], List[str]]:
    """Load and resolve a profile by name. Returns (profile_dict, errors).

    Resolves `extends` chains (cycle-safe), deep-merging child over parent.
    """
    _seen = _seen or set()
    if name in _seen:
        return None, [f"profile inheritance cycle at '{name}'"]
    _seen.add(name)

    raw = _load_raw(name, user_dir, bundled_dir)
    if raw is None:
        return None, [f"profile '{name}' not found"]

    errors: List[str] = []
    parent_name = raw.get("extends")
    if parent_name:
        parent, perrs = load(parent_name, user_dir, bundled_dir, _seen)
        errors.extend(perrs)
        if parent:
            raw = _deep_merge(parent, {k: v for k, v in raw.items() if k != "extends"})

    raw.setdefault("name", name)
    return raw, errors


def validate(profile: dict) -> List[str]:
    """Return a list of human-readable problems; empty list == valid."""
    errs: List[str] = []
    if not isinstance(profile, dict):
        return ["profile is not an object"]

    name = profile.get("name")
    if not isinstance(name, str) or not name.strip():
        errs.append("'name' must be a non-empty string")

    kind = profile.get("kind", "lexical")
    if kind not in VALID_KINDS:
        errs.append(f"'kind' must be one of {VALID_KINDS} (got {kind!r})")

    thr = profile.get("threshold", 70)
    if not isinstance(thr, (int, float)) or not (0 <= thr <= 100):
        errs.append("'threshold' must be a number in [0,100]")

    sens = profile.get("sensitivity", 1.0)
    if not isinstance(sens, (int, float)) or not (0.1 <= sens <= 3.0):
        errs.append("'sensitivity' must be a number in [0.1,3.0]")

    weights = profile.get("weights", {})
    if not isinstance(weights, dict):
        errs.append("'weights' must be an object")
    else:
        for k, v in weights.items():
            if not isinstance(v, (int, float)) or v < 0:
                errs.append(f"weight '{k}' must be a non-negative number")

    lex = profile.get("lexicons", {})
    if not isinstance(lex, dict):
        errs.append("'lexicons' must be an object of class -> [terms]")
    else:
        for cls, terms in lex.items():
            if not isinstance(terms, list) or not all(
                isinstance(t, str) for t in terms
            ):
                errs.append(f"lexicon '{cls}' must be a list of strings")

    if kind == "judge":
        jc = profile.get("judge", {})
        if not isinstance(jc, dict):
            errs.append("'judge' must be an object for kind=judge profiles")
        elif not jc.get("rubric"):
            errs.append("judge profiles require 'judge.rubric'")

    return errs


def list_profiles(user_dir: Optional[str], bundled_dir: str) -> List[Dict[str, str]]:
    """Enumerate available profiles across user + bundled dirs (deduped)."""
    seen: Dict[str, Dict[str, str]] = {}
    for source, d in (("bundled", bundled_dir), ("user", user_dir)):
        if not d or not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".json"):
                continue
            name = fn[:-5]
            try:
                with open(os.path.join(d, fn), "r", encoding="utf-8") as fh:
                    meta = json.load(fh)
            except (OSError, json.JSONDecodeError):
                meta = {}
            seen[name] = {
                "name": name,
                "source": source,  # user shadows bundled (later wins)
                "kind": meta.get("kind", "lexical"),
                "threshold": meta.get("threshold", 70),
                "description": meta.get("description", ""),
            }
    return list(seen.values())


def _main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="drift-detector profile tool")
    ap.add_argument("action", choices=["show", "validate", "list"])
    ap.add_argument("--name", default="caveman")
    ap.add_argument("--user-dir")
    ap.add_argument("--bundled-dir", required=True)
    args = ap.parse_args()

    if args.action == "list":
        print(json.dumps(list_profiles(args.user_dir, args.bundled_dir), indent=2))
        return 0

    prof, errs = load(args.name, args.user_dir, args.bundled_dir)
    if prof is None:
        print(json.dumps({"ok": False, "errors": errs}))
        return 1
    verrs = validate(prof)
    if args.action == "validate":
        print(json.dumps({"ok": not verrs, "errors": errs + verrs}, indent=2))
        return 0 if not verrs else 1
    print(json.dumps({"profile": prof, "warnings": errs + verrs}, indent=2))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_main())
