# cxl_lab.lab

Ansible collection automating the **disposable OpenShift + QEMU-emulated CXL Type-3
lab**: a single-node OpenShift (SNO) master, 2 CXL workers, and 1 DRAM worker — with a
committed 4 GiB emulated CXL region exposed as System RAM on a CPU-less guest NUMA
node on both CXL workers — built from a bare RHEL-family server with nothing but
podman and SSH.

> **This is v2 (SNO)** — a dramatic simplification of the v1 topology, driven by
> [`ANSIBLE-AUTOMATION-PLAN-SNO.md`](../../../../../../ANSIBLE-AUTOMATION-PLAN-SNO.md)
> (the **authoritative plan**: topology, phases, adopted decisions §10 with live
> verdicts). The v1 plan
> ([`ANSIBLE-AUTOMATION-PLAN.md`](../../../../../../ANSIBLE-AUTOMATION-PLAN.md)) and
> [`REPRODUCTION-RUNBOOK.md`](../../../../../../REPRODUCTION-RUNBOOK.md) remain the
> behavioral reference; every deviation from the runbook is recorded in plan §10
> (notably: host NUMA is *not* pinned — §7/§10.7; a minimal host-native dnsmasq serves
> DNS+DHCP — §10.8).

- **v2 shape**: no HAProxy, no ignition server, no bootstrap VM, no per-VM containers,
  no custom iptables chains, no QEMU patch. Stock upstream QEMU (≥ 11.1) is built once
  in a digest-pinned CS10 builder container and **installed to a host prefix**
  (`/opt/qemu-11.1.2`), then run natively on the host; the lab network is a persistent
  iproute2 bridge with one reversible tagged MASQUERADE rule; the install path is
  **bootstrap-in-place (BiP)** on the master.
- All results are **test-only / cache-coherency-unverified** — the region commit relies
  on the guest kernel's `CONFIG_CXL_REGION_INVALIDATION_TEST=y` bypass of
  `cpu_cache_invalidate_memregion()`.

## What you get

After the pipeline runs you have, all idempotent and re-runnable:

- an OpenShift single-node cluster (schedulable master) + 3 workers, all Ready with
  operators Available;
- every VM as **host-native QEMU** under per-node run scripts (pidfile,
  `--status`/`--stop`/`--validate`), the CXL workers carrying the full emulated
  Type-3 graph (`pxb-cxl` → `cxl-rp` → `cxl-type3` + fixed-memory-window, guest NUMA
  node 1 — deliberately **without** `host-nodes` binding, plan §7);
- CXL workers booted into a layered OS image carrying the `cxltest` kernel, with the
  CXL region committed (`region0/commit=1`) and `dax0.0` onlined as System RAM on the
  CPU-less guest NUMA node;
- a 0600 deterministic evidence report (runbook §10 checklist) in the install dir;
- operational helpers (status, single-worker cold restart) and a per-layer tagged
  teardown.

## Quick start

```sh
cd <repo>/ansible

# 1. Site inputs (see "Variable reference" below): pull secret, SSH keypair,
#    subnet/pins in inventory/group_vars/all/*.yml — paths only, never secrets
#    in the repo.

# 2. Install pinned external collections (gitignored):
ansible-galaxy collection install -r requirements.yml

# 3. Host prerequisites + full pipeline + verification:
ansible-playbook -i inventory \
  collections/ansible_collections/cxl_lab/lab/playbooks/site.yml
```

`site.yml` runs the numbered phases in order (01–08, then verify). Each phase is also
a standalone playbook; every phase guards itself for idempotent re-runs, and
mid-install re-runs resume cleanly.

## Playbooks

| Playbook | Phase | What it does |
|---|---|---|
| `01_host_prep.yml` | P2 | Asserts OS family (RedHat), SELinux (conditional), SNO capacity (host-wide memfree math), ports; RHSM (RHEL-gated, load-bearing for the §6.2 CDN kernel-SRPM route); ensures packages incl. `dnsmasq`. |
| `02_artifacts.yml` | P3 | Downloads + SHA-verifies installer/oc/RHCOS live ISO; builds the `cxltest` kernel RPMs via the §6.2 closed loop (CDN SRPM route; brew fallback); builds **stock QEMU 11.1.2** in a digest-pinned CS10 builder → `/opt/qemu-11.1.2` (exists-guard: never rebuilt, never torn down). |
| `03_network.yml` | P4 | Persistent iproute2 bridge + gateway address, `ip_forward` sysctl drop-in, host `/etc/hosts` block, one tagged MASQUERADE rule, **host-native dnsmasq** (DNS records + per-MAC DHCP reservations, plan §10.8), egress probe (veth+netns → TCP to quay.io). |
| `04_ignition.yml` | P5 | install-config (1 CP/0 compute, `bootstrapInPlace` on `/dev/vda`) → manifests → custom node MCs → `single-node-ignition-config`; per-node merge (NM keyfile MAC-bound static IP, hosts, hostname) → 0700/0600 staged ignition (12 h freshness window). |
| `05_vms.yml` | P6 | Per-node run scripts + qcow2 overlays; master = BiP boot model (kargs-patched live-ISO copy + blank virtio target + fw_cfg merged ignition — see "Metal-ISO" below); workers = MCS stubs fetched over DHCP; ONE fleet-parallel start command; in-role assert of the CXL graph shape. |
| `06_install.yml` | P7 | Heals the 4.22.16 machine-os-puller SA pivot race; CSR approvals; IngressController pinned to the master (+ rollout settle); operator settle; final asserts (4/4 Ready, clusterversion, routers on master, master osImageURL == pin). |
| `07_os_image.yml` | P8 | Layered OS image (`machine_os_content_ref` base + SHA-verified cxltest RPMs), bootc lint, push to the lab registry (published on the bridge gateway), authoritative `Docker-Content-Digest` stamp (0600), cluster-wide `insecureRegistries` trust. |
| `08_mco.yml` | P9 | worker-cxl labels → MachineConfigPool → osImageURL + region-commit MachineConfigs → MCO rollout waits → region commit with guest self-recovery (stale `MEM_ENABLE`) and cold-restart fallback. |
| `verify.yml` | P10 | Runbook §10 checklist per CXL worker (guest), host QEMU liveness + type-3 device count, cluster state; writes the deterministic 0600 evidence report. |
| `teardown.yml` | P11 | Per-layer tagged reverse teardown — see below. **Not in site.yml.** |
| `ops/status.yml` | P11 | Read-only summary: nodes, clusterversion, VM `--status`, network/resolver/registry, region commit, layered image. |
| `ops/restart_cxl_worker.yml` | P11 | Cold-restart one CXL worker: drain → poweroff → relaunch → Ready → uncordon → region-commit assert. `-e target_worker=worker-cxl-0`. |
| `site.yml` | — | 01→08 in order, then verify. |

### Teardown

`teardown.yml` removes layers in reverse build order — `mco` → `registry` → `vms` →
`network` → `images` → `artifacts` → `install-data` (secrets last), tagged per layer:

```sh
ansible-playbook .../playbooks/teardown.yml                 # everything
ansible-playbook .../playbooks/teardown.yml --tags vms      # one layer
ansible-playbook .../playbooks/teardown.yml --check         # dry run (safe:
                                                            # exec tasks are
                                                            # check-guarded)
```

- The vms layer shuts guests down **fleet-parallel** (one poweroff command, one fleet
  poller, `--stop` fallback); the QEMU **host prefix is never removed** (§10.6 — a
  same-version rebuild skips the QEMU build entirely).
- Removing the `mco` layer lets the MCO roll CXL workers back to the release base
  image (not waited on); a network-only teardown with running VMs requires restarting
  them (taps re-enslave at VM start); a rebuild after the `vms` layer or deeper must
  regenerate ignition within a fresh 12 h window.
- The full teardown → `site.yml` rebuild path is live-validated (P11).

## Variable reference

Everything is a variable; role `defaults/` hold knobs, and **shared values live in the
taxonomy** (`inventory/group_vars/all/`) because role defaults never cross roles. The
gate (`scripts/check-no-hardcoding.sh`) fails any IP/URL/MAC/version literal outside
vars files.

Per-site inputs you must set (defaults match the reference lab):

| File | Key contents |
|---|---|
| `secrets.yml` | **Paths only** to the pull secret (0600) and the install SSH keypair; kubeconfig location. Tasks touching them are `no_log`. |
| `network.yml` | Lab subnet prefix/CIDR, bridge name, gateway IP (also the registry host + dnsmasq listener), dnsmasq conf/service + DHCP free-range suffixes, NAT rule comment, host-resolution block (API/API-int/ingress names on the master IP). |
| `topology.yml` | Node table (names/IP/MAC/vCPU/RAM/CXL size), `bip_installation_disk`, VM lifecycle waits, MCP/label/MC names, guest CXL NUMA node, ignition freshness window. |
| `versions.yml` | OpenShift version + channel, installer/oc/RHCOS-ISO URLs **with pinned SHA-256s**, kernel NVR + CDN pin, QEMU version/prefix + source SHA, `machine_os_content_ref` (TOFU pin), layered image refs. |
| `paths.yml` | Install dir (secrets, stamps), VM assets dir and its subdirs (images/ignition/logs/scripts/run/registry), verification dir, per-master boot-ISO name. |
| `host.yml` | Lab host connection (local by default). |

## Iterate until green (development model)

There is no molecule and no test harness — the automation is developed by running it
against the disposable lab host:

1. Run the phase's playbook on the lab host.
2. Observe the failure.
3. Fix the automation — never perform by hand what the automation should do.
4. Re-run until clean. **Idempotency is mandatory**: a re-run reports `changed=0`
   (guards, stamps, `changed_when`/`failed_when` discipline).

Read-only asserts run freely; mutating phases run as they land; `teardown.yml` resets
state between attempts. Using the automation's own primitives to resume an interrupted
run is acceptable.

## Safety gates

Enforced in code and re-asserted by `verify`:

- QEMU-emulated CXL only — physical CXL passthrough args are never emitted; the CXL
  graph shape (pxb-cxl/cxl-type3/fmw, CPU-less guest node, **no** `host-nodes`
  binding) is asserted in-role (§7).
- Host NUMA placement is **not** gated or pinned — first-touch placement (§7/§10.7; a
  deliberate, recorded deviation from the runbook).
- Secrets are path-referenced only, `no_log` on tasks that touch them, 0700/0600.
- Ignition must be fresh (12 h window); VM starts refuse stale ignition.
- Process management via pidfiles and exact pids — never `pkill -f`.
- Guest root operations via `ssh core@… 'sudo bash -s'` with the install keypair.
- Host-OS agnostic within the RedHat family (`os_family` assert, SELinux-conditional).
- New disposable cluster only.

## Iteration gates

Run from `ansible/` before every commit:

```sh
scripts/check-no-hardcoding.sh
yamllint collections/ansible_collections/cxl_lab inventory
ansible-lint collections/ansible_collections/cxl_lab inventory
ansible-playbook --syntax-check <every playbook, incl. playbooks/ops/*.yml>
# plus: the phase's playbook runs clean on the lab host
```

## Known gotchas (encoded in the roles — do not re-derive)

- **Metal live-ISO**: the RHCOS live ISO boots `platform=metal` with a VGA-only
  console (fw_cfg ignition ignored, serial silent). The vms role prepares a per-master
  ISO copy with `coreos-installer iso kargs modify` appending
  `ignition.platform.id=qemu console=ttyS0,115200n8`; ignition itself stays in fw_cfg.
- **The lab resolver is load-bearing** (§10.8): ignition fetches `config.merge`
  before storage, the MCS certificate has DNS SANs only, and the initramfs defaults
  to DHCP — hence dnsmasq serves both DNS and per-MAC DHCP reservations (first boot
  DHCP, reboots static keyfile, same address).
- **BiP installed-vs-live gate**: the installed master *retains* an inactive
  `bootkube.service`; the real signal is the master's `/sysroot` living on
  `bip_installation_disk` plus the MCS port answering.
- **machine-os-puller SA race** (4.22.16 BiP): the SA's stale
  `internal-registry-pull-secret-ref` annotation across the live→installed transition
  degrades the MCO; `cluster_install` drops the annotation and the MCO resync heals.
- **oc column formats are unstable**: `oc get -o wide` renders RESTARTS as
  `2 (23m ago)` (two fields) once aged — positional awk indexes silently check the
  wrong column. Poll placement via jsonpath (`{.status.phase}`, `{.spec.nodeName}`).
- **include_tasks children do not inherit the include task's tags** (and `apply` is
  gone in ansible-core 2.19) — the teardown per-VM file carries explicit layer tags.
- The authoritative layered-image digest is the registry's `Docker-Content-Digest`
  (podman `RepoDigest` differs by design — plan §11).
- `podman_container_exec` ignores check mode — every destructive exec task carries an
  explicit `not ansible_check_mode` guard (plain `command` tasks auto-skip in check
  mode).
- The region commit's real failure mode is a stale `MEM_ENABLE` bit across warm
  reboots; the region-commit service clears it via setpci + rebinds `cxl_pci`
  (runbook §9), and a drain → poweroff → cold-start fallback remains encoded.
