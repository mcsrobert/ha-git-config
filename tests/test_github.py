import io
import shutil
import tempfile
import unittest
import zipfile

from ha_oci_config.errors import Error
from ha_oci_config.github import GitHub, unzip

from helpers import Server, repository, zip_bytes


class ReleaseAssetTest(unittest.TestCase):
    def setUp(self):
        self.server = Server()
        self.addCleanup(self.server.close)
        self.github = GitHub(api=self.server.url, token='secret')

    def test_downloads_asset_matching_its_digest(self):
        self.server.release('o/r', 'v1', {'r.zip': b'data'})
        self.assertEqual(self.github.release_asset('o/r', 'v1', 'r.zip'), b'data')

    def test_token_is_only_sent_to_the_api(self):
        self.server.release('o/r', 'v1', {'r.zip': b'data'})
        self.github.release_asset('o/r', 'v1', 'r.zip')
        (api, api_headers), (download, download_headers) = self.server.requests
        self.assertEqual(api_headers['Authorization'], 'Bearer secret')
        self.assertNotIn('Authorization', download_headers)

    def test_rejects_digest_mismatch(self):
        self.server.release('o/r', 'v1', {'r.zip': b'data'}, digests={'r.zip': 'sha256:' + '0' * 64})
        with self.assertRaisesRegex(Error, 'does not match its published digest'):
            self.github.release_asset('o/r', 'v1', 'r.zip')

    def test_rejects_asset_without_digest(self):
        self.server.release('o/r', 'v1', {'r.zip': b'data'}, digests={'r.zip': None})
        with self.assertRaisesRegex(Error, 'no SHA-256 digest'):
            self.github.release_asset('o/r', 'v1', 'r.zip')

    def test_rejects_missing_asset_and_release(self):
        self.server.release('o/r', 'v1', {'other.zip': b'data'})
        with self.assertRaisesRegex(Error, 'has no release asset r.zip'):
            self.github.release_asset('o/r', 'v1', 'r.zip')
        with self.assertRaisesRegex(Error, 'HTTP 404'):
            self.github.release_asset('o/r', 'v2', 'r.zip')


class CheckoutTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        self.github = GitHub(git=f'file://{self.root}')

    def test_reads_files_and_directories_at_the_tagged_commit(self):
        commit = repository(self.root, 'o/r', {'custom_components/r/manifest.json': '{}',
                                               'custom_components/r/sub/x.py': 'x', 'dist/card.js': 'js'},
                            'v1', annotated=True)
        with self.github.checkout('o/r', 'v1', commit) as tree:
            self.assertEqual(tree.dirs('custom_components'), ['r'])
            self.assertEqual(tree.files('custom_components/r'), {'manifest.json': b'{}', 'sub/x.py': b'x'})
            self.assertEqual(tree.files('dist/card.js'), {'card.js': b'js'})

    def test_rejects_tag_pointing_elsewhere(self):
        repository(self.root, 'o/r', {'a': 'a'}, 'v1')
        with self.assertRaisesRegex(Error, 'tag v1 points to [0-9a-f]{40}, not ' + 'b' * 40):
            with self.github.checkout('o/r', 'v1', 'b' * 40):
                pass
        with self.assertRaisesRegex(Error, 'tag v2 points to nothing'):
            with self.github.checkout('o/r', 'v2', 'b' * 40):
                pass

    def test_rejects_symlinks(self):
        commit = repository(self.root, 'o/r', {'dist/card.js': 'js'}, 'v1', symlinks={'dist/link.js': '/etc/passwd'})
        with self.github.checkout('o/r', 'v1', commit) as tree:
            with self.assertRaisesRegex(Error, 'dist/link.js is not a regular file'):
                tree.files('dist')

    def test_reports_missing_path(self):
        commit = repository(self.root, 'o/r', {'a': 'a'}, 'v1')
        with self.github.checkout('o/r', 'v1', commit) as tree:
            with self.assertRaisesRegex(Error, 'git: .*dist'):
                tree.files('dist')


class UnzipTest(unittest.TestCase):
    def test_returns_regular_files(self):
        self.assertEqual(unzip(zip_bytes({'manifest.json': '{}', 'sub/': '', 'sub/x.py': 'x'})),
                         {'manifest.json': b'{}', 'sub/x.py': b'x'})

    def test_rejects_traversal_and_absolute_paths(self):
        for name in ('../x', '/x', 'a/../../x', 'a\\x'):
            with self.assertRaisesRegex(Error, 'unsafe path'):
                unzip(zip_bytes({name: 'x'}))

    def test_rejects_symlinks(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            info = zipfile.ZipInfo('link')
            info.external_attr = 0o120777 << 16
            archive.writestr(info, '/etc/passwd')
        with self.assertRaisesRegex(Error, 'link is not a regular file'):
            unzip(buffer.getvalue())


if __name__ == '__main__':
    unittest.main()
