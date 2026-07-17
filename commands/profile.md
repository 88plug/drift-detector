---
description: Manage the active drift profile — /drift-detector:profile [name | list | show].
argument-hint: "[name|list|show]"
allowed-tools: Bash(bash:*), Bash(/scripts/run-python.sh:*), Bash(cat:*), Bash(printf:*)
---

Manage drift profiles. The argument is: `$ARGUMENTS`

Resolve paths first:

```bash
DATA="${CLAUDE_PLUGIN_DATA:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/data/drift-detector-88plug}"
BUNDLED="${CLAUDE_PLUGIN_ROOT}/profiles"
ACTIVE_FILE="$DATA/active-profile"
PY=(bash "${CLAUDE_PLUGIN_ROOT}/scripts/run-python.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/profiles.py")
```

Behavior by argument:
- `list` (or empty): run
  `"${PY[@]}" list --user-dir "$DATA/profiles" --bundled-dir "$BUNDLED"`
  and present the available profiles, marking the active one.
- `show`: print the active profile name (`cat "$ACTIVE_FILE"`) and run
  `"${PY[@]}" show --name <active> --user-dir "$DATA/profiles" --bundled-dir "$BUNDLED"`
  to display its resolved config.
- any other token `<name>`: validate it with
  `"${PY[@]}" validate --name <name> --user-dir "$DATA/profiles" --bundled-dir "$BUNDLED"`;
  if valid, set it active by writing the name to `$ACTIVE_FILE` atomically and
  confirm. If invalid, show the validation errors and do NOT change the active
  profile.

Keep output terse.
