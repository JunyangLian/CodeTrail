"""Operator-only knowledge review; never registered as an Agent tool."""
import argparse
import json

from .knowledge import KnowledgeStore


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    propose = commands.add_parser('propose')
    propose.add_argument('--key', required=True)
    propose.add_argument('--text', required=True)
    propose.add_argument('--evidence', action='append', default=[])
    propose.add_argument('--ttl', type=int)
    for command in ('confirm', 'retract'):
        sub = commands.add_parser(command)
        sub.add_argument('id')
        sub.add_argument('--version', type=int, required=True)
    commands.add_parser('list')
    show = commands.add_parser('show')
    show.add_argument('id')
    args = parser.parse_args(argv)
    store = KnowledgeStore(args.workspace)
    if args.command == 'propose':
        result = store.propose(args.text, args.key, args.evidence, ttl_seconds=args.ttl)
    elif args.command == 'confirm':
        result = store.activate(args.id, args.version, reviewer='operator_cli')
    elif args.command == 'retract':
        store.retract(args.id, args.version)
        result = store.get(args.id)
    elif args.command == 'show':
        result = store.get(args.id)
    else:
        result = store.active()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
