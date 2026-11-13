"""Assemble the files Home Assistant reads from Git into one directory."""
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from . import sources
from .errors import Error
from .github import unzip

CONFIG_FILES = ('configuration.yaml', 'automations.yaml', 'scripts.yaml', 'scenes.yaml')
CONFIG_DIRS = ('includes', 'packages', 'blueprints', 'pyscript', 'themes')
HACS_FILES = ('hacs.yaml', 'hacs.yml')
DOMAIN = re.compile(r'[a-z0-9_]+')


def build(source, output, github):
    source = Path(source)
    output = Path(output).absolute()
    if output.exists():
        raise Error(f'{output} already exists')
    found = [source / name for name in HACS_FILES if (source / name).exists()]
    if len(found) > 1:
        raise Error('use either hacs.yaml or hacs.yml, not both')
    pinned = sources.load(found[0].read_text()) if found else []
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as staging:
        root = Path(staging) / 'config'
        root.mkdir()
        for name in CONFIG_FILES:
            copy_file(source / name, root / name)
        for name in CONFIG_DIRS:
            if (source / name).exists() or (source / name).is_symlink():
                copy_tree(source / name, root / name)
            else:
                (root / name).mkdir()
        (root / 'custom_components').mkdir()
        (root / 'www' / 'community').mkdir(parents=True)
        for item in pinned:
            print(f'{item} -> {install(item, root, github)}')
        normalize(root)
        root.rename(output)


def install(item, root, github):
    folder = None
    if item.asset:
        data = github.release_asset(item.repository, item.version, item.asset)
        files = unzip(data) if item.category == 'integrations' else {item.asset: data}
    else:
        with github.checkout(item.repository, item.version, item.commit) as tree:
            if item.category == 'cards':
                files = tree.files(item.path)
            else:
                folders = tree.dirs('custom_components')
                if len(folders) != 1:
                    raise Error(f'{item} must have exactly one directory in custom_components')
                folder = folders[0]
                files = tree.files(f'custom_components/{folder}')
    if not files:
        raise Error(f'{item} contains no files')
    if item.category == 'integrations':
        try:
            domain = json.loads(files['manifest.json'])['domain']
        except (KeyError, TypeError, ValueError):
            raise Error(f'{item} has no manifest.json with a domain') from None
        if not isinstance(domain, str) or not DOMAIN.fullmatch(domain) or folder not in (None, domain):
            raise Error(f'{item} has an invalid integration domain: {domain!r}')
        target = Path('custom_components', domain)
    else:
        target = Path('www', 'community', item.name)
    if (root / target).exists():
        raise Error(f'{item} installs into {target.as_posix()}, which another entry already uses')
    for name, data in files.items():
        path = root / target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return target.as_posix()


def copy_file(source, target):
    if source.is_symlink() or not source.is_file():
        raise Error(f'{source} must be a regular file')
    shutil.copyfile(source, target)


def copy_tree(source, target):
    if source.is_symlink() or not source.is_dir():
        raise Error(f'{source} must be a directory')
    target.mkdir()
    for entry in sorted(source.iterdir()):
        if entry.is_dir() and not entry.is_symlink():
            copy_tree(entry, target / entry.name)
        else:
            copy_file(entry, target / entry.name)


def normalize(root):
    for directory, _, files in os.walk(root):
        os.chmod(directory, 0o755)
        for name in files:
            os.chmod(os.path.join(directory, name), 0o644)
