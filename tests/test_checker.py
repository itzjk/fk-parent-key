from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'examples'))
from fk_parent_key.checker import InputError, Inspector, check
from fk_parent_key.ddl import UnsupportedDDL, declarations, fold
from four_cases import CASES, generate, oracle, schema


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def database(self, sql='', name='snapshot.sqlite', setup=None):
        path = self.directory / name
        with closing(sqlite3.connect(path)) as db:
            db.execute('PRAGMA foreign_keys=ON')
            if setup:
                setup(db)
            db.executescript(sql)
            db.execute('VACUUM')
        return path

    def run_cli(self, path, *args):
        env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'))
        return subprocess.run([sys.executable, '-m', 'fk_parent_key', 'check', str(path), *args],
                              capture_output=True, text=True, env=env, timeout=15)

    def relation(self, sql, expected, native_ok=None, setup=None):
        path = self.database(sql, setup=setup)
        result = check(path)
        self.assertEqual(len(result['relations']), 1)
        relation = result['relations'][0]
        self.assertEqual(relation['status'], expected, relation)
        if native_ok is not None:
            with closing(sqlite3.connect(path)) as db:
                db.execute('PRAGMA foreign_keys=ON')
                query = 'INSERT INTO ' + '"' + relation['child_table'].replace('"', '""') + '" DEFAULT VALUES'
                if native_ok:
                    self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
                    db.execute(query)
                else:
                    if relation['reasons'][0]['code'] != 'missing-parent-table':
                        with self.assertRaises(sqlite3.OperationalError):
                            db.execute('PRAGMA foreign_key_check')
                    with self.assertRaises(sqlite3.OperationalError):
                        db.execute(query)
        return relation


class MainCases(Fixture):
    def test_four_cli_cases_and_native_oracle(self):
        generate(self.directory)
        native = oracle()
        self.assertEqual(native['sqlite_version'], sqlite3.sqlite_version)
        for case in CASES:
            with self.subTest(case=case):
                path = self.directory / f'{case}.sqlite'
                before = hashlib.sha256(path.read_bytes()).hexdigest()
                files = set(self.directory.iterdir())
                result = self.run_cli(path, '--json')
                report = json.loads(result.stdout)
                self.assertEqual(result.returncode, 1 if case in 'BC' else 0)
                self.assertEqual(report['schema_version'], 1)
                self.assertTrue(report['coverage']['complete'])
                relation = report['relations'][0]
                self.assertEqual(relation['fk_id'], 0)
                self.assertEqual(relation['child_table'], 'child')
                self.assertEqual(relation['parent_columns'], ['code'])
                candidate = relation['candidates'][0]
                self.assertEqual(candidate['name'], 'ux')
                self.assertTrue(candidate['unique'])
                self.assertEqual(candidate['partial'], case == 'C')
                self.assertEqual(candidate['key_columns'][0]['coll'], 'NOCASE' if case == 'D' else 'BINARY')
                self.assertEqual(candidate['auxiliary_columns'][0]['key'], 0)
                codes = [r['code'] for r in candidate['reasons']]
                self.assertEqual(codes, ['collation-mismatch'] if case == 'B' else ['partial-index'] if case == 'C' else [])
                if case == 'B':
                    item = candidate['reasons'][0]
                    self.assertEqual((item['expected'], item['actual']), ('NOCASE', 'BINARY'))
                    self.assertIn('sqlite_schema.sql', item['expected_source'])
                    self.assertEqual(item['actual_source'], 'PRAGMA index_xinfo.coll')
                self.assertEqual(report['snapshot']['sha256'], before)
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
                self.assertEqual(set(self.directory.iterdir()), files)
                expected = [] if case in 'AD' else 'foreign key mismatch - "child" referencing "parent"'
                self.assertEqual(native['cases'][case]['foreign_key_check'], expected)
                self.assertEqual(native['cases'][case]['insert_null'], expected)
                self.assertEqual(native['cases'][case]['integrity_check'], [('ok',)])

    def test_fix_only_incompatible_cases(self):
        generate(self.directory)
        before = {case: check(self.directory / f'{case}.sqlite') for case in CASES}
        for case in 'BC':
            path = self.directory / f'{case}.sqlite'
            with closing(sqlite3.connect(path)) as db:
                db.execute('DROP INDEX ux')
                coll = 'NOCASE' if case == 'B' else 'BINARY'
                db.execute(f'CREATE UNIQUE INDEX ux ON parent(code COLLATE {coll})')
        changed = []
        for case in CASES:
            path = self.directory / f'{case}.sqlite'
            after = check(path)
            if after['relations'] != before[case]['relations']:
                changed.append(case)
            self.assertEqual(after['exit_code'], 0)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(changed, ['B', 'C'])

    def test_text_has_provenance(self):
        path = self.database(schema('B'))
        result = self.run_cli(path)
        for text in ['no-eligible-parent-key', 'collation-mismatch', 'column NOCASE', 'index BINARY', 'PRAGMA index_xinfo.coll', '1/1 analyzed']:
            self.assertIn(text, result.stdout)


class SupportedKeys(Fixture):
    def test_primary_and_unique_variants(self):
        for ddl, kind in [
            ('code INTEGER PRIMARY KEY', 'integer-primary-key'),
            ('code INTEGER, PRIMARY KEY(code DESC)', 'integer-primary-key'),
            ('code INTEGER PRIMARY KEY DESC', 'primary-key-index'),
            ('code TEXT PRIMARY KEY COLLATE NOCASE', 'primary-key-index'),
            ('code TEXT UNIQUE', 'unique-constraint-index'),
            ('code TEXT, UNIQUE(code)', 'unique-constraint-index'),
            ('code TEXT COLLATE RTRIM, PRIMARY KEY(code)', 'primary-key-index'),
        ]:
            with self.subTest(ddl=ddl):
                sql = f'CREATE TABLE p({ddl}); CREATE TABLE c(x REFERENCES p(code));'
                path = self.database(sql, name=str(len(list(self.directory.iterdir()))) + '.sqlite')
                relation = check(path)['relations'][0]
                self.assertEqual(relation['status'], 'eligible-parent-key')
                self.assertEqual(relation['candidates'][0]['kind'], kind)
                with closing(sqlite3.connect(path)) as db:
                    db.execute('PRAGMA foreign_keys=ON')
                    self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
                    db.execute('INSERT INTO c VALUES(NULL)')

    def test_pk_index_still_requires_declared_collation(self):
        relation = self.relation('CREATE TABLE p(code TEXT COLLATE NOCASE, PRIMARY KEY(code COLLATE BINARY)); CREATE TABLE c(x REFERENCES p(code));', 'no-eligible-parent-key', False)
        self.assertEqual(relation['candidates'][0]['reasons'][0]['code'], 'collation-mismatch')

    def test_without_rowid_auxiliary_columns(self):
        relation = self.relation('CREATE TABLE p(id INTEGER PRIMARY KEY, code TEXT UNIQUE) WITHOUT ROWID; CREATE TABLE c(x REFERENCES p(code));', 'eligible-parent-key', True)
        candidate = next(c for c in relation['candidates'] if c['kind'] == 'unique-constraint-index')
        self.assertEqual([r['name'] for r in candidate['key_columns']], ['code'])
        self.assertEqual([r['name'] for r in candidate['auxiliary_columns']], ['id'])
        self.assertTrue(candidate['eligible'])

    def test_rejection_reasons_are_independent(self):
        relation = self.relation('CREATE TABLE p(code TEXT COLLATE NOCASE, other TEXT); CREATE INDEX bad ON p(code COLLATE BINARY, lower(other)) WHERE code IS NOT NULL; CREATE TABLE c(x REFERENCES p(code));', 'no-eligible-parent-key', False)
        self.assertEqual({r['code'] for r in relation['candidates'][0]['reasons']}, {'not-unique', 'partial-index', 'expression-key', 'key-columns-mismatch', 'collation-mismatch'})

    def test_multiple_candidates_keep_rejections(self):
        relation = self.relation('CREATE TABLE p(code TEXT COLLATE NOCASE, other TEXT); CREATE UNIQUE INDEX a_bad ON p(code COLLATE BINARY); CREATE UNIQUE INDEX z_good ON p(code); CREATE TABLE c(x REFERENCES p(code));', 'eligible-parent-key', True)
        self.assertEqual([(c['name'], c['eligible']) for c in relation['candidates']], [('a_bad', False), ('z_good', True)])

    def test_expression_and_multicolumn_are_not_single_keys(self):
        relation = self.relation('CREATE TABLE p(code TEXT, other TEXT); CREATE UNIQUE INDEX expr ON p(lower(code)); CREATE UNIQUE INDEX composite ON p(code,other); CREATE TABLE c(x REFERENCES p(code));', 'no-eligible-parent-key', False)
        self.assertEqual([c['eligible'] for c in relation['candidates']], [False, False])
        self.assertIn('expression-key', [r['code'] for r in relation['candidates'][1]['reasons']])

    def test_unrelated_integer_primary_key(self):
        relation = self.relation('CREATE TABLE p(id INTEGER PRIMARY KEY, code TEXT); CREATE TABLE c(x REFERENCES p(code));', 'no-eligible-parent-key', False)
        self.assertFalse(relation['primary_key']['applicable'])
        self.assertFalse(relation['candidates'][0]['eligible'])

    def test_no_index(self):
        relation = self.relation('CREATE TABLE p(code TEXT); CREATE TABLE c(x REFERENCES p(code));', 'no-eligible-parent-key', False)
        self.assertEqual(relation['candidates'], [])

    def test_self_reference(self):
        self.relation('CREATE TABLE p(code TEXT UNIQUE, x REFERENCES p(code));', 'eligible-parent-key', True)

    def test_quoted_names_and_ascii_equivalence(self):
        relation = self.relation('CREATE TABLE "Pa""rent"("Co""de" TEXT COLLATE "nocase"); CREATE UNIQUE INDEX "ux weird" ON "Pa""rent"("Co""de" COLLATE NOCASE); CREATE TABLE "ch ild"("x" REFERENCES "pA""RENT"("cO""DE"));', 'eligible-parent-key', True)
        self.assertEqual(relation['resolved_parent_table'], 'Pa"rent')
        self.assertEqual(relation['parent_column']['name'], 'Co"de')
        self.assertNotEqual(fold('Ä'), fold('ä'))

    def test_rowid_must_be_declared(self):
        relation = self.relation('CREATE TABLE p(id INTEGER PRIMARY KEY); CREATE TABLE c(x REFERENCES p(rowid));', 'no-eligible-parent-key', False)
        self.assertEqual(relation['reasons'][0]['code'], 'missing-parent-column')

    def test_declared_rowid_is_valid(self):
        self.relation('CREATE TABLE p(rowid TEXT UNIQUE); CREATE TABLE c(x REFERENCES p(rowid));', 'eligible-parent-key', True)

    def test_missing_parent(self):
        relation = self.relation('CREATE TABLE c(x REFERENCES missing(code));', 'no-eligible-parent-key', False)
        self.assertEqual(relation['reasons'][0]['code'], 'missing-parent-table')

    def test_missing_column(self):
        relation = self.relation('CREATE TABLE p(other TEXT UNIQUE); CREATE TABLE c(x REFERENCES p(code));', 'no-eligible-parent-key', False)
        self.assertEqual(relation['reasons'][0]['code'], 'missing-parent-column')
        self.assertEqual(len(relation['candidates']), 1)
        self.assertFalse(relation['candidates'][0]['eligible'])

    def test_business_rows_are_not_queried(self):
        path = self.database('CREATE TABLE p(code TEXT UNIQUE); CREATE TABLE c(x REFERENCES p(code)); INSERT INTO c VALUES(NULL);')
        queries = []
        real_connect = sqlite3.connect
        def connect(*args, **kwargs):
            db = real_connect(*args, **kwargs)
            db.set_trace_callback(queries.append)
            return db
        with patch('fk_parent_key.checker.sqlite3.connect', side_effect=connect):
            self.assertEqual(check(path)['exit_code'], 0)
        self.assertTrue(queries)
        for query in queries:
            self.assertTrue(query.startswith('PRAGMA ') or query.startswith('SELECT type, name, tbl_name, sql FROM main.sqlite_schema'), query)


class Coverage(Fixture):
    def test_known_and_unsupported_together(self):
        sql = schema('B') + '''
CREATE TABLE compound(a,b,PRIMARY KEY(a,b));
CREATE TABLE multi(x,y,FOREIGN KEY(x,y) REFERENCES compound(a,b));
CREATE TABLE implicit(x REFERENCES parent);
CREATE TABLE custom(code TEXT COLLATE SPECIAL UNIQUE);
CREATE TABLE customchild(x REFERENCES custom(code));
CREATE TABLE bracket([code] TEXT UNIQUE);
CREATE TABLE bracket_child(x REFERENCES bracket(code));
CREATE VIEW v AS SELECT code FROM parent;
CREATE TABLE view_child(x REFERENCES v(code));
'''
        path = self.database(sql, setup=lambda db: db.create_collation('SPECIAL', lambda a,b: (a>b)-(a<b)))
        report = check(path)
        self.assertEqual(report['exit_code'], 2)
        self.assertEqual(report['coverage']['total_relations'], 6)
        self.assertEqual(report['coverage']['incompatible'], 1)
        self.assertEqual(report['coverage']['not_analyzed'], 5)
        self.assertEqual(report['relations'][1]['child_table'], 'child')
        self.assertEqual(report['relations'][1]['status'], 'no-eligible-parent-key')

    def test_multiple_fk_ids_within_one_child(self):
        path = self.database('CREATE TABLE p(a TEXT UNIQUE, b TEXT); CREATE TABLE c(x REFERENCES p(a), y REFERENCES p(b));')
        report = check(path)
        self.assertEqual(report['coverage']['total_relations'], 2)
        self.assertEqual({r['fk_id'] for r in report['relations']}, {0, 1})
        self.assertEqual({r['status'] for r in report['relations']}, {'eligible-parent-key', 'no-eligible-parent-key'})

    def test_known_valid_key_beside_unknown_candidate(self):
        self.relation('CREATE TABLE p(code TEXT UNIQUE); CREATE UNIQUE INDEX odd ON p(code COLLATE SPECIAL); CREATE TABLE c(x REFERENCES p(code));', 'eligible-parent-key', True, setup=lambda db: db.create_collation('SPECIAL', lambda a,b: (a>b)-(a<b)))

    def test_virtual_parent(self):
        relation = self.relation('CREATE VIRTUAL TABLE p USING fts5(code); CREATE TABLE c(x REFERENCES p(code));', 'not-analyzed')
        self.assertEqual(relation['reasons'][0]['code'], 'unsupported-parent-table')

    def test_custom_index_collation(self):
        relation = self.relation('CREATE TABLE p(code TEXT); CREATE UNIQUE INDEX ux ON p(code COLLATE SPECIAL); CREATE TABLE c(x REFERENCES p(code));', 'not-analyzed', setup=lambda db: db.create_collation('SPECIAL', lambda a,b: (a>b)-(a<b)))
        self.assertIsNone(relation['candidates'][0]['eligible'])

    def test_empty_inventory(self):
        for sql in ['', 'CREATE TABLE p(x);']:
            path = self.database(sql, name=str(len(list(self.directory.iterdir()))) + '.sqlite')
            result = self.run_cli(path, '--json')
            self.assertEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout)['relations'], [])
            self.assertIn('empty relationship inventory', self.run_cli(path).stdout)


class DeclarationParsing(Fixture):
    def test_nested_check_and_other_columns(self):
        relation = self.relation('''CREATE TABLE p(
            code TEXT /* COLLATE NOCASE, ) */ CHECK((code COLLATE RTRIM) != 'COLLATE NOCASE'),
            other TEXT COLLATE NOCASE DEFAULT ('x,y'),
            CONSTRAINT "COLLATE" CHECK(other COLLATE RTRIM != 'a')
        ); CREATE UNIQUE INDEX ux ON p(code COLLATE BINARY);
        CREATE TABLE c(x REFERENCES p(code));''', 'eligible-parent-key', True)
        declaration = relation['parent_column']['declaration']
        self.assertEqual(declaration['collation'], 'BINARY')
        self.assertIn('no column COLLATE clause', declaration['collation_source'])

    def test_comments_quotes_parentheses(self):
        parsed = declarations('CREATE TABLE "p"("co""de" TEXT -- COLLATE NOCASE\n COLLATE RTRIM CHECK(length("co""de") > 0), other TEXT);', 'p')
        self.assertEqual(parsed['co"de']['collation'], 'RTRIM')
        self.assertEqual(parsed['other']['collation'], 'BINARY')

    def test_ambiguous_or_unsupported(self):
        for sql in ['CREATE TABLE p(code TEXT COLLATE NOCASE COLLATE BINARY)',
                    'CREATE TABLE p AS SELECT 1 AS code',
                    'CREATE TABLE p([code] TEXT)',
                    "CREATE TABLE p('code' TEXT)",
                    'CREATE TABLE p(code TEXT) WILD',
                    'CREATE TABLE p(code TEXT /* unclosed',
                    'CREATE TABLE p(code TEXT CHECK(' + '(' * 101 + '1' + ')' * 101 + '))']:
            with self.subTest(sql=sql), self.assertRaises(UnsupportedDDL):
                declarations(sql, 'p')

    def test_repeated_collate_stays_incomplete(self):
        self.relation('CREATE TABLE p(code TEXT COLLATE NOCASE COLLATE BINARY UNIQUE); CREATE TABLE c(x REFERENCES p(code));', 'not-analyzed')

    def test_single_quoted_reference_stays_incomplete(self):
        self.relation("CREATE TABLE p(code TEXT UNIQUE); CREATE TABLE c(x REFERENCES 'p'(code));", 'not-analyzed')


class SnapshotSafety(Fixture):
    def test_missing_invalid_directory_and_symlink(self):
        missing = self.directory / 'missing.sqlite'
        self.assertEqual(self.run_cli(missing, '--json').returncode, 3)
        self.assertFalse(missing.exists())
        invalid = self.directory / 'bad.sqlite'
        invalid.write_bytes(b'not a database')
        valid = self.database()
        link = self.directory / 'link.sqlite'
        link.symlink_to(valid)
        for path in [invalid, self.directory, link]:
            with self.subTest(path=path), self.assertRaises(InputError):
                check(path)

    def test_sidecars_and_wal_format(self):
        path = self.database()
        for suffix in ['-wal', '-journal', '-shm']:
            sidecar = Path(str(path) + suffix)
            sidecar.write_bytes(b'')
            with self.assertRaisesRegex(InputError, 'sidecar'):
                check(path)
            sidecar.unlink()
        data = bytearray(path.read_bytes())
        data[18] = data[19] = 2
        path.write_bytes(data)
        with self.assertRaisesRegex(InputError, 'WAL format'):
            check(path)

    def test_limits_and_timeout(self):
        path = self.database(schema('A'))
        for options, message in [({'max_bytes': 100}, 'byte limit'), ({'max_objects': 1}, 'Object/metadata'), ({'timeout': 1e-12}, 'timeout')]:
            with self.subTest(options=options), self.assertRaisesRegex(InputError, message):
                check(path, **options)
        for option in ['nan', 'inf', '-1', '0']:
            result = self.run_cli(path, '--json', '--timeout', option)
            self.assertEqual(result.returncode, 3)

    def test_repeated_candidate_evidence_obeys_budget(self):
        sql = 'CREATE TABLE p(code TEXT);'
        sql += ''.join(f'CREATE UNIQUE INDEX ux{i} ON p(code);' for i in range(20))
        sql += ''.join(f'CREATE TABLE child{i}(x REFERENCES p(code));' for i in range(20))
        path = self.database(sql)
        self.assertEqual(check(path)['coverage']['eligible'], 20)
        with self.assertRaisesRegex(InputError, 'Object/metadata'):
            check(path, max_objects=300)

    def test_repeated_parent_evidence_obeys_budget(self):
        sql = 'CREATE TABLE p(' + ','.join(f'c{i} TEXT' for i in range(200)) + ');'
        sql += ''.join(f'CREATE TABLE child{i}(x REFERENCES p(c0));' for i in range(200))
        path = self.database(sql)
        with self.assertRaisesRegex(InputError, 'Object/metadata'):
            check(path, max_objects=1500)

    def test_repeated_sql_obeys_byte_budget(self):
        sql = 'CREATE TABLE p(code TEXT CHECK(code != "' + 'x' * 8000 + '"));'
        sql += ''.join(f'CREATE TABLE child{i}(x REFERENCES p(code));' for i in range(25))
        path = self.database(sql)
        with self.assertRaisesRegex(InputError, 'evidence byte limit'):
            check(path, max_bytes=path.stat().st_size)

    def test_concurrent_change_and_late_sidecar(self):
        path = self.database(schema('A'))
        original_inspect = Inspector.inspect
        def mutate(inspector):
            result = original_inspect(inspector)
            with path.open('ab') as stream:
                stream.write(b'x')
            return result
        with patch.object(Inspector, 'inspect', mutate), self.assertRaisesRegex(InputError, 'Concurrent'):
            check(path)
        path2 = self.database(schema('A'), name='another.sqlite')
        def sidecar(inspector):
            result = original_inspect(inspector)
            Path(str(path2) + '-wal').touch()
            return result
        with patch.object(Inspector, 'inspect', sidecar), self.assertRaisesRegex(InputError, 'sidecar'):
            check(path2)

    def test_same_size_change_detected_by_hash(self):
        path = self.database(schema('A'))
        original_inspect = Inspector.inspect
        def mutate(inspector):
            result = original_inspect(inspector)
            data = bytearray(path.read_bytes())
            data[-1] ^= 1
            path.write_bytes(data)
            return result
        with patch.object(Inspector, 'inspect', mutate), self.assertRaisesRegex(InputError, 'Concurrent'):
            check(path)

    def test_content_corruption_and_safe_path(self):
        path = self.database(schema('A'), name='data?#.sqlite')
        self.assertEqual(check(path)['exit_code'], 0)
        data = bytearray(path.read_bytes())
        data[100:108] = b'\xFF' * 8
        path.write_bytes(data)
        self.assertEqual(self.run_cli(path, '--json').returncode, 3)


if __name__ == '__main__':
    unittest.main()
