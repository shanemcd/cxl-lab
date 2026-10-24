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
- every VM as a persistent **libvirt domain** (`qemu:///system`) with autostart;
  per-node run scripts delegate `--status`/`--stop`/`--validate` to libvirt,
  with the CXL workers carrying the full emulated
  Type-3 graph (`pxb-cxl` → `cxl-rp` → `cxl-type3` + fixed-memory-window, guest
  NUMA node 1 — deliberately without `host-nodes` binding);
- CXL workers booted into a layered OS image carrying the `cxltest` kernel, with
  the CXL region committed (`region0/commit=1`) and `dax0.0` onlined as System
  RAM on the CPU-less guest NUMA node;
- a 0600 deterministic evidence report in the install dir;
- a dynamically-provisioned StorageClass (`lab-nfs`) — host kernel nfsd + the
  in-cluster NFS subdir provisioner — so stateful add-ons (Perses, CDI,
  VirtualMachines) have persistent volumes;
- observability: the Cluster Observability Operator with the **Red Hat build of
  Perses** (console Observe → Dashboards), persisting on `lab-nfs`, wired to
  the built-in Thanos Querier with per-user RBAC, plus CXL memory-tier
  recording rules (`openshift:node:memory:tier:*` — the tier 1/cxl slot of the
  openshift memory-tier vocabulary) and two lab dashboards (cluster overview,
  infra-cxl-glance);
- operational helpers (status, single-worker cold restart) and a per-layer
  tagged teardown.

## How it works

- **Install path**: bootstrap-in-place (BiP) — the master installs OpenShift
  onto its own disk from a live ISO; workers join via ignition fetched from the
  master's machine config server.
- **QEMU**: stock upstream QEMU (11.1.x) built once in the shared RHEL 10
  builder container (entitled CDN build deps via the host RHSM
  entitlement, meson/ninja from pip — the entitlement carries no CRB) and
  installed to `/opt/qemu-<version>` on
  the host (RHEL's path — its appstream `qemu-kvm` deliberately lacks the
  CXL backports; nothing is patched). `qemu_source: auto | distro`
  (topology.yml, experimental) switches the lab to the distribution's
  `qemu-system-x86_64` when it carries `pxb-cxl`/`cxl-rp`/`cxl-type3`
  (Fedora's `qemu-system-x86-core` qualifies) — with this machine config
  Fedora 44's QEMU 10.2.2 stalled guest<->host frame flow, so pinned
  remains the default.
- **Lifecycle**: libvirt owns QEMU processes, monitor connections, PID files,
  disks, and automatic restart after guest reboots. The pinned QEMU remains
  the emulator; native domain XML describes the usual devices and a small
  `qemu:commandline` extension supplies the unsupported CXL graph. Runtime
  disks and ignition are private system-owned copies in
  `/var/lib/libvirt/images/cxl-lab`; originals in the workspace are not live disks.
- **Network**: a persistent libvirt NAT network (`cxl-lab`) creates the private
  bridge (`ocpcxlbr0`, 192.168.124.0/24). Libvirt owns NAT and its dedicated
  dnsmasq, serving DNS records, wildcard ingress, and per-MAC DHCP reservations. The
  resolver is load-bearing: ignition fetches each worker's config merge
  **before** applying storage/files, so the initramfs must resolve `api-int`
  via DHCP before the node's static keyfile exists.
- **CXL kernel**: `cxltest` kernel RPMs are built from the RHEL SRPM (one
  config flip: `CONFIG_CXL_REGION_INVALIDATION_TEST=y`) inside the layered
  image build's kernel-builder stage (RHEL 10 GA builder image, entitled CDN
  build deps), baked into the bootc layered OS image, and baked into the
  bootc-image-builder qcow2 the CXL workers boot from first boot.
- **Worker OS**: the layered image is turned into a bootc-image-builder
  qcow2 (patched for first-boot ignition) and the CXL workers boot it on
  the cxltest kernel from second zero. OpenShift's firstboot OS check
  compares each node's booted image ref against its pool's osImageURL, so
  the install-time worker-cxl MachineConfigs (pool + osImageURL) carry the
  layered image's local ref — the exact string a BIB-booted worker reports
  as booted — and the check matches: no pivot, no MCO rollout, no second
  per-worker reboot. The region commit lands at first boot.
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
# secret at the path secrets.yml expects (default ~/pull-secret.txt in the invoking user's home, 0600).

# full pipeline + verification (~1 h on suitable hardware; the heavy
# stages overlap — see the playbook table below):
ansible-playbook shanemcd.cxl_lab.site
```

Playbooks are invoked **by name from the repository root** — the repo-root
`ansible.cfg` sets `collections_path = .` so the bundled collection resolves
without installation. Always run from the repo root.

## Workspace ownership

Run Ansible as your normal account; the playbooks escalate (`become: true`) for host changes, so artifacts the stages create land root-owned in your home. At the end of a run the lab hands the affected trees (`install_dir`, `vm_assets_dir`, the artifact cache, the controller kubeconfig directory) back to the invoking user — only files that are actually root-owned are changed, and the NFS export (cluster PVC data) and the QEMU build tree are left alone. A failed run simply leaves the handback for the next successful run.

The workspace account is resolved on the managed host before escalation (actual UID, primary GID, passwd home). When provisioning for another account, set `lab_user` explicitly.

The controller kubeconfig copy (`~/.config/cxl-lab/kubeconfig`) is written with an explicit owner during `install_config`, so `oc` works without sudo even mid-run:

```sh
export KUBECONFIG="$HOME/.config/cxl-lab/kubeconfig"
oc whoami
```

System configuration and the installed `/opt/qemu-*` tree remain root-owned; VM management and rootful container builds still require escalation.

## Playbooks

| Playbook | Invocation | What it does |
|---|---|---|
| `site` | `shanemcd.cxl_lab.site` | Runs the numbered stages in order, 01→11, then verify (see `site.yml`). The stages overlap by design: the master installs and converges while the layered image builds. |
| `01_host_prep` | `shanemcd.cxl_lab.01_host_prep` | Asserts OS family (RedHat), SELinux (conditional), SNO capacity; RHSM check (RHEL-gated); ensures packages incl. libvirt QEMU/network drivers and disk-use checks. |
| `02_artifacts` | `shanemcd.cxl_lab.02_artifacts` | Downloads + SHA-verifies installer/oc/RHCOS live ISO and the kernel SRPM (entitled CDN route); launches the stock QEMU build into `/opt/qemu-<version>` (exists-guard: never rebuilt, never torn down) fire-and-forget — the master stage collects it. |
| `03_network` | `shanemcd.cxl_lab.03_network` | Persistent autostart libvirt NAT network (bridge, DNS, wildcard ingress, DHCP reservations), ordered modular libvirt services, `ip_forward`, host `/etc/hosts`, egress probe (veth+netns → TCP to quay.io). |
| `04_ignition` | `shanemcd.cxl_lab.04_ignition` | install-config (1 control plane / 0 compute, `bootstrapInPlace`) → manifests → custom node MCs → install-time worker-cxl manifests (pool, layered osImageURL) → `single-node-ignition-config`; per-node merge (NM keyfile, hosts, hostname, MCS pool stub + region-commit snippet) → 0700/0600 staged ignition (12 h freshness window). |
| `05_master` | `shanemcd.cxl_lab.05_master` | Collects the in-flight QEMU build (fresh-host path); persistent domain XML, private runtime disk copies, libvirt-backed run scripts; master = kargs-patched live-ISO copy + blank virtio target + fw_cfg merged ignition; starts the master and holds until its install-to-disk reboot. Runs before the layered image — the master needs only ignition + network, so its install and convergence overlap the kernel/image builds. |
| `06_os_image` | `shanemcd.cxl_lab.06_os_image` | cxltest kernel RPMs (multi-stage Containerfile kernel-builder stage in the RHEL 10 GA image, from the SHA-verified SRPM; cached in the artifacts dir — built only when missing) + layered OS image (pinned machine-os-content base + SHA-verified cxltest RPMs), bootc lint, and the BIB node disk the workers boot. |
| `07_workers` | `shanemcd.cxl_lab.07_workers` | Worker qcow2 overlays over the pre-baked BIB disk; persistent worker domains and one fleet-parallel start command; kernel-boot evidence from serial or SSH; holds until the installed master's Machine Config Server answers (the workers ride ignition's own retry loop against it from their first boot). |
| `08_install` | `shanemcd.cxl_lab.08_install` | Heals the 4.22.16 machine-os-puller SA pivot race; CSR approvals; IngressController pinned to the master; operator settle; final asserts (4/4 Ready, clusterversion, routers on master, master osImageURL == pin). |
| `09_region_commit` | `shanemcd.cxl_lab.09_region_commit` | Labels the CXL workers into the install-time worker-cxl pool; verifies the outcome — layered image + cxltest kernel booted, region-commit service active, region committed (the service itself ships via the workers' first-boot ignition). |
| `10_storage` | `shanemcd.cxl_lab.10_storage` | The lab StorageClass (`lab-nfs`): host kernel nfsd exports the assets tree to the lab subnet; the in-cluster `nfs-subdir-external-provisioner` (with the OCP 5.x hostmount SCC grants it needs) dynamically provisions per-PVC subdirectories. LVM Storage is absent from the reachable catalogs and the in-catalog alternatives are Ceph-family — outside the lab host's RAM budget. |
| `11_observability` | `shanemcd.cxl_lab.11_observability` | Cluster Observability Operator (Red Hat build of Perses) from `redhat-operators`; monitoring UIPlugin (console Observe → Dashboards); Perses persisting on the `lab-nfs` StorageClass; Thanos Querier datasource (per-user RBAC via the console plugin, TLS-anchored on the service CA); CXL memory-tier recording rules (`openshift:node:memory:tier:*`) + cluster-overview, infra-cxl-glance and VM Explorer dashboards. |
| `verify` | `shanemcd.cxl_lab.verify` | Full checklist per CXL worker (guest), host QEMU liveness + type-3 device count, cluster state; writes the deterministic 0600 evidence report. |
| `teardown` | `shanemcd.cxl_lab.teardown` | Per-layer tagged reverse teardown — see below. **Not in site.** |
| `ops.status` | `shanemcd.cxl_lab.ops.status` | Read-only summary: nodes, clusterversion, VM `--status`, network/resolver, region commit, layered image. |
| `ops.restart_cxl_worker` | `shanemcd.cxl_lab.ops.restart_cxl_worker` | Cold-restart one CXL worker: drain → poweroff → relaunch → Ready → uncordon → region-commit assert. Run with `-e target_worker=worker-cxl-0`. |

Every phase guards itself for idempotent re-runs; a clean re-run of `site`
reports `changed=0`. Mid-install re-runs resume cleanly.

## Teardown

`teardown` removes layers in reverse build order — `storage` → `mco` → `vms` →
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
  same-version rebuild skips the QEMU build entirely. It undefines only the
  inventory domains and removes their known runtime files; it never asks
  libvirt to recursively remove storage.
- The storage layer removes the `lab-storage` namespace (provisioner,
  StorageClass) when the cluster answers, then the host side: exports drop-in,
  nfs-server service, export tree. Cluster-side COO/Perses objects live and die
  with the VMs (no host state), so observability has no teardown layer of its
  own.
- Removing the `mco` layer lets the MCO roll CXL workers back to the release
  base image (not waited on). Network teardown refuses attached libvirt ports:
  remove the VM layer first. Only the lab network's matching UUID may be removed.
- A rebuild after the VM layer or deeper regenerates ignition within a fresh
  12 h window. Existing installed domains can restart without regenerating ignition.
- Shared libvirt packages/services and their network-before-domain service
  ordering remain installed, as does the QEMU prefix. Unrelated domains,
  networks, and storage are not teardown targets.

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
| `inventory/group_vars/all/network.yml` | Lab subnet prefix/CIDR, libvirt network/bridge name, gateway IP, DHCP free-range suffixes, upstream resolvers (empty = auto-detect from the host: resolv.conf, systemd-resolved, local stub), host-resolution block (API/API-int/ingress names on the master IP). |
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
| `inventory/group_vars/all/paths.yml` | Install dir (secrets, stamps), VM assets dir and its subdirs (images/ignition/logs/scripts/run), verification dir, per-master boot-ISO name. |
| `inventory/group_vars/all/host.yml` | RHSM knobs + host package list. |
| `inventory/group_vars/all/storage.yml` | NFS export dir/options, exports drop-in path, nfs-server service + package names, provisioner namespace/k8s names, StorageClass name. |
| `inventory/group_vars/all/observability.yml` | COO namespace/subscription/channel/catalog, UIPlugin name, Perses name + storage class/size, instance selector label, content namespace, datasource name + Thanos Querier URL, rule name. |
| `inventory/hosts.yml` | Lab host connection (local by default). |

## Development model

Unit tests cover domain XML, safe runtime-disk staging, and Perses dashboards:

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

They supplement integration runs against the disposable lab host:

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
  binding) is covered by XML tests and live guest verification.
- Host NUMA placement is not gated or pinned — first-touch placement.
- Secrets are path-referenced only, `no_log` on tasks that touch them, 0700/0600.
- Fresh installs use the ignition freshness checks (12 h window); restarting
  an installed domain does not invalidate its original ignition.
- Lifecycle commands address libvirt domains with deterministic UUID checks;
  compatibility PID paths point at libvirt's PID files — never `pkill -f`.
- Guest root operations via `ssh core@… 'sudo bash -s'` with the install keypair.
- Host-OS agnostic within the RedHat family (`os_family` assert, SELinux-conditional).
- New disposable installs and explicit stopped-disk adoption are supported.
  Domain/network name collisions with different UUIDs are rejected. Shared
  libvirt services and the QEMU prefix are retained by teardown.

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
- **The CI artifacts service is lazy, flappy, and forgetful** (nightly
  profiles): client-tool tarballs 404 for tens of minutes on first hit
  (on-demand extraction), the directory index flaps ready/extracting, a
  download issued mid-extraction can arrive truncated, and old builds are
  garbage-collected outright. The artifacts role therefore HEAD-polls until
  the service serves each absent nightly tarball (bounded; see
  `artifacts_ci_readiness_*` in the role defaults — skipped when the
  tarballs are already on disk) and re-fetches pinned downloads on checksum
  mismatch. A build whose tools never appear within the poll window is
  GC'd: re-pin with `scripts/find-nightly-pins.sh`, which prints the
  `versions.yml` profile block for the latest accepted nightly.
- **oc column formats are unstable**: `oc get -o wide` renders RESTARTS as
  `2 (23m ago)` (two fields) once aged — positional awk indexes silently check
  the wrong column. Placement is polled via jsonpath (`{.status.phase}`,
  `{.spec.nodeName}`).
- **include_tasks children do not inherit the include task's tags** (and
  `apply` is gone in ansible-core 2.19) — the teardown per-VM file carries
  explicit layer tags.
- `podman_container_exec` ignores check mode — every destructive exec task
  carries an explicit `not ansible_check_mode` guard (plain `command` tasks
  auto-skip in check mode).
- The region commit's real failure mode is a stale `MEM_ENABLE` bit across warm
  reboots; the region-commit service clears it via setpci + rebinds `cxl_pci`,
  and a drain → poweroff → cold-start fallback remains encoded.
- **LVM Storage is absent from the catalogs this lab reaches** (checked by
  subscription resolution against both the nightly's `redhat-operators` and
  the published `redhat-operator-index` v5.0); the in-catalog alternatives
  are Ceph-family, outside the lab host's RAM budget. The lab therefore runs
  a host-native nfsd + `nfs-subdir-external-provisioner` — same host-service
  pattern as dnsmasq and the install registry.
- **NFS provisioner SCCs (OCP 5.x)**: uid 0 + an `nfs` volume requires
  `hostmount-anyuid` (and its `-v2` sibling), whose seccomp lists are empty —
  the container must NOT set `seccompProfile` or admission rejects it. The
  grants are owned declaratively as ClusterRoleBindings.
- **Perses storage is patch-managed**: the COO owns the `Perses` CR, so the lab
  JSON-patch-adds `spec.storage.pvcTemplate` (a merge patch leaves the COO's
  default `emptyDir` beside it and the CRD rejects the pair as mutually
  exclusive). Switching storage later wipes the database — the operator only
  re-reconciles `PersesDashboard`/`PersesDatasource` CRs on CR events, so
  re-apply (or annotate) them after a storage flip to repopulate.
- **The CXL memory-tier rules need the consumer-side exporter**: they map
  `node_memory_tier_*` from the memory-tier-exporter DaemonSet (part of the
  memory-tiering work that consumes this lab, not this repo) onto the
  `openshift:*:memory:tier:*` vocabulary; without it they evaluate to empty
  series, harmlessly.

### Filtering dashboards at cluster scale

Under **Observe → Dashboards**, the lab owns three dashboards in the observability content namespace (default `perses-dev`):

- **Virtualization / VM Explorer**: cascading **OCP node → VM namespace → VM** selectors and a **VM chart limit** of 5, 10 (default), 20 or 50. All charts use the same VM/node pairs, ranked by configured guest RAM at the end of the selected time range. Memory, CPU, network and storage therefore describe the same selection. Select a specific VM to inspect it regardless of its rank. Metrics must be exposed by KubeVirt; missing data is unknown, not zero.
- **Infrastructure / CXL / Glance**: the same selectors; node capacity and tiering charts follow the node selection, while VM placement charts also follow namespace and VM. VM placement ranks the largest anonymous-memory consumers; the separate CXL chart ranks CXL consumers. Both keep their selected set fixed across the range. Legends include namespace, VM and node, so same-name VMs and migration copies remain distinct. Anonymous memory includes QEMU overhead and excludes explicit HugeTLB guest memory. The placement chart uses unstacked lines so DRAM cannot obscure CXL.
- **OpenShift Cluster Overview**: an OCP-node selector scopes CPU, memory and top-pod charts. API availability remains explicitly cluster-wide.

The dropdowns default to all nodes/namespaces/VMs. Clear a downstream selection to **All** when changing a parent scope if the previously selected VM is not present there. The VM limit caps returned VM/node pairs, not the cost of scanning all matching series; select a node and namespace first on large clusters. Migration can show one VM on two nodes, and guest-reported use, QEMU residency and tier residency are different measurements.

CNV/HCO and other operators own their own dashboards. The lab does not patch those resources or reconcile the older consumer dashboard set over them. The standalone VM Explorer provides a durable node-scoped view without fighting operator reconciliation.

To update only lab-owned dashboard content, the datasource and CXL rules, without reinstalling operators or changing workloads:

```sh
cd cxl-lab
ansible-playbook shanemcd.cxl_lab.11_observability --tags observability_content
```

Template regression tests are included in `python -m unittest discover -s tests -p 'test_*.py'` (use the project's virtual environment). They cover declared variables, cascading selectors, consistent bounded VM selection, VM/node/namespace identity, overview label matching and layout references.
