"""Regression checks for CI permissions and release-only side effects."""
from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow(name):
    # BaseLoader keeps the YAML key 'on' as text rather than a YAML 1.1 boolean.
    return yaml.load((ROOT / '.github/workflows' / name).read_text(), Loader=yaml.BaseLoader)


class WorkflowPolicyTests(unittest.TestCase):
    def setUp(self):
        self.ci = workflow('verify.yml')
        self.release = workflow('release.yml')

    def test_ordinary_updates_and_pull_requests_only_verify(self):
        self.assertEqual(set(self.ci['on']), {'push', 'pull_request', 'workflow_call'})
        self.assertEqual(self.ci['on']['push']['branches'], ['main'])
        self.assertEqual(self.ci['permissions'], {'contents': 'read'})
        self.assertEqual(set(self.release['on']), {'workflow_dispatch'})
        self.assertEqual(self.release['on']['workflow_dispatch']['inputs']['version']['required'], 'true')
        scripts = '\n'.join(s.get('run', '') for s in self.ci['jobs']['verify']['steps'])
        for command in ('pip_audit', 'compileall', 'unittest discover', 'node --check', 'node --test'):
            self.assertIn(command, scripts)
        for command in ('gh release', 'git push', 'git_filter_repo', 'PyInstaller'):
            self.assertNotIn(command, scripts)

    def test_publish_requires_explicit_main_request_and_successful_verification(self):
        self.assertEqual(self.release['permissions'], {'contents': 'read'})
        verify = self.release['jobs']['verify']
        self.assertEqual(verify['uses'], './.github/workflows/verify.yml')
        self.assertEqual(verify['if'], "github.ref == 'refs/heads/main'")
        publish = self.release['jobs']['publish']
        self.assertEqual(publish['needs'], 'verify')
        self.assertEqual(publish['if'], "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'")
        self.assertEqual(publish['permissions'], {'contents': 'write'})
        self.assertEqual(self.release['concurrency']['cancel-in-progress'], 'false')

    def test_duplicate_and_privacy_guards_precede_build_and_publish(self):
        steps = self.release['jobs']['publish']['steps']
        version = next(i for i, s in enumerate(steps) if s.get('id') == 'version')
        build = next(i for i, s in enumerate(steps) if 'PyInstaller' in s.get('run', ''))
        self.assertLess(version, build)
        guard = steps[version]['run']
        self.assertEqual(steps[version]['env']['REQUESTED_VERSION'], '${{ inputs.version }}')
        for marker in ('REQUESTED_VERSION -cne $version', "if ($status -eq '200')", "if ($status -ne '404')", 'git ls-remote --tags', '$tags.Count -gt 0'):
            self.assertIn(marker, guard)
        scripts = '\n'.join(s.get('run', '') for s in steps)
        for marker in ('users\\.noreply\\.github\\.com', 'settings\\.json', 'github-token\\.dpapi', 'tskey-', 'git archive', '--target $cleanHead', '${{ github.sha }}'):
            self.assertIn(marker, scripts)
        for command in ('git push', 'git_filter_repo', 'gh release upload', '--clobber', 'gh release delete'):
            self.assertNotIn(command, scripts)
        self.assertIn('Release creation failed', steps[-1]['run'])
        for wf in (self.ci, self.release):
            for job in wf['jobs'].values():
                for step in job.get('steps', []):
                    if step.get('uses', '').startswith('actions/checkout@'):
                        self.assertEqual(step['with']['persist-credentials'], 'false')


if __name__ == '__main__':
    unittest.main()
