"""Run Home Assistant's check_config on a built configuration."""
import json
from pathlib import Path
import re
import shutil
import tempfile

import yaml

from .errors import Error

PLACEHOLDER = 'ha-oci-config-placeholder'
# Fallback for files PyYAML cannot parse; check_config reports those files itself.
SECRET = re.compile(r'''!secret[ \t]+(?:"([^"\n]+)"|'([^'\n]+)'|([^\s#,\]}]+))''')


def check(config):
    from homeassistant.scripts import check_config

    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / 'config'
        shutil.copytree(config, copy)
        write_placeholder_secrets(copy)
        result = check_config.check(str(copy))
    problems = {}
    for kind, key in (('errors', 'except'), ('warnings', 'warn')):
        problems[kind] = {domain: [message.replace(f'{copy}/', '') for message in messages if isinstance(message, str)]
                          for domain, messages in result[key].items()}
    print(json.dumps(problems, indent=2))
    if any(problems.values()):
        raise Error('Home Assistant check_config reported problems')
    print('Home Assistant check_config passed')


def write_placeholder_secrets(config):
    """Write secrets.yaml with a placeholder for every secret the configuration references.

    The real secrets.yaml lives on the persistent volume and never reaches Git or the image,
    so check_config would otherwise fail on every !secret. Only call this on a throwaway copy.
    """
    config = Path(config)
    names = sorted(secret_names(config))
    if names:
        (config / 'secrets.yaml').write_text(yaml.safe_dump(dict.fromkeys(names, PLACEHOLDER), sort_keys=True))
    return names


def secret_names(config):
    """Return the names referenced with !secret in any YAML file under config.

    Scanning every file covers whatever configuration.yaml includes, however it includes it.
    """
    names = set()
    for path in sorted(Path(config).rglob('*')):
        if path.suffix in ('.yaml', '.yml') and path.is_file() and path.name != 'secrets.yaml':
            names |= file_secret_names(path.read_text(errors='replace'))
    return names


def file_secret_names(text):
    try:
        nodes = [node for node in yaml.compose_all(text, Loader=yaml.SafeLoader) if node is not None]
    except yaml.YAMLError:
        return {next(group for group in match.groups() if group) for match in SECRET.finditer(text)}
    names = set()
    seen = set()
    while nodes:
        node = nodes.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, yaml.ScalarNode):
            if node.tag == '!secret' and node.value:
                names.add(node.value)
        elif isinstance(node, yaml.SequenceNode):
            nodes.extend(node.value)
        elif isinstance(node, yaml.MappingNode):
            for key, value in node.value:
                nodes.extend((key, value))
    return names
