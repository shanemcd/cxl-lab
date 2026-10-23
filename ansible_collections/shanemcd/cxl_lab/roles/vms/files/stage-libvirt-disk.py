#!/usr/bin/env python3
"""Seed a stopped QCOW2 into libvirt's private tree, without altering its source."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def stage(source, target, backing=None, expected_backing=None):
    source, target = Path(source), Path(target)
    if target.exists():
        print("UNCHANGED")
        return
    info = json.loads(subprocess.check_output(["qemu-img", "info", "--output=json", str(source)]))
    if info["format"] != "qcow2":
        raise ValueError("only QCOW2 sources are supported")
    actual = info.get("full-backing-filename")
    if actual:
        if not backing or not expected_backing or Path(actual).resolve() != Path(expected_backing).resolve():
            raise ValueError("unexpected backing chain; refusing an unsafe rebase")
        if not Path(backing).is_file():
            raise ValueError("runtime backing image must be staged first")
    elif backing or expected_backing:
        raise ValueError("expected a backed overlay, not a standalone image")
    fd, temporary = tempfile.mkstemp(prefix=".seed-", suffix=".qcow2", dir=target.parent)
    os.close(fd)
    try:
        subprocess.run(["cp", "--reflink=auto", "--sparse=always", str(source), temporary], check=True)
        if actual:
            subprocess.run(["qemu-img", "rebase", "-u", "-f", "qcow2", "-F", "qcow2", "-b", str(backing), temporary], check=True)
        os.chmod(temporary, 0o600)
        os.rename(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print("SEEDED")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("target")
    parser.add_argument("--backing")
    parser.add_argument("--expected-backing")
    args = parser.parse_args()
    stage(args.source, args.target, args.backing, args.expected_backing)
