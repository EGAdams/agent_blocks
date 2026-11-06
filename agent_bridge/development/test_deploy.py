"""Deployment gate regression tests, using temporary repositories only."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location("deploy", Path(__file__).with_name("deploy.py"))
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.prod, self.dev, self.baseline = root/"prod", root/"dev", root/"baseline.json"
        self.name = "lancedb_memory/receptionist_team/example.py"
        for top in [self.prod, self.dev]:
            (top/self.name).parent.mkdir(parents=True)
            (top/self.name).write_text("value = 1\n")
        self.baseline.write_text(json.dumps({self.name:deploy.digest(b"value = 1\n")}))
        p = patch.multiple(deploy, PRODUCTION=self.prod, DEVELOPMENT=self.dev, BASELINE=self.baseline)
        p.start();self.addCleanup(p.stop)

    def test_no_changes_is_empty(self): self.assertEqual(deploy.plan()[0], [])

    def test_exact_diff_and_hashes(self):
        (self.dev/self.name).write_text("value = 2\n")
        changes, digest = deploy.plan()
        self.assertEqual(changes[0]["path"], self.name)
        self.assertIn("+value = 2", changes[0]["diff"])
        self.assertEqual(len(digest),64)

    def test_concurrent_production_changes_are_refused(self):
        (self.prod/self.name).write_text("value = 3\n")
        (self.dev/self.name).write_text("value = 2\n")
        with self.assertRaisesRegex(ValueError,"production changed"): deploy.plan()

    def test_configuration_and_symlinks_are_refused(self):
        config=self.dev/"lancedb_memory/receptionist_team/settings.json"
        config.write_text('{}')
        with self.assertRaisesRegex(ValueError,"protected path"): deploy.plan()
        config.unlink()
        (self.dev/self.name).unlink();(self.dev/self.name).symlink_to(self.prod/self.name)
        with self.assertRaisesRegex(ValueError,"symlink"): deploy.plan()

    def test_failed_gate_never_deploys(self):
        (self.dev/self.name).write_text("value = 2\n")
        _, digest=deploy.plan()
        fake=SimpleNamespace(run=lambda *args:SimpleNamespace(passed=False,output_tail="test failed"))
        with patch.object(sys,'argv',['deploy.py','apply','--reviewed-digest',digest]), \
             patch.object(deploy,'jail',return_value=None),patch.object(deploy,'JailedCheckRunner',return_value=fake):
            with self.assertRaisesRegex(SystemExit,"Gate failed"):deploy.main()
        self.assertEqual((self.prod/self.name).read_text(),"value = 1\n")

    def test_reviewed_passing_change_is_backed_up_then_deployed(self):
        (self.dev/self.name).write_text("value = 2\n")
        _, digest=deploy.plan()
        fake=SimpleNamespace(run=lambda *args:SimpleNamespace(passed=True,output_tail="pass"))
        with patch.object(sys,'argv',['deploy.py','apply','--reviewed-digest',digest]), \
             patch.object(deploy,'jail',return_value=None),patch.object(deploy,'JailedCheckRunner',return_value=fake):
            deploy.main()
        self.assertEqual((self.prod/self.name).read_text(),"value = 2\n")
        saved=list(self.baseline.parent.glob('deployment_*/'+self.name))
        self.assertEqual(len(saved),1)
        self.assertEqual(saved[0].read_text(),"value = 1\n")
        self.assertEqual(deploy.plan()[0],[])


if __name__ == "__main__":unittest.main()
