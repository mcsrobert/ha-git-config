"""Verified downloads from GitHub: release assets by digest, repository files by commit."""
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import PurePosixPath
import subprocess
import tarfile
import tempfile
import urllib.error
from urllib.parse import quote
import urllib.request
import zipfile

from . import __version__
from .errors import Error

USER_AGENT = f'ha-oci-config/{__version__}'


class GitHub:
    def __init__(self, api='https://api.github.com', git='https://github.com', token=None):
        self.api = api
        self.git = git
        self.token = token

    def release_asset(self, repository, tag, name):
        """Download a release asset and check it against the SHA-256 digest GitHub publishes for it."""
        headers = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2026-03-10'}
        if self.token:
            headers['Authorization'] = f'Bearer {self.token}'
        release = json.loads(get(f'{self.api}/repos/{repository}/releases/tags/{quote(tag, safe="")}', headers))
        assets = [asset for asset in release['assets'] if asset['name'] == name]
        if not assets:
            raise Error(f'{repository} {tag} has no release asset {name}')
        digest = assets[0].get('digest') or ''
        if not digest.startswith('sha256:'):
            raise Error(f'GitHub publishes no SHA-256 digest for {repository} {tag} {name}; pin a commit instead')
        data = get(assets[0]['browser_download_url'], {})
        if hashlib.sha256(data).hexdigest() != digest.removeprefix('sha256:'):
            raise Error(f'{repository} {tag} {name} does not match its published digest')
        return data

    @contextmanager
    def checkout(self, repository, tag, commit):
        """Fetch one commit, after checking that the tag still points to it."""
        url = f'{self.git}/{repository}'
        refs = dict(reversed(line.split('\t')) for line in
                    git('ls-remote', url, f'refs/tags/{tag}', f'refs/tags/{tag}^{{}}').decode().splitlines())
        target = refs.get(f'refs/tags/{tag}^{{}}') or refs.get(f'refs/tags/{tag}')
        if target != commit:
            raise Error(f'{repository} tag {tag} points to {target or "nothing"}, not {commit}')
        with tempfile.TemporaryDirectory() as tmp:
            git('init', '-q', '--bare', tmp)
            git('-C', tmp, 'fetch', '-q', '--depth', '1', '--no-tags', url, commit)
            yield Tree(tmp, commit)


class Tree:
    def __init__(self, repo, commit):
        self.repo = repo
        self.commit = commit

    def dirs(self, path):
        names = git('-C', self.repo, 'ls-tree', '-d', '--name-only', self.commit, '--', f'{path}/').decode()
        return [PurePosixPath(name).name for name in names.splitlines()]

    def files(self, path):
        """Files at path: the file itself under its own name, or the contents of a directory."""
        archive = git('-C', self.repo, 'archive', '--format=tar', self.commit, '--', path)
        files = {}
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            for member in tar:
                if member.isdir():
                    continue
                if not member.isfile():
                    raise Error(f'{member.name} is not a regular file')
                name = PurePosixPath(path).name if member.name == path else member.name.removeprefix(f'{path}/')
                files[safe(name)] = tar.extractfile(member).read()
        return files


def unzip(data):
    files = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for entry in archive.infolist():
            if entry.is_dir():
                continue
            if (entry.external_attr >> 16) & 0o170000 not in (0, 0o100000):
                raise Error(f'{entry.filename} is not a regular file')
            name = safe(entry.filename)
            if name in files:
                raise Error(f'download contains {name!r} more than once')
            files[name] = archive.read(entry)
    return files


def safe(name):
    path = PurePosixPath(name)
    if '\\' in name or path.is_absolute() or not path.parts or '..' in path.parts:
        raise Error(f'unsafe path in download: {name!r}')
    return path.as_posix()


def get(url, headers):
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, **headers})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        error.close()
        raise Error(f'{url}: HTTP {error.code}') from None
    except urllib.error.URLError as error:
        raise Error(f'{url}: {error.reason}') from None


def git(*args):
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0'}
    try:
        return subprocess.run(['git', *args], check=True, capture_output=True, env=env).stdout
    except subprocess.CalledProcessError as error:
        lines = error.stderr.decode(errors='replace').strip().splitlines()
        raise Error(f'git: {lines[-1] if lines else f"exit status {error.returncode}"}') from None
