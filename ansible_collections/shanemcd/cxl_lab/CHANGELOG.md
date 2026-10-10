# Changelog

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
