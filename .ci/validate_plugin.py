#!/usr/bin/env python3
"""validate_plugin.py — CI gate for the drift-detector plugin.

Checks, with no third-party deps:
  * every JSON file parses;
  * the plugin manifest(s) have the required fields and well-formed hooks;
  * marketplace.json is well-formed;
  * every command/skill markdown has valid YAML-ish frontmatter with a
    `description` (commands) / `name`+`description` (skills);
  * every referenced hook script and the scripts they call exist;
  * `bash -n` passes on every shell script;
  * the scoring engine self-test passes.

Exit 0 == all good; non-zero == CI fail, with a summary of problems.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
errors: list = []
checks = 0


def ok(msg):
    global checks
    checks += 1
    print(f"  ok: {msg}")


def fail(msg):
    errors.append(msg)
    print(f"FAIL: {msg}")


def load_json(rel):
    path = os.path.join(ROOT, rel)
    if not os.path.isfile(path):
        fail(f"missing file: {rel}")
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {rel}: {exc}")
        return None


def all_json_parse():
    for dirpath, _dirs, files in os.walk(ROOT):
        if "/.git" in dirpath:
            continue
        for fn in files:
            if fn.endswith(".json"):
                rel = os.path.relpath(os.path.join(dirpath, fn), ROOT)
                if load_json(rel) is not None:
                    ok(f"json parses: {rel}")


def check_manifest(rel, discovery=False):
    """Check a plugin manifest.

    discovery=True: .claude-plugin/plugin.json is a marketplace-discovery manifest —
    it carries name/description/keywords but no runtime fields (version, hooks).
    """
    data = load_json(rel)
    if data is None:
        return
    required = ("name", "description") if discovery else ("name", "version", "description")
    for field in required:
        if not data.get(field):
            fail(f"{rel}: missing required field '{field}'")
    if data.get("name") and data["name"] != "drift-detector":
        fail(f"{rel}: name must be 'drift-detector'")
    if not discovery:
        hooks = data.get("hooks")
        if isinstance(hooks, str):
            hp = os.path.join(ROOT, hooks.lstrip("./"))
            if not os.path.isfile(hp):
                fail(f"{rel}: hooks path not found: {hooks}")
            else:
                check_hooks_obj(load_json(os.path.relpath(hp, ROOT)), rel)
        elif isinstance(hooks, dict):
            check_hooks_obj({"hooks": hooks}, rel)
        else:
            fail(f"{rel}: missing or malformed hooks")
    ok(f"manifest ok: {rel}")


def check_hooks_obj(obj, src):
    if not isinstance(obj, dict):
        fail(f"{src}: hooks object not a dict")
        return
    hooks = obj.get("hooks", obj)
    for event in ("SessionStart", "Stop", "UserPromptSubmit"):
        if event not in hooks:
            fail(f"{src}: hooks missing event {event}")
            continue
        for group in hooks[event]:
            for h in group.get("hooks", []):
                cmd = h.get("command", "")
                # extract a referenced .sh path
                for tok in cmd.replace('"', " ").split():
                    if tok.endswith(".sh") and "CLAUDE_PLUGIN_ROOT" in cmd:
                        rel = tok.split("CLAUDE_PLUGIN_ROOT}/", 1)[-1]
                        p = os.path.join(ROOT, rel)
                        if not os.path.isfile(p):
                            fail(f"{src}: hook script missing: {rel}")
    ok(f"hooks reference real scripts: {src}")


def check_marketplace():
    data = load_json(".claude-plugin/marketplace.json")
    if data is None:
        return
    if not data.get("plugins"):
        fail("marketplace.json: no plugins array")
    else:
        names = [p.get("name") for p in data["plugins"]]
        if "drift-detector" not in names:
            fail("marketplace.json: drift-detector not listed")
    ok("marketplace.json ok")


def parse_frontmatter(path):
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    block = text[3:end].strip().splitlines()
    fm = {}
    for line in block:
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip().strip('"')
    return fm


def check_commands():
    cdir = os.path.join(ROOT, "commands")
    if not os.path.isdir(cdir):
        fail("commands/ dir missing")
        return
    for fn in os.listdir(cdir):
        if not fn.endswith(".md"):
            continue
        fm = parse_frontmatter(os.path.join(cdir, fn))
        if not fm:
            fail(f"commands/{fn}: missing frontmatter")
        elif not fm.get("description"):
            fail(f"commands/{fn}: frontmatter missing 'description'")
        else:
            ok(f"command frontmatter ok: {fn}")


def check_skill():
    sp = os.path.join(ROOT, "skills", "drift-detector", "SKILL.md")
    if not os.path.isfile(sp):
        fail("SKILL.md missing")
        return
    fm = parse_frontmatter(sp)
    if not fm or not fm.get("name") or not fm.get("description"):
        fail("SKILL.md: frontmatter needs name + description")
    else:
        ok("SKILL.md frontmatter ok")


def check_bash_syntax():
    for dirpath, _dirs, files in os.walk(ROOT):
        if "/.git" in dirpath:
            continue
        for fn in files:
            if fn.endswith(".sh"):
                p = os.path.join(dirpath, fn)
                rel = os.path.relpath(p, ROOT)
                r = subprocess.run(["bash", "-n", p], capture_output=True, text=True)
                if r.returncode != 0:
                    fail(f"bash -n {rel}: {r.stderr.strip()}")
                else:
                    ok(f"bash -n ok: {rel}")


def check_python_syntax():
    import py_compile
    for dirpath, _dirs, files in os.walk(ROOT):
        if "/.git" in dirpath or "__pycache__" in dirpath:
            continue
        for fn in files:
            if fn.endswith(".py"):
                p = os.path.join(dirpath, fn)
                rel = os.path.relpath(p, ROOT)
                try:
                    py_compile.compile(p, doraise=True)
                    ok(f"py_compile ok: {rel}")
                except py_compile.PyCompileError as exc:
                    fail(f"py_compile {rel}: {exc}")


def check_engine():
    eng = os.path.join(ROOT, "src", "lib", "drift_score.py")
    if not os.path.isfile(eng):
        fail("engine missing: src/lib/drift_score.py")
        return
    r = subprocess.run([sys.executable, eng, "--selftest"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"engine selftest failed: {r.stderr.strip() or r.stdout.strip()}")
    else:
        ok(f"engine selftest: {r.stdout.strip()}")


def check_profiles():
    pdir = os.path.join(ROOT, "profiles")
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    try:
        import profiles as profmod
    except Exception as exc:  # noqa: BLE001
        fail(f"cannot import profiles.py: {exc}")
        return
    for fn in os.listdir(pdir):
        if not fn.endswith(".json"):
            continue
        name = fn[:-5]
        prof, errs = profmod.load(name, None, pdir)
        verrs = profmod.validate(prof or {})
        if errs or verrs:
            fail(f"profile {name} invalid: {errs + verrs}")
        else:
            ok(f"profile valid: {name}")


def main():
    print("== drift-detector plugin validation ==")
    all_json_parse()
    check_manifest("plugin.json")
    check_manifest(".claude-plugin/plugin.json", discovery=True)
    check_marketplace()
    check_commands()
    check_skill()
    check_bash_syntax()
    check_python_syntax()
    check_profiles()
    check_engine()
    print(f"\n{checks} checks run, {len(errors)} failures")
    if errors:
        print("\n".join(f"  - {e}" for e in errors))
        return 1
    print("ALL GOOD")
    return 0


if __name__ == "__main__":
    sys.exit(main())
