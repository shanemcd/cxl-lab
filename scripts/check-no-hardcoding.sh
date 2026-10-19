#!/usr/bin/env bash
# Gate: no hardcoded configuration values in role/playbook code.
#
# Vars files (roles/*/defaults, roles/*/vars, inventory group_vars/host_vars, and
# molecule scenarios) are the sanctioned homes for literal values. Everywhere else,
# values must come from variables.
#
# Detects: IPv4 addresses, http(s):// URLs, MAC addresses, x.y.z version literals.
# Escape hatch: append "# hardcode-ok" to a line (use sparingly, with justification).
set -euo pipefail
cd "$(dirname "$0")/.."

BASE="ansible_collections/shanemcd/cxl_lab"
if [ ! -d "$BASE" ]; then
  echo "hardcoding gate: collection not found at $BASE" >&2
  exit 1
fi

# galaxy.yml and meta/ are collection/role manifests (ansible-galaxy format:
# version, repository, requires_ansible, …) — structural metadata, not lab
# configuration.
mapfile -t FILES < <(find "$BASE" -type f \( -name '*.yml' -o -name '*.yaml' -o -name '*.j2' \) \
  ! -name 'galaxy.yml' \
  ! -path '*/meta/*' \
  ! -path '*/defaults/*' ! -path '*/vars/*' ! -path '*/molecule/*' | sort)

# One combined alternation, one grep per file — a per-line grep -q loop
# forks thousands of subprocesses and dominates the runtime.
PATTERN='([0-9]{1,3}\.){3}[0-9]{1,3}|https?://[^[:space:]}]|([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}|(^|[^0-9.])[0-9]+\.[0-9]+\.[0-9]+([^0-9.]|$)'

fail=0
for f in "${FILES[@]}"; do
  while IFS= read -r num_line; do
    num="${num_line%%:*}"
    line="${num_line#*:}"
    # Skip comment-only lines and the explicit allowlist marker.
    stripped="${line#"${line%%[![:space:]]*}"}"
    case "$stripped" in \#*) continue ;; esac
    case "$line" in *'# hardcode-ok'*) continue ;; esac
    echo "hardcoding gate: $f:$num: $line" >&2
    fail=1
  done < <(grep -nE "$PATTERN" "$f" || true)
done

if [ "$fail" -ne 0 ]; then
  echo "hardcoding gate: FAILED — move values into variables" >&2
  exit 1
fi
echo "hardcoding gate: OK (${#FILES[@]} files checked)"
