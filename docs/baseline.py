import json
from pathlib import Path
import sqlite3
import sys


with sqlite3.connect(Path(sys.argv[1]).resolve().as_uri() + '?mode=ro', uri=True) as db:
    db.row_factory = sqlite3.Row
    tables = db.execute("SELECT name, sql FROM sqlite_schema WHERE type='table'").fetchall()
    result = []
    for table in tables:
        name = table['name'].replace('"', '""')
        fks = [dict(r) for r in db.execute(f'PRAGMA foreign_key_list("{name}")')]
        for fk in fks:
            parent = fk['table'].replace('"', '""')
            indexes = [dict(r) for r in db.execute(f'PRAGMA index_list("{parent}")')]
            for index in indexes:
                quoted = index['name'].replace('"', '""')
                index['xinfo'] = [dict(r) for r in db.execute(f'PRAGMA index_xinfo("{quoted}")')]
            result.append({'child': table['name'], 'fk': fk, 'indexes': indexes,
                           'parent_columns': [dict(r) for r in db.execute(f'PRAGMA table_xinfo("{parent}")')],
                           'parent_sql': db.execute('SELECT sql FROM sqlite_schema WHERE name=? COLLATE NOCASE', (fk['table'],)).fetchone()[0]})
    print(json.dumps(result, indent=2))
