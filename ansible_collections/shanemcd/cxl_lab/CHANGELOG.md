# Changelog

## 1.4.0

The deploy pipeline's heavy blocks now overlap; a full run costs ~56
minutes on the reference host (was ~1h23 warm / ~1h33 fresh).

- VM networking uses vhost (`vhost=on` on the tap netdevs): vhost_net
  was always loaded but never opted into, so every guest packet —
  including the release-payload pulls that dominate BiP convergence —
  went through QEMU userspace. Measured: convergence 28 → 22 minutes.
- The master VM phase runs before the layered-image stage: the master
  needs only ignition + network (it boots the live ISO against a blank
  install target), so its install-to-disk reboot and BiP convergence
  proceed while the kernel RPMs and the pre-baked worker disk build.
  The vms role is split into master/workers task phases and the plays
  are renumbered to the new order (04_ignition, 05_master, 06_os_image,
  07_workers, 08_install, 09_region_commit).
- Workers start against the already-converged master: their first-boot
  ignition merge rides ignition's own fetch retry loop (infinite,
  backoff capped at 5s — the rendered worker configs set no timeout),
  gated on the master's install-to-disk reboot so the live env's
  bootstrap-era MCS can never serve them.
- The QEMU build launches fire-and-forget in the artifacts play and is
  collected by the master VM play, overlapping the kernel build. The
  MCS wait gate is configurable (`vm_mcs_wait_retries`, 240 × 15 s) and
  the router-rollout wait runs directly before the router-placement
  read it guards (the ingress operator can start a second roll moments
  after the pin settles).

## 1.3.0

All in-container builds now run in the shared RHEL 10 builder.

- Stock upstream QEMU is built in the `rhel-bootc` RHEL 10 builder
  container (entitled CDN build deps via the host RHSM entitlement) —
  one fewer third-party image, and the builder's RHEL glibc is older than
  or equal to any RedHat-family host.
- The build is hermetic: meson and ninja come from pip inside the builder
  (the lab's entitlement carries no CRB), and `-Dwrap_mode=nodownload`
  disables FDT cleanly (x86 targets do not use it) — so the produced
  binary has no libfdt linkage and the host needs no extra runtime
  packages.
- The builder image is a single shared pin (`rhel10_builder_image`) used
  by the kernel-SRPM download, the kernel-builder stage, and the QEMU
  build.

## 1.2.0

The layered OS image never leaves the host.

- The bootc-image-builder node disk is built from the layered image in the
  host's container storage, and the install-time osImageURL MachineConfig for
  the worker-cxl pool carries the layered image's local ref — the exact ref a
  BIB-booted worker reports as booted — so OpenShift's firstboot OS check
  matches and no pivot, pull, or second reboot ever fires.
- The region-commit service ships via the CXL workers' first-boot ignition
  (rendered from the `region_commit` role's ignition template);
  `08_region_commit` labels the workers into the install-time worker-cxl pool
  and verifies the booted image, kernel, service, and committed region.
- The artifacts stage downloads only the RHCOS live ISO (the master's boot
  media) — the workers boot the BIB node disk.

## 1.1.0

Slimmer defaults and a configurable fleet.

- The CXL and DRAM worker fleets are generated from `cxl_worker_count` and
  `dram_worker_count` (one each by default) with deterministic node names,
  IP/MAC suffixes and CXL bus numbers — scaling the fleet appends without
  renumbering existing nodes.
- Node specs drop to the OpenShift-documented minimum resource requirements:
  the master at the single-node minimum profile (4 vCPUs, 16 GiB RAM, 120 GiB
  storage) and workers at the compute-machine minimum (2 vCPUs, 8 GiB RAM,
  100 GiB storage). The default lab needs ~36 GiB of guest RAM instead of
  ~104 GiB. The emulated CXL region size is unchanged.
- `host_prep` now asserts the generated topology is sane — unique IP suffixes
  below the DHCP free range, unique in-bounds CXL bus numbers — before the
  host capacity checks.
- Reliability fixes found while validating the minimum-spec fleet and the
  scale-out path, all convergent-wait gates:
  - `cluster_install` polls API readiness (readyz) before its first cluster
    interaction; a freshly installed API server can time out requests
    while it warms up.
  - the CSR join gate no longer counts the schedulable single-node master
    as a Ready worker (its role list matches /worker/), which let the gate
    pass before the last worker joined.
  - `mco_cxl` waits on the booted kernel of every CXL worker instead of the
    pool's Updated condition, which can read stale-True before the
    controller notices a freshly labeled node.
  - `verify` settles first: a read-only poller waits for every operator and
    the clusterversion to read Available before asserting, instead of
    racing post-install churn.

## 1.0.0

Initial public release of the `shanemcd.cxl_lab` collection: automation for a
disposable OpenShift + QEMU-emulated CXL Type-3 lab.

- Single-node OpenShift (bootstrap-in-place) master + 2 CXL workers + 1 DRAM
  worker, driven end-to-end by idempotent playbooks (`site` runs the full
  pipeline, `teardown` reverses it layer by layer).
- CXL workers get a full emulated Type-3 device graph (pxb-cxl / cxl-rp /
  cxl-type3 + fixed memory window) on a CPU-less guest NUMA node; the region
  is committed and onlined as System RAM via a Machine Config Operator
  rollout.
- `cxltest` kernel RPMs built from the RHEL SRPM (single config flip:
  `CONFIG_CXL_REGION_INVALIDATION_TEST=y`) in a disposable CentOS Stream 10
  builder, baked into a bootc layered OS image served from a lab registry.
- Stock upstream QEMU built in-collection and installed to a host prefix —
  no patches, no per-VM containers, no libvirt.
- Private lab network: persistent bridge with host gateway, one reversible
  masquerade rule, and a host-native dnsmasq (DNS records + per-MAC DHCP
  reservations) that ignition's fetch-before-apply ordering makes
  load-bearing.
- Operational playbooks: read-only `ops.status`, single-worker cold restart
  `ops.restart_cxl_worker`, full-lab `verify` with a deterministic 0600
  evidence report.
- Quality gates: `check-no-hardcoding.sh` (no literal config values outside
  the variable taxonomy) and `check-public-clean.sh` (no private references
  in tracked files).
