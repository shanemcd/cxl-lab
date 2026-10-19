#!/usr/bin/env bash
# find-nightly-pins.sh — discover every pin for an OCP nightly and print a
# ready-to-paste versions.yml profile block.
#
# The pipeline pins nightly artifacts by sha256 because the CI artifacts
# service publishes no checksum manifest; when a pinned nightly is
# garbage-collected (its tools 404 forever), this script re-derives the
# full pin block for a newer accepted nightly following exactly the
# sources the artifacts role asserts against:
#   client-tool sha256s (stable double-download), installer-reported
#   payload digest, rhel-coreos-10 pullspec, machine-os build (-> ISO
#   name + build dir via meta.json), shipped kernel NVR (stream check
#   against the SRPM pin).
#
# Usage:
#   scripts/find-nightly-pins.sh [--stream STREAM] [VERSION|latest]
#                                [--pull-secret PATH] [--workdir DIR]
#                                [--timeout-min MIN] [--expect-kernel-stream S]
#
#   --stream                  release stream to query when VERSION is
#                             "latest" (default: 5.1.0-0.nightly)
#   VERSION                   nightly to pin (default: latest accepted)
#   --pull-secret PATH        pull secret with registry.ci.openshift.org
#                             auth (default: /root/pull-secret.txt)
#   --workdir DIR             scratch dir (default: mktemp -d)
#   --timeout-min MIN         per-tarball stable-fetch deadline (default 45)
#   --expect-kernel-stream S  warn when the shipped kernel is not from
#                             stream S (e.g. 6.12.0-211) — the SRPM pin in
#                             versions.yml must match the shipped stream
#
# The script never edits versions.yml: review the block, paste it under
# ocp_profiles, switch ocp_profile, and re-run the pipeline (the role's
# asserts re-verify every pin at runtime).

set -euo pipefail

STREAM="5.1.0-0.nightly"
VER="latest"
PULL_SECRET=""
TIMEOUT_MIN=45
EXPECT_KERNEL_STREAM=""
WORK=""

while [ $# -gt 0 ]; do
  case "$1" in
    --stream) STREAM="$2"; shift 2 ;;
    --pull-secret) PULL_SECRET="$2"; shift 2 ;;
    --workdir) WORK="$2"; shift 2 ;;
    --timeout-min) TIMEOUT_MIN="$2"; shift 2 ;;
    --expect-kernel-stream) EXPECT_KERNEL_STREAM="$2"; shift 2 ;;
    -h|--help) grep '^#' "$0" | sed '1d'; exit 0 ;;
    *) VER="$1"; shift ;;
  esac
done

BASE="https://openshift-release-artifacts.apps.ci.l2s4.p1.openshiftapps.com"
API="https://amd64.ocp.releases.ci.openshift.org/api/v1/releasestream"
BUILDS="https://rhcos.mirror.openshift.com/art/storage/prod/streams/rhel-10.2/builds"
BASE_PULL_SECRET="/root/pull-secret.txt"
CI_PULL_SECRET="/root/ci-pull-secret.json"
MERGED_PULL_SECRET="/root/ocp-install-cxl/pull-secret-merged.json"

fail() { echo "FAIL: $*" >&2; exit 1; }

# A pull secret with registry.ci.openshift.org auth is required for the
# payload queries. Prefer an explicit --pull-secret, then the merged
# secret host_prep renders into the install dir, then merge the base and
# CI secrets on the fly.
if [ -z "$PULL_SECRET" ]; then
  if [ -f "$MERGED_PULL_SECRET" ]; then
    PULL_SECRET="$MERGED_PULL_SECRET"
  elif [ -f "$BASE_PULL_SECRET" ] && [ -f "$CI_PULL_SECRET" ]; then
    PULL_SECRET="$(mktemp /tmp/nightly-pins-auth.XXXXXX.json)"
    jq -s '.[0] * .[1]' "$BASE_PULL_SECRET" "$CI_PULL_SECRET" > "$PULL_SECRET" \
      || fail "cannot merge $BASE_PULL_SECRET + $CI_PULL_SECRET"
    chmod 600 "$PULL_SECRET"
  else
    fail "no pull secret with registry.ci.openshift.org auth found (looked for $MERGED_PULL_SECRET, $BASE_PULL_SECRET + $CI_PULL_SECRET); pass --pull-secret PATH"
  fi
fi

command -v curl >/dev/null || fail "curl not found"
command -v jq >/dev/null || fail "jq not found"
command -v podman >/dev/null || fail "podman not found"
[ -n "$WORK" ] || WORK="$(mktemp -d /tmp/nightly-pins.XXXXXX)"
mkdir -p "$WORK/bin"
cd "$WORK"

echo "=== phase 0: resolve the nightly ==="
if [ "$VER" = "latest" ]; then
  VER="$(curl -fsS --max-time 60 "$API/$STREAM/latest" | jq -r .name)" || fail "cannot query $API/$STREAM/latest"
fi
case "$VER" in
  *-0.nightly-*|*-0.ci-*) ;;
  *) fail "$VER does not look like a nightly version (expected *-0.nightly-* or *-0.ci-*)" ;;
esac
echo "nightly: $VER"

echo "=== phase 1: stable fetch of the client tools ==="
# The CI artifacts service extracts tools lazily: first hits 404 for
# minutes (tens of minutes on a fresh nightly), the index state flaps,
# and truncated tarballs can be served while the extraction is writing.
# Trust only: HTTP 200 + gzip -t clean + identical sha256 across two
# downloads 30s apart.
fetch_stable() {
  local f="$1" url="$BASE/$VER/$1"
  local deadline=$(( $(date +%s) + TIMEOUT_MIN * 60 ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if curl -fsS --max-time 600 -o "$f.tmp" "$url" && gzip -t "$f.tmp" 2>/dev/null; then
      sleep 30
      if curl -fsS --max-time 600 -o "$f.tmp2" "$url" && gzip -t "$f.tmp2" 2>/dev/null \
         && [ "$(sha256sum < "$f.tmp")" = "$(sha256sum < "$f.tmp2")" ]; then
        mv -f "$f.tmp" "$f"; rm -f "$f.tmp2"; return 0
      fi
      echo "  $f: served unstable/truncated content; continuing to wait"
    else
      echo "  $(date +%H:%M:%S) $f: not ready yet (HTTP $(curl -sS -o /dev/null -w '%{http_code}' --max-time 30 "$url" || echo err))"
    fi
    sleep 60
  done
  return 1
}
INST="openshift-install-linux-$VER.tar.gz"
CLIENT="openshift-client-linux-$VER.tar.gz"
fetch_stable "$INST" \
  || fail "tools for $VER never stabilized within ${TIMEOUT_MIN} min — the build is likely garbage-collected; pick another nightly"
fetch_stable "$CLIENT" \
  || fail "tools for $VER never stabilized within ${TIMEOUT_MIN} min — the build is likely garbage-collected; pick another nightly"
INST_SHA="$(sha256sum < "$INST" | awk '{print $1}')"
CLIENT_SHA="$(sha256sum < "$CLIENT" | awk '{print $1}')"

echo "=== phase 2: installer self-report (payload digest) ==="
tar -xzf "$INST" -C bin
tar -xzf "$CLIENT" -C bin
./bin/openshift-install version | tee installer-version.txt
PAYLOAD="$(grep -o 'release image \S*' installer-version.txt | awk '{print $3}')"
[ -n "$PAYLOAD" ] || fail "installer reported no release image"
echo "payload ref: $PAYLOAD"

echo "=== phase 3: release payload info ==="
export REGISTRY_AUTH_FILE="$PULL_SECRET"
./bin/oc adm release info "$PAYLOAD" --pullspecs > pullspecs.txt 2>&1 || { cat pullspecs.txt; fail "oc adm release info --pullspecs"; }
MACHINE_OS_REF="$(grep -E '^\s*rhel-coreos-10\s' pullspecs.txt | awk '{print $2}' | head -1)"
[ -n "$MACHINE_OS_REF" ] || { cat pullspecs.txt; fail "no rhel-coreos-10 pullspec in payload"; }
./bin/oc adm release info "$PAYLOAD" > release-info.txt 2>&1 || { cat release-info.txt; fail "oc adm release info"; }
# Dual-stream payloads list machine-os as "10.2.X-0, 9.8.Y-0 ..." — the
# first entry is the rhel-10 build (comma-stripped).
MACHINE_OS_VER="$(grep -oE '^\s*machine-os\s+\S+' release-info.txt | awk '{print $2}' | head -1 | sed 's/,$//')"
[ -n "$MACHINE_OS_VER" ] || { cat release-info.txt; fail "no machine-os line in release info"; }
echo "machine-os build: $MACHINE_OS_VER"

echo "=== phase 4: shipped kernel NVR ==="
podman run --rm --security-opt label=disable --authfile "$PULL_SECRET" \
  --entrypoint /usr/bin/rpm "$MACHINE_OS_REF" -q kernel | tee kernel-nvr.txt
KERNEL_NVR="$(head -1 kernel-nvr.txt | sed 's/\.x86_64$//')"
echo "osimage kernel NVR: $KERNEL_NVR"
if [ -n "$EXPECT_KERNEL_STREAM" ] && ! grep -q "$EXPECT_KERNEL_STREAM" <<<"$KERNEL_NVR"; then
  echo "WARNING: shipped kernel $KERNEL_NVR is NOT from stream $EXPECT_KERNEL_STREAM —" \
       "the kernel_srpm_nvr/kernel_srpm_sha256 pins in versions.yml need a matching" \
       "update or the artifacts role SRPM-stream assert will fail." >&2
fi

echo "=== phase 5: RHCOS live ISO build + sha ==="
curl -fsS --max-time 60 "$BUILDS/$MACHINE_OS_VER/x86_64/meta.json" > meta.json || fail "meta.json fetch for $MACHINE_OS_VER"
jq . meta.json > meta-pretty.json || { cat meta.json; fail "meta.json parse"; }
ISO_NAME="$(jq -r '.images["live-iso"].path' meta.json)"
ISO_SHA="$(jq -r '.images["live-iso"].sha256' meta.json)"
[ -n "$ISO_NAME" ] && [ "$ISO_NAME" != "null" ] || { cat meta-pretty.json; fail "no live-iso in meta.json"; }
ISO_CODE="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 60 "$BUILDS/$MACHINE_OS_VER/x86_64/$ISO_NAME")"
echo "live-iso: $ISO_NAME (HTTP $ISO_CODE)"

echo ""
echo "==================== PIN BLOCK for versions.yml ===================="
cat <<EOF
  # $VER — pinned $(date +%Y-%m-%d) by scripts/find-nightly-pins.sh
  nightly-$VER:
    ocp_version: "$VER"
    openshift_install_sha256: "$INST_SHA"
    oc_sha256: "$CLIENT_SHA"
    machine_os_content_ref: >-
      $MACHINE_OS_REF
    kernel_srpm_nvr: (keep the current pin if the stream still matches: shipped $KERNEL_NVR)
    rhcos_iso_image_name: "$ISO_NAME"
    rhcos_iso_sha256: "$ISO_SHA"
    nightly_release_image: >-
      $PAYLOAD
    nightly_iso_build_dir: "$MACHINE_OS_VER/x86_64"

and switch the active profile:
  ocp_profile: "nightly-$VER"
====================================================================
EOF
echo "review the block, paste it under ocp_profiles in versions.yml, then re-run the pipeline."
