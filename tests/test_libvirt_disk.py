"""Offline disk adoption tests; no libvirt daemon, root or lab disks required."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "stage_disk", ROOT / "ansible_collections/shanemcd/cxl_lab/roles/vms/files/stage-libvirt-disk.py"
)
stage_disk = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stage_disk)


@unittest.skipUnless(shutil.which("qemu-img") and shutil.which("fuser"), "requires qemu-img and psmisc")
class DiskAdoptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cxl-disk-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "original.qcow2"
        self.target = self.root / "runtime.qcow2"
        self.command("qemu-img", "create", "-f", "qcow2", str(self.source), "16M")

    def command(self, *args):
        return subprocess.check_output(args, text=True)

    def info(self, path):
        return json.loads(self.command("qemu-img", "info", "--output=json", str(path)))

    def test_copy_is_private_and_never_overwrites_runtime(self):
        before = self.source.read_bytes()
        stage_disk.stage(str(self.source), str(self.target))
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.source.read_bytes(), before)
        # Runtime may have advanced since adoption. Even an unreadable source
        # must not trigger replacement of an existing runtime disk.
        runtime = self.target.read_bytes()
        stage_disk.stage(str(self.root / "absent-source"), str(self.target))
        self.assertEqual(self.target.read_bytes(), runtime)

    def test_overlay_rebases_only_the_copy(self):
        overlay = self.root / "overlay.qcow2"
        runtime_base = self.root / "runtime-base.qcow2"
        self.command("qemu-img", "create", "-f", "qcow2", "-F", "qcow2", "-b", str(self.source), str(overlay))
        before = overlay.read_bytes()
        stage_disk.stage(str(self.source), str(runtime_base))
        stage_disk.stage(str(overlay), str(self.target), str(runtime_base), str(self.source))
        self.assertEqual(self.info(self.target)["full-backing-filename"], str(runtime_base))
        self.assertEqual(self.info(overlay)["full-backing-filename"], str(self.source))
        self.assertEqual(overlay.read_bytes(), before)

    def test_unexpected_backing_is_rejected_without_output(self):
        with self.assertRaises((ValueError, RuntimeError)):
            stage_disk.stage(str(self.source), str(self.target), str(self.source), "/unexpected/base.qcow2")
        self.assertFalse(self.target.exists())

    def test_open_source_stages_without_output_guard(self):
        # The raw-QEMU fuser guard was removed with the migration code; an open
        # source file is copied like any other (qemu-img reads are safe).
        with self.source.open("rb"):
            stage_disk.stage(str(self.source), str(self.target))
        self.assertTrue(self.target.exists())
        self.assertEqual(self.info(self.target)["format"], "qcow2")


if __name__ == "__main__":
    unittest.main()
