"""Exercise the compatibility interface against a fake virsh, never real VMs."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from ansible.plugins.filter import core
from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "ansible_collections/shanemcd/cxl_lab/roles/vms/templates/vm-run.sh.j2"


class WrapperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.state.write_text("shut off\n")
        env = Environment(undefined=StrictUndefined)
        env.filters.update(to_uuid=core.to_uuid, quote=core.quote)
        self.script = self.root / "run.sh"
        self.script.write_text(env.from_string(TEMPLATE.read_text()).render(
            node={"name": "test-worker"}, libvirt_uri="qemu:///system",
            vm_run_dir=str(self.root), vm_stop_wait_seconds=0))
        virsh = self.root / "virsh"
        virsh.write_text('''#!/bin/bash
set -eu
shift 2 # -c URI
printf '%s\n' "$1" >> "$STUB_ROOT/calls"
if [ "${UNREACHABLE:-0}" = 1 ]; then exit 1; fi
case "$1" in
  domuuid) printf '%s\n' "$STUB_UUID";;
  list) :;;
  domstate) cat "$STUB_ROOT/state";;
  start) echo paused > "$STUB_ROOT/state";;
  resume) echo running > "$STUB_ROOT/state";;
  shutdown) :;;
  destroy) echo 'shut off' > "$STUB_ROOT/state";;
  *) exit 2;;
esac
''')
        virsh.chmod(0o700)
        self.environ = dict(os.environ, PATH=str(self.root) + os.pathsep + os.environ["PATH"],
                            STUB_ROOT=str(self.root), STUB_UUID=core.to_uuid("test-worker"))

    def run_wrapper(self, *args):
        return subprocess.run(["bash", str(self.script), *args], env=self.environ,
                              capture_output=True, text=True, timeout=5)

    def test_status_is_read_only_and_preserves_stopped_code(self):
        self.assertEqual(self.run_wrapper("--status").returncode, 3)
        self.assertFalse((self.root / "test-worker.lifecycle.lock").exists())
        self.state.write_text("running\n")
        self.assertEqual(self.run_wrapper("--status").returncode, 0)
        self.assertEqual(self.state.read_text().strip(), "running")

    def test_validate_inactive_starts_paused_then_destroys(self):
        self.assertEqual(self.run_wrapper("--validate").returncode, 124)
        self.assertEqual(self.state.read_text().strip(), "shut off")
        calls = (self.root / "calls").read_text().splitlines()
        self.assertEqual(calls[-2:], ["start", "destroy"])

    def test_validate_does_not_disturb_running_domain(self):
        self.state.write_text("running\n")
        self.assertEqual(self.run_wrapper("--validate").returncode, 124)
        self.assertNotIn("destroy", (self.root / "calls").read_text())

    def test_wrong_uuid_refuses_lifecycle_action(self):
        self.environ["STUB_UUID"] = "another-domain"
        self.assertNotEqual(self.run_wrapper("--stop").returncode, 0)
        self.assertEqual((self.root / "calls").read_text().splitlines(), ["domuuid"])

    def test_unreachable_daemon_is_not_reported_as_stopped(self):
        self.environ["UNREACHABLE"] = "1"
        self.assertEqual(self.run_wrapper("--status").returncode, 1)
        self.assertEqual(self.run_wrapper("--stop").returncode, 1)

    def test_stop_uses_domain_destroy_after_grace_window(self):
        self.state.write_text("running\n")
        self.assertEqual(self.run_wrapper("--stop").returncode, 0)
        self.assertEqual(self.state.read_text().strip(), "shut off")


if __name__ == "__main__":
    unittest.main()
