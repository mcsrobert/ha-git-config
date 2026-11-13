"""Command line: ha-git-config build SOURCE OUTPUT, ha-git-config check CONFIG."""
import argparse
import os
from pathlib import Path
import sys

from .build import build
from .check import check
from .errors import Error
from .github import GitHub


def main(argv=None):
    parser = argparse.ArgumentParser(prog='ha-git-config', description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    build_command = commands.add_parser('build', help='assemble the configuration and pinned downloads')
    build_command.add_argument('source', type=Path, help='configuration repository directory')
    build_command.add_argument('output', type=Path, help='new directory for the result')
    check_command = commands.add_parser('check', help="run Home Assistant's check_config")
    check_command.add_argument('config', type=Path, help='directory made by build')
    args = parser.parse_args(argv)
    try:
        if args.command == 'build':
            build(args.source, args.output, GitHub(token=os.environ.get('GITHUB_TOKEN')))
        else:
            check(args.config)
    except Error as error:
        sys.exit(f'error: {error}')


if __name__ == '__main__':
    main()
