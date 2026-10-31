import argparse
import json
import sys

from . import __version__
from .checker import InputError, check


def render(report):
    lines = [f"SQLite {report['sqlite_version']} | report v{report['schema_version']}"]
    for relation in report['relations']:
        child = json.dumps(relation['child_table'], ensure_ascii=True)
        parent = json.dumps(relation['parent_table'], ensure_ascii=True)
        columns = json.dumps(relation['child_columns'], ensure_ascii=True)
        targets = json.dumps(relation['parent_columns'], ensure_ascii=True)
        lines.append(f"{child}{columns} FK#{relation['fk_id']} -> {parent}{targets}: {relation['status']}")
        pk = relation['primary_key']
        if pk:
            lines.append(f"  PK: {json.dumps([c['name'] for c in pk['columns']])}; storage={pk['storage']}; applicable={pk['applicable']}")
        column = relation['parent_column']
        if column and column['declaration']:
            declaration = column['declaration']
            lines.append(f"  Parent collation: {declaration['collation']} ({declaration['collation_source']})")
        for candidate in relation['candidates']:
            verdict = 'eligible' if candidate['eligible'] is True else 'rejected' if candidate['eligible'] is False else 'not analyzed'
            name = json.dumps(candidate['name']) if candidate['name'] is not None else 'INTEGER PRIMARY KEY'
            terms = [(t['name'], t.get('coll')) for t in candidate['key_columns']]
            lines.append(f"  {name}: {verdict}; unique={candidate['unique']}; partial={candidate['partial']}; keys={json.dumps(terms)}")
            for item in candidate['reasons'] + candidate['not_analyzed_reasons']:
                lines.append(f"    {item['code']}: {item['message']}")
                if item['code'] == 'collation-mismatch':
                    lines.append(f"      column {item['expected']} ({item['expected_source']}) != index {item['actual']} ({item['actual_source']})")
        for item in relation['reasons']:
            lines.append(f"  {item['code']}: {item['message']}")
    coverage = report['coverage']
    if coverage['total_relations'] == 0:
        lines.append('No foreign keys found (empty relationship inventory).')
    lines.append(f"Coverage: {coverage['analyzed_relations']}/{coverage['total_relations']} analyzed; {coverage['eligible']} eligible; {coverage['incompatible']} incompatible; {coverage['not_analyzed']} not analyzed")
    lines.append('Schema parent-key eligibility only. Rows and application foreign_keys settings are not checked.')
    return '\n'.join(lines)


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError('must be positive')
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(prog='fk-parent-key', description='Explain SQLite parent-key eligibility without querying business rows.')
    parser.add_argument('--version', action='version', version=__version__)
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('check', help='Inspect an existing, self-contained SQLite snapshot')
    command.add_argument('snapshot')
    command.add_argument('--json', action='store_true', help='Emit report schema version 1')
    command.add_argument('--max-bytes', type=positive_int, default=100 * 1024 * 1024)
    command.add_argument('--max-objects', type=positive_int, default=10000)
    command.add_argument('--timeout', type=float, default=5.0, metavar='SECONDS')
    args = parser.parse_args(argv)
    try:
        report = check(args.snapshot, args.max_bytes, args.max_objects, args.timeout)
    except InputError as exc:
        report = {'schema_version': 1, 'error': str(exc), 'exit_code': 3}
        print(json.dumps(report) if args.json else 'Invalid input: ' + str(exc), file=sys.stdout if args.json else sys.stderr)
        return 3
    print(json.dumps(report, indent=2, ensure_ascii=True) if args.json else render(report))
    return report['exit_code']


if __name__ == '__main__':
    sys.exit(main())
