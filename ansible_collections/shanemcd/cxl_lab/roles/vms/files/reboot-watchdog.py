#!/usr/bin/env python3
"""Reboot-hang watchdog client: issue an HMP system_reset to a QEMU monitor socket.

The master's install-to-disk reboot can hang under QEMU (the guest's
reset request does not propagate to a platform reset): the CPU halts in
kernel space and the serial log freezes on "reboot: Restarting system".
This client speaks just enough HMP to reset the VM, which then boots the
installed disk system. It is staged by roles/vms/tasks/workers.yml and
invoked from the master install-wait gate when that exact frozen state
is detected; failures there are non-fatal (`|| true`).
"""

import socket
import sys
import time

s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect(sys.argv[1])
s.settimeout(3)
s.recv(4096)  # HMP greeting banner
s.sendall(b"system_reset\n")
time.sleep(1)
try:
    s.recv(65536)
except socket.timeout:
    pass
print("watchdog: issued system_reset (frozen install-to-disk reboot)")
