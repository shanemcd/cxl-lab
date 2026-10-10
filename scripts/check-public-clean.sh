#!/usr/bin/env bash
# Gate: the repository must be publishable — no references to private
# design documents, working-session artifacts, internal infrastructure, or
# internal hostnames in any TRACKED file (git ls-files; installed
# third-party collections are gitignored and never checked).
#
# What is checked and why:
#   - section markers / document names: '§', runbook, plan §
#   - working-session artifacts: ralph, .unipi, iteration, spike
#   - phase/story markers: standalone P<number> tokens and (v1)/(v2)-style
#     narrative tags (version-suffixed technical names like the registry
#     "v2 API" or OCI media types are NOT matched by these patterns)
#   - internal infrastructure: the development host names, internal build
#     system, internal resolver addresses, and the private repository name
#
# Exit 0 = clean. Any hit fails the gate with file:line detail.
set -euo pipefail
cd "$(dirname "$0")/.."

fail=0
check() {
  local label="$1" pattern="$2"
  local hits
  # This script documents and matches the forbidden tokens itself, so it is
  # excluded from its own scan.
  hits=$(git ls-files | grep -vx 'scripts/check-public-clean.sh' \
          | xargs grep -nE "$pattern" 2>/dev/null || true)
  if [ -n "$hits" ]; then
    echo "public-clean gate: FAILED ($label):" >&2
    echo "$hits" >&2
    fail=1
  fi
}

check 'section markers / document references' \
  '§|runbook'
check 'working-session artifacts' \
  'ralph|\.unipi|\biteration\b|\bspike\b'
check 'phase / story markers' \
  '\bP[0-9]{1,2}\b|\(v1\)|\(v2\)|\bv2:|\bin v2\b|\bthe v1\b'
check 'internal infrastructure' \
  'dell-per|per740|devel\.redhat\.com|cxl-lab-old|10\.2\.32\.|10\.11\.5\.'

if [ "$fail" -ne 0 ]; then
  exit 1
fi
echo "public-clean gate: OK ($(git ls-files | wc -l) tracked files checked)"
