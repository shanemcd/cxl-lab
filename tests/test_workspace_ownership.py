"""Metadata-only tests; never inspect the real lab or read secret contents."""
import importlib.util
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / 'ansible_collections/shanemcd/cxl_lab/plugins/modules/workspace_ownership.py'
spec = importlib.util.spec_from_file_location('workspace_ownership', MODULE)
workspace = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workspace)


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.root = self.home / 'install'
        self.root.mkdir()
        self.args = dict(roots=[str(self.root)], home=str(self.home),
                         uid=os.getuid(), gid=os.getgid(), private_roots=[str(self.root)])

    def test_private_modes_and_idempotence(self):
        auth = self.root / 'auth'
        auth.mkdir(mode=0o755)
        config = auth / 'kubeconfig'
        config.write_text('test fixture, not a credential')
        tool = self.root / 'oc'
        tool.write_text('fixture')
        tool.chmod(0o755)
        self.assertGreater(workspace.reconcile(**self.args), 0)
        self.assertEqual(stat.S_IMODE(auth.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(tool.stat().st_mode), 0o700)
        self.assertEqual(workspace.reconcile(**self.args), 0)

    def test_check_mode_changes_nothing(self):
        original = stat.S_IMODE(self.root.stat().st_mode)
        self.assertGreater(workspace.reconcile(**self.args, check=True), 0)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), original)

    def test_nfs_tree_is_untouched(self):
        nfs = self.root / 'nfs'
        nfs.mkdir(mode=0o777)
        pvc = nfs / 'pvc'
        pvc.write_text('fixture')
        before = (nfs.stat(), pvc.stat())
        workspace.reconcile(**self.args, exclude=[str(nfs)])
        for path, info in zip([nfs, pvc], before):
            now = path.stat()
            self.assertEqual((now.st_uid, now.st_gid, now.st_mode, now.st_ctime_ns),
                             (info.st_uid, info.st_gid, info.st_mode, info.st_ctime_ns))

    def test_symlink_and_hardlink_targets_untouched(self):
        external = self.home / 'outside'
        external.write_text('fixture')
        external.chmod(0o644)
        (self.root / 'link').symlink_to(external)
        os.link(external, self.root / 'hardlink')
        before = external.stat().st_ctime_ns
        workspace.reconcile(**self.args)
        self.assertEqual(external.stat().st_ctime_ns, before)
        self.assertEqual(stat.S_IMODE(external.stat().st_mode), 0o644)

    def test_refuses_redirected_root(self):
        alias = self.home / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        self.args['roots'] = [str(alias)]
        with self.assertRaises(ValueError):
            workspace.reconcile(**self.args)

    def test_validates_all_roots_before_changes(self):
        self.args['roots'].append('/etc')
        before = self.root.stat().st_ctime_ns
        with self.assertRaises(ValueError):
            workspace.reconcile(**self.args)
        self.assertEqual(self.root.stat().st_ctime_ns, before)

    def test_home_itself_is_not_a_workspace(self):
        self.args['roots'] = [str(self.home)]
        with self.assertRaises(ValueError):
            workspace.reconcile(**self.args)

    def test_nested_mount_is_skipped(self):
        mounted = self.root / 'mount'
        mounted.mkdir(mode=0o755)
        real_ismount = os.path.ismount
        with patch.object(workspace.os.path, 'ismount',
                          side_effect=lambda p: str(p) == str(mounted) or real_ismount(p)):
            workspace.reconcile(**self.args)
        self.assertEqual(stat.S_IMODE(mounted.stat().st_mode), 0o755)

    def test_missing_workspaces_are_not_created(self):
        missing = self.home / 'absent'
        self.args['roots'] = [str(missing)]
        self.assertEqual(workspace.reconcile(**self.args), 0)
        self.assertFalse(missing.exists())


if __name__ == '__main__':
    unittest.main()
