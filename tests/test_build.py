import contextlib
import io
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest

from ha_git_config.build import build
from ha_git_config.errors import Error
from ha_git_config.github import GitHub

from helpers import Server, repository, zip_bytes


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.server = Server()
        self.addCleanup(self.server.close)
        self.github = GitHub(api=self.server.url, git=f'file://{self.tmp}/remote')
        self.source = self.tmp / 'source'
        self.write('configuration.yaml', 'homeassistant:\n')
        self.write('automations.yaml', '[]\n')
        self.write('scripts.yaml', '{}\n')
        self.write('scenes.yaml', '[]\n')
        self.output = self.tmp / 'out' / 'config'

    def write(self, name, text):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def build(self):
        with contextlib.redirect_stdout(io.StringIO()) as log:
            build(self.source, self.output, self.github)
        return log.getvalue()

    def files(self):
        return sorted(path.relative_to(self.output).as_posix() for path in self.output.rglob('*') if path.is_file())

    def test_copies_configuration_and_creates_every_mount_point(self):
        self.write('includes/scripts.yaml', '{}\n')
        self.write('packages/a.yaml', 'a:\n')
        self.write('packages/old.yaml.disable', 'b:\n')
        self.write('automations.yaml', '[]\n')
        self.build()
        self.assertEqual(self.files(), ['automations.yaml', 'configuration.yaml', 'includes/scripts.yaml',
                                        'packages/a.yaml', 'packages/old.yaml.disable', 'scenes.yaml', 'scripts.yaml'])
        for name in ('includes', 'packages', 'blueprints', 'pyscript', 'custom_components', 'www/community'):
            self.assertTrue((self.output / name).is_dir(), name)

    def test_copies_the_actual_editor_files_to_their_standard_paths(self):
        for name, content in (
            ('automations.yaml', '- id: git_automation\n  alias: Git automation\n'),
            ('scripts.yaml', 'git_script:\n  sequence: []\n'),
            ('scenes.yaml', '- id: git_scene\n  name: Git scene\n'),
        ):
            self.write(name, content)
            self.write(f'includes/{name}', content)
        self.build()
        for name in ('automations.yaml', 'scripts.yaml', 'scenes.yaml'):
            with self.subTest(name=name):
                self.assertEqual((self.output / name).read_bytes(), (self.source / name).read_bytes())
                self.assertEqual((self.output / 'includes' / name).read_text(),
                                 (self.source / 'includes' / name).read_text())

    def test_installs_integrations_and_cards(self):
        self.server.release('o/zipped', 'v1', {'zipped.zip': zip_bytes({'manifest.json': '{"domain": "zipped"}',
                                                                         'sensor.py': 'x'})})
        self.server.release('o/card', 'v2', {'card.js': b'js'})
        source = repository(self.tmp / 'remote', 'o/source', {'custom_components/src/manifest.json': '{"domain": "src"}',
                                                               'README.md': 'x'}, '1.0')
        dist = repository(self.tmp / 'remote', 'o/dist-card', {'dist/dist-card.js': 'js', 'src/x.ts': 'ts'}, 'V3')
        self.write('hacs.yaml', f"""
integrations:
  - repository: o/zipped
    version: v1
    asset: zipped.zip
  - repository: o/source
    version: '1.0'
    commit: {source}
cards:
  - repository: o/card
    version: v2
    asset: card.js
  - repository: o/dist-card
    version: V3
    commit: {dist}
    path: dist/dist-card.js
""")
        log = self.build()
        self.assertEqual(self.files(), [
            'automations.yaml',
            'configuration.yaml',
            'custom_components/src/manifest.json',
            'custom_components/zipped/manifest.json',
            'custom_components/zipped/sensor.py',
            'scenes.yaml',
            'scripts.yaml',
            'www/community/card/card.js',
            'www/community/dist-card/dist-card.js',
        ])
        self.assertIn('o/source 1.0 -> custom_components/src', log)

    def test_normalizes_permissions(self):
        self.write('pyscript/run.py', '')
        os.chmod(self.source / 'pyscript' / 'run.py', 0o700)
        self.build()
        for path in [self.output, *self.output.rglob('*')]:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o755 if path.is_dir() else 0o644, path)

    def assertFails(self, message):
        with self.assertRaisesRegex(Error, message):
            self.build()
        self.assertFalse(self.output.exists())
        self.assertEqual(os.listdir(self.output.parent), [])

    def test_rejects_symlinks_in_the_configuration(self):
        (self.source / 'packages').mkdir()
        (self.source / 'packages' / 'link.yaml').symlink_to('/etc/hosts')
        self.assertFails('link.yaml must be a regular file')

    def test_requires_every_root_yaml_file(self):
        for name, content in (('configuration.yaml', 'homeassistant:\n'), ('automations.yaml', '[]\n'),
                              ('scripts.yaml', '{}\n'), ('scenes.yaml', '[]\n')):
            with self.subTest(name=name):
                (self.source / name).unlink()
                self.assertFails(f'{name} must be a regular file')
                self.write(name, content)

    def test_refuses_existing_output(self):
        self.output.mkdir(parents=True)
        with self.assertRaisesRegex(Error, 'already exists'):
            self.build()

    def test_rejects_two_integrations_with_one_domain(self):
        for name in ('a', 'b'):
            self.server.release(f'o/{name}', 'v1', {'x.zip': zip_bytes({'manifest.json': '{"domain": "same"}'})})
        self.write('hacs.yaml', 'integrations:\n'
                   '  - repository: o/a\n    version: v1\n    asset: x.zip\n'
                   '  - repository: o/b\n    version: v1\n    asset: x.zip\n')
        self.assertFails('o/b v1 installs into custom_components/same')

    def test_rejects_integration_without_valid_manifest(self):
        for manifest, message in (({}, 'no manifest.json'), ({'manifest.json': '{"domain": "../x"}'}, 'invalid'),
                                  ({'manifest.json': '[]'}, 'no manifest.json')):
            with self.subTest(manifest=manifest):
                self.server.release('o/a', 'v1', {'x.zip': zip_bytes({'x.py': '', **manifest})})
                self.write('hacs.yaml', 'integrations:\n  - repository: o/a\n    version: v1\n    asset: x.zip\n')
                self.assertFails(message)

    def test_source_integration_needs_one_matching_folder(self):
        cases = {
            'o/none': ({'README.md': ''}, 'exactly one directory'),
            'o/two': ({'custom_components/a/manifest.json': '{"domain": "a"}',
                       'custom_components/b/manifest.json': '{"domain": "b"}'}, 'exactly one directory'),
            'o/mismatch': ({'custom_components/a/manifest.json': '{"domain": "b"}'}, "invalid integration domain: 'b'"),
        }
        for name, (files, message) in cases.items():
            with self.subTest(name=name):
                commit = repository(self.tmp / 'remote', name, files, 'v1')
                self.write('hacs.yaml', f'integrations:\n  - repository: {name}\n    version: v1\n    commit: {commit}\n')
                self.assertFails(message)


if __name__ == '__main__':
    unittest.main()
