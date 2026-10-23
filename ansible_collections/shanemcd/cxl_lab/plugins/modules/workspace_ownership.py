#!/usr/bin/python
"""Reconcile lab artifacts without following links or entering PVC data."""
from __future__ import annotations

import os
import stat

DOCUMENTATION = r'''
---
module: workspace_ownership
short_description: Reconcile ownership of existing private lab workspaces
options:
  roots:
    type: list
    elements: path
    required: true
  home:
    type: path
    required: true
  uid:
    type: int
    required: true
  gid:
    type: int
    required: true
  private_roots:
    type: list
    elements: path
    default: []
  exclude:
    type: list
    elements: path
    default: []
notes:
  - Only existing paths strictly beneath home are accepted.
  - Symlinks, multiply-linked files, nested mounts and excluded trees are not modified.
  - No file contents are read. Private directories are 0700; private files are 0600
    or 0700 when already executable. Other file modes are preserved.
  - Does not create workspaces or change system directories.
author: cxl-lab contributors
'''
EXAMPLES = r'''
- name: Repair existing installer files
  shanemcd.cxl_lab.workspace_ownership:
    roots: ["/home/alice/ocp-install-cxl"]
    private_roots: ["/home/alice/ocp-install-cxl"]
    home: /home/alice
    uid: 1000
    gid: 1000
'''
RETURN = r'''
updated:
  description: Number of existing entries requiring ownership or mode changes.
  type: int
  returned: always
'''


def beneath(path, parent):
    return os.path.commonpath([path, parent]) == parent


def reconcile(roots, home, uid, gid, private_roots=(), exclude=(), check=False):
    home = os.path.abspath(home)
    roots = list(dict.fromkeys(os.path.abspath(p) for p in roots))
    private_roots = [os.path.abspath(p) for p in private_roots]
    exclude = [os.path.abspath(p) for p in exclude]
    # Validate every root before changing anything; refuse redirected ancestors.
    for root in roots:
        if root == home or not beneath(root, home):
            raise ValueError("Workspace roots must be strictly beneath the configured user home")
        if os.path.realpath(root) != root:
            raise ValueError("Workspace roots and their ancestors must not be symlinks")
    updated = 0
    seen = set()

    def visit(path, root=False, parent_fd=None, name=None):
        nonlocal updated
        if path in seen or any(beneath(path, p) for p in exclude):
            return
        seen.add(path)
        try:
            info = os.stat(name if parent_fd is not None else path,
                           dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:  # Running VMs can rotate logs/remove pidfiles.
            return
        if stat.S_ISLNK(info.st_mode) or (not root and os.path.ismount(path)):
            return
        directory = stat.S_ISDIR(info.st_mode)
        if not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink > 1):
            return
        mode = stat.S_IMODE(info.st_mode)
        private = any(beneath(path, p) for p in private_roots)
        desired = (0o700 if directory or mode & 0o111 else 0o600) if private else mode
        needs_change = (info.st_uid, info.st_gid) != (uid, gid) or mode != desired
        if needs_change:
            updated += 1
        # Walk relative to an open directory descriptor: replacing a directory
        # with a symlink during repair cannot redirect traversal outside the tree.
        fd = os.open(name if parent_fd is not None else path,
                     os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=parent_fd)
        try:
            current = os.fstat(fd)
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError("Workspace entry changed during ownership repair; retry")
            if needs_change and not check:
                os.fchown(fd, uid, gid)
                os.fchmod(fd, desired)
            if directory:
                with os.scandir(fd) as entries:
                    for entry in entries:
                        visit(os.path.join(path, entry.name), parent_fd=fd, name=entry.name)
        finally:
            os.close(fd)

    for root in roots:
        visit(root, root=True)
    return updated


def main():
    from ansible.module_utils.basic import AnsibleModule
    module = AnsibleModule(argument_spec=dict(
        roots=dict(type='list', elements='path', required=True),
        home=dict(type='path', required=True),
        uid=dict(type='int', required=True), gid=dict(type='int', required=True),
        private_roots=dict(type='list', elements='path', default=[]),
        exclude=dict(type='list', elements='path', default=[]),
    ), supports_check_mode=True)
    try:
        updated = reconcile(**module.params, check=module.check_mode)
    except (OSError, ValueError) as exc:
        module.fail_json(msg=str(exc))
    module.exit_json(changed=bool(updated), updated=updated)


if __name__ == '__main__':
    main()
