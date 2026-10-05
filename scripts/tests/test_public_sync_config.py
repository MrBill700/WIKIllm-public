"""Public sync configuration: trusted paths and provenance gates, on real fixture repositories.

Run: python -B scripts/tests/test_public_sync_config.py
PUBLIC_SYNC_TEST_SCRIPT can select an older implementation for a red control.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
SCRIPT = Path(os.environ.get('PUBLIC_SYNC_TEST_SCRIPT') or
              Path(__file__).resolve().parents[1] / 'sync_from_template.py')

class PublicSyncConfig(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wiki-public-sync-')
        self.root = Path(self.temp.name)
        self.source = self.root / 'trusted-template'
        self.vault = self.root / 'topic-vault'
        for p in (self.source, self.vault):
            (p / 'scripts').mkdir(parents=True)
            shutil.copyfile(SCRIPT, p / 'scripts' / 'sync_from_template.py')
        (self.source / 'scripts' / 'check_raw.py').write_text('# upstream\n')
        (self.vault / 'scripts' / 'check_raw.py').write_text('# local\n')
        (self.vault / 'raw').mkdir()
        (self.vault / 'raw' / 'private.txt').write_text('private fixture bytes\n')
        self.git('init', '-q', '-b', 'main')
        self.git('add', '-A')
        self.git('commit', '-qm', 'fixture initial')
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        self.env = dict(os.environ, WIKILLM_TEMPLATE=str(self.source),
                        PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.source), '-c', 'user.name=Fixture',
                               '-c', 'user.email=fixture@example.invalid', *args],
                              check=True, capture_output=True, text=True).stdout.strip()

    def run_sync(self, *args):
        return subprocess.run([sys.executable, '-B', str(self.vault / 'scripts' /
                               'sync_from_template.py'), *args], env=self.env,
                              cwd=self.vault, capture_output=True, text=True,
                              encoding='utf-8', timeout=30)

    def assert_refused(self, result, message):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(message, result.stderr)
        self.assertEqual((self.vault / 'scripts' / 'check_raw.py').read_text(), '# local\n')

    def test_configured_source_applies_without_bypass_and_preserves_private_source(self):
        dry = self.run_sync()
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertEqual((self.vault / 'scripts' / 'check_raw.py').read_text(), '# local\n')
        run = self.run_sync('--apply')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual((self.vault / 'scripts' / 'check_raw.py').read_text(), '# upstream\n')
        self.assertEqual((self.vault / 'raw' / 'private.txt').read_text(), 'private fixture bytes\n')
        again = self.run_sync('--apply')
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn('Nothing to do', again.stdout)

    def test_alternate_path_requires_explicit_override(self):
        self.env['WIKILLM_TEMPLATE'] = str(self.root / 'different-trusted-path')
        self.assert_refused(self.run_sync('--template', str(self.source), '--apply'), 'not the configured trusted template')
        allowed = self.run_sync('--template', str(self.source), '--allow-noncanonical', '--apply')
        self.assertEqual(allowed.returncode, 0, allowed.stderr)

    def test_dirty_source_refused_even_with_override(self):
        (self.source / 'scripts' / 'check_raw.py').write_text('# unreviewed\n')
        self.assert_refused(self.run_sync('--apply', '--allow-noncanonical'), 'uncommitted')

    def test_task_branch_refused(self):
        self.git('checkout', '-qb', 'work-in-progress')
        self.assert_refused(self.run_sync('--apply'), "not 'main'")

    def test_ahead_source_refused(self):
        (self.source / 'scripts' / 'check_raw.py').write_text('# ahead\n')
        self.git('add', '-A')
        self.git('commit', '-qm', 'unpublished')
        self.assert_refused(self.run_sync('--apply'), 'origin/main')

    def test_behind_source_refused(self):
        previous = self.git('rev-parse', 'HEAD')
        (self.source / 'scripts' / 'check_raw.py').write_text('# newer\n')
        self.git('add', '-A')
        self.git('commit', '-qm', 'newer')
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        self.git('reset', '--hard', previous)
        self.assert_refused(self.run_sync('--apply'), 'origin/main')

    def test_missing_origin_ref_refused(self):
        self.git('update-ref', '-d', 'refs/remotes/origin/main')
        self.assert_refused(self.run_sync('--apply'), 'origin/main')

    def test_missing_source_fails_before_writing(self):
        self.env['WIKILLM_TEMPLATE'] = str(self.root / 'missing')
        self.assert_refused(self.run_sync('--apply'), 'template not found')

if __name__ == '__main__':
    unittest.main(verbosity=2)
