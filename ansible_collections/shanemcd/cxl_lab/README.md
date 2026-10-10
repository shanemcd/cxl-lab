# shanemcd.cxl_lab

The `shanemcd.cxl_lab` Ansible collection: automation for a disposable
OpenShift + QEMU-emulated CXL Type-3 lab (single-node OpenShift master,
2 CXL workers, 1 DRAM worker; committed 4 GiB emulated CXL region as System
RAM on a CPU-less guest NUMA node on both CXL workers).

The repository README is the documentation:

https://github.com/shanemcd/cxl-lab

Quick start from the repository root (the bundled `ansible.cfg` resolves this
collection without installation):

```sh
ansible-galaxy collection install -r requirements.yml -p ansible_collections
ansible-playbook shanemcd.cxl_lab.site
```
