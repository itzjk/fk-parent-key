import argparse
from pathlib import Path
import shutil
import sqlite3
from contextlib import closing


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Repair only the generated B and C fixtures on fresh copies')
    parser.add_argument('fixtures', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    for case in 'ABCD':
        path = args.output / f'{case}.sqlite'
        shutil.copyfile(args.fixtures / f'{case}.sqlite', path)
        if case in 'BC':
            with closing(sqlite3.connect(path)) as db:
                db.execute('DROP INDEX ux')
                collation = 'NOCASE' if case == 'B' else 'BINARY'
                db.execute(f'CREATE UNIQUE INDEX ux ON parent(code COLLATE {collation})')
    print('Created four copies; changed only B (NOCASE index) and C (full index).')
