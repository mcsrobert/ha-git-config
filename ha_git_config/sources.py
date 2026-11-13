"""Custom integrations and dashboard cards pinned in hacs.yaml."""
from dataclasses import dataclass
from pathlib import PurePosixPath
import re

import yaml

from .errors import Error

REPOSITORY = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?/[A-Za-z0-9_.-]+')
VERSION = re.compile(r'[A-Za-z0-9][A-Za-z0-9._+-]*')
COMMIT = re.compile(r'[0-9a-f]{40}')
SHAPES = {
    'integrations': (('repository', 'version', 'asset'), ('repository', 'version', 'commit')),
    'cards': (('repository', 'version', 'asset'), ('repository', 'version', 'commit', 'path')),
}


@dataclass(frozen=True)
class Source:
    category: str
    repository: str
    version: str
    asset: str | None = None
    commit: str | None = None
    path: str | None = None

    @property
    def name(self):
        return self.repository.split('/')[1]

    def __str__(self):
        return f'{self.repository} {self.version}'


def load(text):
    data = yaml.load(text, Loader=yaml.BaseLoader) or {}
    if not isinstance(data, dict) or set(data) - set(SHAPES):
        raise Error('hacs.yaml may only contain the lists integrations and cards')
    sources = []
    for category, shapes in SHAPES.items():
        entries = data.get(category) or []
        if not isinstance(entries, list):
            raise Error(f'{category} must be a list')
        for entry in entries:
            sources.append(parse(category, shapes, entry))
    repositories = [source.repository.lower() for source in sources]
    duplicates = sorted({name for name in repositories if repositories.count(name) > 1})
    if duplicates:
        raise Error('repository listed more than once: ' + ', '.join(duplicates))
    return sources


def parse(category, shapes, entry):
    if not isinstance(entry, dict) or tuple(entry) not in shapes:
        allowed = ' or '.join(', '.join(shape) for shape in shapes)
        raise Error(f'each entry in {category} needs the keys {allowed}, in that order: {entry!r}')
    if not all(isinstance(value, str) for value in entry.values()):
        raise Error(f'values must be plain strings: {entry!r}')
    source = Source(category, **entry)
    if not REPOSITORY.fullmatch(source.repository) or source.name in {'.', '..'}:
        raise Error(f'invalid repository: {source.repository!r}')
    if not VERSION.fullmatch(source.version):
        raise Error(f'invalid version for {source.repository}: {source.version!r}')
    if source.asset is not None:
        if source.asset in {'', '.', '..'} or PurePosixPath(source.asset).name != source.asset or '\\' in source.asset:
            raise Error(f'invalid asset name for {source.repository}: {source.asset!r}')
        if category == 'integrations' and not source.asset.endswith('.zip'):
            raise Error(f'integration asset must be a .zip: {source.repository}')
    if source.commit is not None and not COMMIT.fullmatch(source.commit):
        raise Error(f'commit must be a full lowercase SHA: {source.repository}')
    if source.path is not None:
        path = PurePosixPath(source.path)
        if path.is_absolute() or not path.parts or '..' in path.parts or path.as_posix() != source.path:
            raise Error(f'invalid path for {source.repository}: {source.path!r}')
    return source
