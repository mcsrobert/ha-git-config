"""Run Home Assistant's check_config on a built configuration."""
import json
from pathlib import Path
import shutil
import tempfile

from .errors import Error


def check(config):
    from homeassistant.scripts import check_config

    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / 'config'
        shutil.copytree(config, copy)
        result = check_config.check(str(copy))
    problems = {}
    for kind, key in (('errors', 'except'), ('warnings', 'warn')):
        problems[kind] = {domain: [message.replace(f'{copy}/', '') for message in messages if isinstance(message, str)]
                          for domain, messages in result[key].items()}
    print(json.dumps(problems, indent=2))
    if any(problems.values()):
        raise Error('Home Assistant check_config reported problems')
    print('Home Assistant check_config passed')
