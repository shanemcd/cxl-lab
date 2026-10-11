# cxl-lab

Ansible automation for a **disposable OpenShift + QEMU-emulated CXL Type-3 lab**:
a single-node OpenShift (SNO) master plus a configurable fleet of CXL and DRAM
workers (one of each by default) — with a committed 4 GiB emulated CXL region
exposed as System RAM on a CPU-less guest NUMA node on every CXL worker. Built
from a bare Red Hat family server with nothing but SSH (podman and the
rest of the host package set are ensured by the pipeline itself).

All results are **test-only / cache-coherency-unverified**: the region commit
relies on the guest kernel's `CONFIG_CXL_REGION_INVALIDATION_TEST=y` bypass of
`cpu_cache_invalidate_memregion()`. This lab is for CXL software development and
demos, not for cache-coherency validation.

## What you get

After the pipeline runs you have, all idempotent and re-runnable:

- an OpenShift single-node cluster (schedulable master) + the worker fleet
  (one CXL + one DRAM worker by default), all Ready with operators Available;
- every VM as **host-native QEMU** under per-node run scripts (pidfile,
  `--status`/`--stop`/`--validate`), the CXL workers carrying the full emulated
  Type-3 graph (`pxb-cxl` → `cxl-rp` → `cxl-type3` + fixed-memory-window, guest
  NUMA node 1 — deliberately without `host-nodes` binding);
- CXL workers booted into a layered OS image carrying the `cxltest` kernel, with
  the CXL region committed (`region0/commit=1`) and `dax0.0` onlined as System
  RAM on the CPU-less guest NUMA node;
- a 0600 deterministic evidence report in the install dir;
- operational helpers (status, single-worker cold restart) and a per-layer
  tagged teardown.

## How it works

- **Install path**: bootstrap-in-place (BiP) — the master installs OpenShift
  onto its own disk from a live ISO; workers join via ignition fetched from the
  master's machine config server.
- **QEMU**: stock upstream QEMU (11.1.x) built once in a pinned CentOS
  Stream 10 builder container and installed to `/opt/qemu-<version>` on
  the host (RHEL's path — its appstream `qemu-kvm` deliberately lacks the
  CXL backports; nothing is patched). `qemu_source: auto | distro`
  (topology.yml, experimental) switches the lab to the distribution's
  `qemu-system-x86_64` when it carries `pxb-cxl`/`cxl-rp`/`cxl-type3`
  (Fedora's `qemu-system-x86-core` qualifies) — with this machine config
  Fedora 44's QEMU 10.2.2 stalled guest<->host frame flow, so pinned
  remains the default.
- **Network**: a private persistent bridge (`ocpcxlbr0`, 192.168.124.0/24) with
  the host as gateway, one reversible tagged MASQUERADE rule for egress, and a
  host-native dnsmasq serving DNS records + per-MAC DHCP reservations. The
  resolver is load-bearing: ignition fetches each worker's config merge
  **before** applying storage/files, so the initramfs must resolve `api-int`
  via DHCP before the node's static keyfile exists.
- **CXL kernel**: `cxltest` kernel RPMs are built from the RHEL SRPM (one
  config flip: `CONFIG_CXL_REGION_INVALIDATION_TEST=y`) inside the layered
  image build's kernel-builder stage (RHEL 10 GA builder image, entitled CDN
  build deps), baked into the bootc layered OS image, and rolled out to the
  CXL workers — pre-baked from first boot by default, or via the Machine
  Config Operator.
- **Worker OS mode** (`use_prebaked_node_images` in topology.yml, default
  true on this branch): **pre-baked** — the layered image is turned into a
  bootc-image-builder qcow2 (patched for first-boot ignition) and the CXL
  workers boot it on the cxltest kernel from second zero; OpenShift's
  firstboot pivot is aimed at the same layered image via install-time
  MachineConfigs (worker-cxl pool + osImageURL + registry trust), so the
  region commit lands at first boot and the MCO rollout, the second
  per-worker reboot, and the cold-restart recovery machinery are all
  eliminated. Set it to false for the classic flow: stock boot image +
  post-install MCO pivot. Both flows share the registry, the layered image,
  and the verification gates.
- **Host NUMA**: intentionally not pinned — guest RAM is placed by the kernel's
  default first-touch policy.

## Prerequisites

- A dedicated Red Hat family host (developed and validated on RHEL 10) with:
  - ~52 GiB free RAM (36 GiB of guests + assert headroom) and ~210 GiB free
    disk at the VM assets dir (180 GiB nominal overlays + headroom) — vCPU/RAM
    per node sit at the OpenShift-documented minimums while guest disks are
    lab-tuned below them (sparse qcow2 overlays, disposable cluster; see
    topology.yml), and the pipeline asserts the exact guest RAM/vCPU/disk
    sums plus headroom against the host before touching anything,
  - python3 and SSH access (podman is ensured by `01_host_prep` along with
    the rest of the host packages),
  - internet egress (OpenShift payload pulls, artifact downloads),
  - an RHSM registration + entitlements (backs the CDN kernel-SRPM download).
    On Fedora: subscription-manager is not part of a default install and the
    host_prep RHSM assert is RHEL-gated — install it, register the host
    manually, then run the pipeline (the CDN route needs the entitlement
    certs on any distro). If registration fails with `Unit with ID "…" could
    not be found`, the portal holds a stale profile bound to the machine's
    DMI UUID — common on lab hardware with placeholder firmware UUIDs;
    override `dmi.system.uuid` with a fresh UUID in
    /etc/rhsm/facts/redhat.facts and retry,
  - appstream `qemu-kvm` NOT required — the collection builds its own QEMU.
- An OpenShift pull secret (0600 file).
- Ansible-core ≥ 2.19 on the control host (the lab host can manage itself over
  a local connection, which is the default inventory).

## Quick start

```sh
git clone https://github.com/shanemcd/cxl-lab && cd cxl-lab

# third-party collections (pinned):
ansible-galaxy collection install -r requirements.yml -p ansible_collections

# site inputs — see "Variable reference" below. At minimum, place your pull
# secret at the path secrets.yml expects (default /root/pull-secret.txt, 0600).

# full pipeline + verification (~1 h on suitable hardware):
ansible-playbook shanemcd.cxl_lab.site
```

Playbooks are invoked **by name from the repository root** — the repo-root
`ansible.cfg` sets `collections_path = .` so the bundled collection resolves
without installation. Always run from the repo root.

## Playbooks

| Playbook | Invocation | What it does |
|---|---|---|
| `site` | `shanemcd.cxl_lab.site` | Runs the numbered stages in order, 01→08, then verify (see `site.yml`). |
| `01_host_prep` | `shanemcd.cxl_lab.01_host_prep` | Asserts OS family (RedHat), SELinux (conditional), SNO capacity, free ports; RHSM check (RHEL-gated); ensures packages incl. dnsmasq. |
| `02_artifacts` | `shanemcd.cxl_lab.02_artifacts` | Downloads + SHA-verifies installer/oc/RHCOS live ISO and the kernel SRPM (entitled CDN route); builds stock QEMU into `/opt/qemu-<version>` (exists-guard: never rebuilt, never torn down). |
| `03_network` | `shanemcd.cxl_lab.03_network` | Persistent bridge + gateway address, `ip_forward` sysctl drop-in, host `/etc/hosts` block, one tagged MASQUERADE rule, host-native dnsmasq (DNS + DHCP reservations), egress probe (veth+netns → TCP to quay.io). |
| `04_os_image` | `shanemcd.cxl_lab.04_os_image` | cxltest kernel RPMs (multi-stage Containerfile kernel-builder stage in the RHEL 10 GA image, from the SHA-verified SRPM; cached in the artifacts dir — built only when missing) + layered OS image (pinned machine-os-content base + SHA-verified cxltest RPMs), bootc lint, push to the lab registry (published on the bridge gateway), authoritative `Docker-Content-Digest` stamp (0600); in pre-baked mode also the BIB node disk. |
| `05_ignition` | `shanemcd.cxl_lab.05_ignition` | install-config (1 control plane / 0 compute, `bootstrapInPlace`) → manifests → custom node MCs → pre-baked install-time manifests (worker-cxl pool, layered osImageURL, registry trust) → `single-node-ignition-config`; per-node merge (NM keyfile, hosts, hostname, pre-baked MCS pool + region-commit snippet) → 0700/0600 staged ignition (12 h freshness window). |
| `06_vms` | `shanemcd.cxl_lab.06_vms` | Per-node run scripts + qcow2 overlays; master = kargs-patched live-ISO copy + blank virtio target + fw_cfg merged ignition; workers = MCS stubs fetched over DHCP; one fleet-parallel start command; in-role assert of the CXL graph shape. |
| `07_install` | `shanemcd.cxl_lab.07_install` | Heals the 4.22.16 machine-os-puller SA pivot race; CSR approvals; IngressController pinned to the master; operator settle; final asserts (4/4 Ready, clusterversion, routers on master, master osImageURL == pin). |
| `08_mco` | `shanemcd.cxl_lab.08_mco` | MCO path: worker-cxl labels → MachineConfigPool → osImageURL + region-commit MachineConfigs → MCO rollout waits → region commit with guest self-recovery (stale `MEM_ENABLE`) and cold-restart fallback. Pre-baked path: worker-cxl labels + pivot/region-commit verification (the install-time manifests already did the heavy lifting). |
| `verify` | `shanemcd.cxl_lab.verify` | Full checklist per CXL worker (guest), host QEMU liveness + type-3 device count, cluster state; writes the deterministic 0600 evidence report. |
| `teardown` | `shanemcd.cxl_lab.teardown` | Per-layer tagged reverse teardown — see below. **Not in site.** |
| `ops.status` | `shanemcd.cxl_lab.ops.status` | Read-only summary: nodes, clusterversion, VM `--status`, network/resolver/registry, region commit, layered image. |
| `ops.restart_cxl_worker` | `shanemcd.cxl_lab.ops.restart_cxl_worker` | Cold-restart one CXL worker: drain → poweroff → relaunch → Ready → uncordon → region-commit assert. Run with `-e target_worker=worker-cxl-0`. |

Every phase guards itself for idempotent re-runs; a clean re-run of `site`
reports `changed=0`. Mid-install re-runs resume cleanly.

## Teardown

`teardown` removes layers in reverse build order — `mco` → `registry` → `vms` →
`network` → `images` → `artifacts` → `install-data` (secrets last), tagged per
layer:

```sh
ansible-playbook shanemcd.cxl_lab.teardown                 # everything
ansible-playbook shanemcd.cxl_lab.teardown --tags vms      # one layer
ansible-playbook shanemcd.cxl_lab.teardown --check         # dry run (safe:
                                                            # destructive exec
                                                            # tasks are
                                                            # check-guarded)
```

- The vms layer shuts guests down **fleet-parallel** (one poweroff command, one
  fleet poller, `--stop` fallback); the QEMU host prefix is never removed, so a
  same-version rebuild skips the QEMU build entirely.
- Removing the `mco` layer lets the MCO roll CXL workers back to the release
  base image (not waited on); a network-only teardown with running VMs requires
  restarting them (taps re-enslave at VM start); a rebuild after the `vms` layer
  or deeper must regenerate ignition within a fresh 12 h window (automatic).
- The full teardown → `site` rebuild path works from a bare host.

## Variable reference

Everything is a variable; role `defaults/` hold role-scoped knobs, and shared
values live in the **taxonomy** (`inventory/group_vars/all/`) because role
defaults never cross roles. The gate (`scripts/check-no-hardcoding.sh`) fails
any IP/URL/MAC/version literal outside vars files.

Per-site inputs (defaults match the reference lab — all private-range,
self-chosen values):

| File | Key contents |
|---|---|
| `inventory/group_vars/all/secrets.yml` | **Paths only** to the pull secret (0600) and the install SSH keypair; kubeconfig location. Tasks touching them are `no_log`. |
| `inventory/group_vars/all/network.yml` | Lab subnet prefix/CIDR, bridge name, gateway IP (also the registry host + dnsmasq listener), dnsmasq conf/service + DHCP free-range suffixes, NAT rule comment, upstream resolvers (empty = auto-detect from the host: resolv.conf, systemd-resolved, local stub), host-resolution block (API/API-int/ingress names on the master IP). |
| `inventory/group_vars/all/topology.yml` | Worker counts (`cxl_worker_count`/`dram_worker_count`, one each by default) and the node specs they generate, `bip_installation_disk`, VM lifecycle waits, MCP/label/MC names, guest CXL NUMA node, ignition freshness window. |

### Scaling the fleet

The worker lists are generated from `cxl_worker_count` and `dram_worker_count` —
grow the fleet with e.g. `ansible-playbook shanemcd.cxl_lab.site -e
cxl_worker_count=2`. Node names, IP suffixes, MAC suffixes (the hex of the IP
suffix) and CXL bus numbers are assigned deterministically — CXL workers at
IP suffixes 20, 22, 24… with pxb-cxl buses 12, 13, 14…, DRAM workers at 21,
23, 25… — so scaling appends without renumbering existing nodes. `host_prep`
fails fast if the grown fleet exceeds the host's free RAM/vCPU/disk or the
address space reserved for DHCP.
| `inventory/group_vars/all/versions.yml` | OpenShift version + feature set, installer/oc/RHCOS-ISO URLs **with pinned SHA-256s**, kernel NVR + per-source SHA pins, QEMU version/prefix + source SHA, `machine_os_content_ref`, layered image refs. |
| `inventory/group_vars/all/paths.yml` | Install dir (secrets, stamps), VM assets dir and its subdirs (images/ignition/logs/scripts/run/registry), verification dir, per-master boot-ISO name. |
| `inventory/group_vars/all/host.yml` | RHSM knobs + host package list. |
| `inventory/hosts.yml` | Lab host connection (local by default). |

## Development model

There is no molecule and no test harness — the automation is developed by
running it against the disposable lab host:

1. Run the playbook on the lab host.
2. Observe the failure.
3. Fix the automation — never perform by hand what the automation should do.
4. Re-run until clean. **Idempotency is mandatory**: a re-run reports
   `changed=0`.

Read-only asserts run freely; mutating phases run as they land; `teardown`
resets state between attempts. Using the automation's own primitives to resume
an interrupted run is acceptable.

Repository gates (run before every commit, from the repo root):

```sh
scripts/check-no-hardcoding.sh     # no literal config values outside vars files
scripts/check-public-clean.sh      # no private/session references in tracked files
yamllint ansible_collections/shanemcd/cxl_lab inventory
ansible-lint ansible_collections/shanemcd/cxl_lab inventory
ansible-playbook shanemcd.cxl_lab.site --syntax-check   # each playbook
```

## Safety properties

- QEMU-emulated CXL only — physical CXL passthrough args are never emitted; the
  CXL graph shape (pxb-cxl/cxl-type3/fmw, CPU-less guest node, no `host-nodes`
  binding) is asserted in-role.
- Host NUMA placement is not gated or pinned — first-touch placement.
- Secrets are path-referenced only, `no_log` on tasks that touch them, 0700/0600.
- Ignition must be fresh (12 h window); VM starts refuse stale ignition.
- Process management via pidfiles and exact pids — never `pkill -f`.
- Guest root operations via `ssh core@… 'sudo bash -s'` with the install keypair.
- Host-OS agnostic within the RedHat family (`os_family` assert, SELinux-conditional).
- New disposable cluster only; teardown reverses every host change it makes
  except the QEMU prefix.

## Known gotchas (encoded in the roles)

- **Metal live-ISO**: the RHCOS live ISO boots `platform=metal` with a VGA-only
  console (fw_cfg ignition ignored, serial silent). The vms role prepares a
  per-master ISO copy with `coreos-installer iso kargs modify` appending
  `ignition.platform.id=qemu console=ttyS0,115200n8`; ignition itself stays in
  fw_cfg.
- **The lab resolver is load-bearing**: ignition's fetch-before-apply ordering
  plus the DNS-SAN-only MCS certificate make dnsmasq DNS + per-MAC DHCP
  reservations required (first boot DHCP, reboots static keyfile, same address).
- **Installed-vs-live master gate**: the installed master retains an inactive
  `bootkube.service`; the real signal is the master's `/sysroot` living on
  `bip_installation_disk` plus the MCS port answering.
- **machine-os-puller SA race** (OpenShift 4.22.16 BiP): the SA's stale
  `internal-registry-pull-secret-ref` annotation across the live→installed
  transition degrades the MCO; `07_install` drops the annotation and the MCO
  resync heals.
- **oc column formats are unstable**: `oc get -o wide` renders RESTARTS as
  `2 (23m ago)` (two fields) once aged — positional awk indexes silently check
  the wrong column. Placement is polled via jsonpath (`{.status.phase}`,
  `{.spec.nodeName}`).
- **include_tasks children do not inherit the include task's tags** (and
  `apply` is gone in ansible-core 2.19) — the teardown per-VM file carries
  explicit layer tags.
- The authoritative layered-image digest is the registry's
  `Docker-Content-Digest` (podman `RepoDigest` differs by design).
- `podman_container_exec` ignores check mode — every destructive exec task
  carries an explicit `not ansible_check_mode` guard (plain `command` tasks
  auto-skip in check mode).
- The region commit's real failure mode is a stale `MEM_ENABLE` bit across warm
  reboots; the region-commit service clears it via setpci + rebinds `cxl_pci`,
  and a drain → poweroff → cold-start fallback remains encoded.
