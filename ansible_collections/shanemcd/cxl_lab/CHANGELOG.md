# Changelog

## 2.0.0 (SNO rewrite)

Dramatic simplification per `ANSIBLE-AUTOMATION-PLAN-SNO.md`; every phase
live-validated on the lab host with idempotent re-runs.

**Topology / install path**
- SNO: 1 schedulable master (bootstrap-in-place on `/dev/vda`) + 2 CXL workers
  + 1 DRAM worker; no HAProxy, no ignition server, no bootstrap VM, no API VIP.
- Stock upstream QEMU 11.1.2 built once in a digest-pinned CS10 builder,
  **installed to a host prefix** (`/opt/qemu-11.1.2`) and run natively on the
  host — no DVSEC patch, no runtime image, no per-VM containers.
- Kernel source closed loop (§6.2): NVR read from the quay osimage → entitled
  CDN SRPM download (CS10 container + ro entitlement mounts) → brew fallback.
  `machine_os_content_ref` pinned to the `rhel-coreos-10` component digest.
- Pre-baked node images NOT adopted (§6.1 spike: BIB qcow2s boot the cxltest
  kernel but first-boot ignition application is blocked by an
  ignition-files/sysroot.mount ordering edge); workers pivot via the MCO.

**Networking (§5, §10.3, §10.8)**
- Persistent iproute2 bridge + gateway; one reversible tagged MASQUERADE rule;
  `ip_forward` sysctl drop-in; host `/etc/hosts` block.
- **Minimal host-native dnsmasq is load-bearing**: ignition fetch-before-apply
  + DNS-SAN-only MCS cert + initramfs default-DHCP ⇒ DNS records + per-MAC
  DHCP reservations (first boot DHCP, reboots static keyfile, same address).
- Egress probe (veth + netns) asserts VM-path internet access.

**Boot model**
- Master = kargs-patched live-ISO copy (ignition.platform.id=qemu,
  console=ttyS0) + blank virtio target + fw_cfg merged BiP ignition.
- Workers = merged MCS stubs fetched over DHCP; ONE fleet-parallel start.

**NUMA (§7, §10.7)**
- Host NUMA handling dropped entirely: no detection, no pinning, first-touch
  placement; the CXL graph keeps a CPU-less guest NUMA node without
  `host-nodes` (asserted in-role). Verify states this explicitly.

**Live-proven fixes worth remembering**
- machine-os-puller SA pivot race healed (stale annotation dropped, MCO
  resyncs).
- Installed-vs-live master gate = `/sysroot` on `bip_installation_disk` (the
  installed master retains an inactive bootkube.service).
- Ingress pinned to the master via IngressController nodePlacement.
- `oc get -o wide` RESTARTS column is two fields once aged → jsonpath
  everywhere placement is polled.
- host_prep/install_config ignition-dir mode fight resolved (0700).

**Phases (P1–P12)**: scaffold; host_prep; artifacts+QEMU; network-lite;
ignition (BiP); vms; install; os image + registry; MCO + region commit;
verify (deterministic 0600 evidence); teardown + ops (fleet-parallel shutdown,
per-layer tags, QEMU prefix never removed, `--check`-safe); docs.
Teardown → full rebuild via `site.yml` validated end-to-end (ok=309, re-run
changed=0).

## 1.0.0

- Full pipeline landed (P1-P13): host prep, artifacts (cxltest kernel RPMs), network
  (dnsmasq/HAProxy/ignition containers), patched QEMU runtime image, ignition, per-VM
  QEMU containers, cluster install + bootstrap teardown, layered OS image + lab
  registry, MCO rollout + region commit (self-recovering), verification with 0600
  evidence, per-layer teardown + ops, docs.
