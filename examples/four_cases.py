import argparse
import json
from pathlib import Path
import sqlite3


CASES = {
    'A': ('BINARY', 'BINARY', ''),
    'B': ('NOCASE', 'BINARY', ''),
    'C': ('BINARY', 'BINARY', ' WHERE code IS NOT NULL'),
    'D': ('NOCASE', 'NOCASE', ''),
}


def schema(case):
    column, index, where = CASES[case]
    return (
        f'CREATE TABLE parent(code TEXT COLLATE {column});\n'
        f'CREATE UNIQUE INDEX ux ON parent(code COLLATE {index}){where};\n'
        'CREATE TABLE child(code TEXT REFERENCES parent(code));\n'
    )


def generate(directory):
    directory.mkdir(parents=True, exist_ok=True)
    for case in CASES:
        path = directory / f'{case}.sqlite'
        if path.exists():
            raise FileExistsError(path)
        with sqlite3.connect(path) as connection:
            connection.execute('PRAGMA foreign_keys=ON')
            connection.executescript(schema(case))
        (directory / f'{case}.sql').write_text(schema(case))


def oracle():
    result = {'sqlite_version': sqlite3.sqlite_version, 'cases': {}}
    for case in CASES:
        with sqlite3.connect(':memory:') as connection:
            connection.execute('PRAGMA foreign_keys=ON')
            connection.executescript(schema(case))
            evidence = {'integrity_check': connection.execute('PRAGMA integrity_check').fetchall()}
            for label, sql in [('foreign_key_check', 'PRAGMA foreign_key_check'),
                               ('insert_null', 'INSERT INTO child VALUES(NULL)')]:
                try:
                    evidence[label] = connection.execute(sql).fetchall()
                except sqlite3.Error as exc:
                    evidence[label] = str(exc)
            for pragma in ['foreign_key_list(child)', 'table_xinfo(parent)',
                           'index_list(parent)', 'index_xinfo(ux)']:
                evidence[pragma] = connection.execute('PRAGMA ' + pragma).fetchall()
            result['cases'][case] = evidence
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', nargs='?', type=Path)
    args = parser.parse_args()
    if args.directory:
        generate(args.directory)
    else:
        print(json.dumps(oracle(), indent=2))
