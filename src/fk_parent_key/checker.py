from collections import Counter
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import stat
import tempfile
import time

from .ddl import UnsupportedDDL, declarations, fold


class InputError(ValueError):
    pass


class Deadline:
    def __init__(self, seconds):
        self.end = time.monotonic() + seconds

    def check(self):
        if time.monotonic() >= self.end:
            raise InputError('Analysis timeout exceeded')

    def expired(self):
        return time.monotonic() >= self.end


class Budget:
    def __init__(self, limit, deadline, max_evidence_bytes):
        self.remaining = limit
        self.evidence_remaining = max_evidence_bytes
        self.deadline = deadline

    def consume(self, count=1):
        self.deadline.check()
        self.remaining -= count
        if self.remaining < 0:
            raise InputError('Object/metadata limit exceeded')

    def evidence(self, relation):
        self.consume(1 + len(relation.get('parent_table_xinfo', [])) + len((relation.get('primary_key') or {}).get('columns', [])))
        encoded = json.dumps(relation, indent=2, ensure_ascii=True).encode('utf-8')
        self.evidence_remaining -= len(encoded) + 4 * encoded.count(b'\n') + 64
        self.deadline.check()
        if self.evidence_remaining < 0:
            raise InputError('Report evidence byte limit exceeded')

    def rows(self, cursor):
        result = []
        for row in cursor:
            self.consume()
            result.append(dict(row))
        return result


def reason(code, message, **evidence):
    return dict(code=code, message=message, **evidence)


def fingerprint(path):
    value = path.stat()
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def sidecars_absent(path):
    for suffix in ['-wal', '-shm', '-journal']:
        candidate = Path(str(path) + suffix)
        if candidate.exists() or candidate.is_symlink():
            raise InputError('Snapshot has a sidecar: ' + candidate.name)


def stream_digest(source, deadline, max_bytes, target=None):
    digest = hashlib.sha256()
    size = 0
    while True:
        deadline.check()
        block = source.read(1024 * 1024)
        if not block:
            break
        size += len(block)
        if size > max_bytes:
            raise InputError('Snapshot byte limit exceeded')
        digest.update(block)
        if target:
            target.write(block)
    return digest.hexdigest()


def quote(name):
    return '"' + name.replace('"', '""') + '"'


class Inspector:
    def __init__(self, db, budget):
        self.db = db
        self.budget = budget
        self.tables = {}
        self.cache = {}
        self.ddl_cache = {}
        self.index_cache = {}
        self.schema = self.rows('SELECT type, name, tbl_name, sql FROM main.sqlite_schema ORDER BY name COLLATE BINARY')
        self.objects = {fold(row['name']): row for row in self.schema if row['type'] in {'table', 'view'}}
        for row in self.rows('PRAGMA main.table_list'):
            if row['schema'] == 'main':
                self.tables[fold(row['name'])] = row

    def rows(self, sql):
        self.budget.deadline.check()
        return self.budget.rows(self.db.execute(sql))

    def pragma(self, operation, name):
        return self.rows('PRAGMA main.' + operation + '(' + quote(name) + ')')

    def columns(self, name):
        key = fold(name)
        if key not in self.cache:
            self.cache[key] = self.pragma('table_xinfo', name)
        return self.cache[key]

    def ddl(self, name):
        key = fold(name)
        if key not in self.ddl_cache:
            sql = self.objects[key]['sql']
            try:
                parsed = declarations(sql, name, self.budget.deadline.check)
                names = {fold(c['name']) for c in self.columns(name)}
                if set(parsed) != names:
                    raise UnsupportedDDL('Column declarations do not match table_xinfo')
                self.ddl_cache[key] = (parsed, None)
            except UnsupportedDDL as exc:
                self.ddl_cache[key] = (None, str(exc))
        return self.ddl_cache[key]

    def indexes(self, name):
        key = fold(name)
        if key not in self.index_cache:
            result = []
            for index in self.pragma('index_list', name):
                index['xinfo'] = self.pragma('index_xinfo', index['name'])
                index['sql'] = next((r['sql'] for r in self.schema if r['type'] == 'index' and r['name'] == index['name']), None)
                result.append(index)
            self.index_cache[key] = sorted(result, key=lambda r: r['name'])
        return self.index_cache[key]

    def ordinary(self, name):
        return self.tables.get(fold(name), {}).get('type') == 'table'

    def candidate(self, index, column, declaration):
        self.budget.consume(1 + len(index['xinfo']))
        keys = [r for r in index['xinfo'] if r['key']]
        auxiliary = [r for r in index['xinfo'] if not r['key']]
        reasons = []
        unknown = []
        if not index['unique']:
            reasons.append(reason('not-unique', 'Index does not enforce uniqueness', source='PRAGMA index_list.unique'))
        if index['partial']:
            reasons.append(reason('partial-index', 'A partial index cannot establish a parent key', source='PRAGMA index_list.partial'))
        if any(r['cid'] == -2 for r in keys):
            reasons.append(reason('expression-key', 'Index contains an expression key term', source='PRAGMA index_xinfo.cid'))
        if len(keys) != 1 or keys[0]['cid'] != column['cid']:
            reasons.append(reason('key-columns-mismatch', 'Index key columns do not exactly match the referenced column', expected=[column['name']], actual=[r['name'] for r in keys], source='PRAGMA index_xinfo rows with key=1'))
        for term in keys:
            coll = term['coll']
            if coll is None or fold(coll) not in {'binary', 'nocase', 'rtrim'}:
                unknown.append(reason('unsupported-index-collation', 'Index collation is outside BINARY, NOCASE and RTRIM', actual=coll, source='PRAGMA index_xinfo.coll'))
            if term['cid'] == column['cid'] and declaration and declaration['supported_collation'] and coll is not None and fold(coll) != fold(declaration['collation']):
                reasons.append(reason('collation-mismatch', 'Index collation differs from the parent column declaration', expected=declaration['collation'], actual=coll, expected_source=declaration['collation_source'], actual_source='PRAGMA index_xinfo.coll'))
        if not declaration:
            unknown.append(reason('unknown-column-collation', 'Parent column collation could not be extracted'))
        elif not declaration['supported_collation']:
            unknown.append(reason('unsupported-column-collation', 'Parent column uses a custom collation', actual=declaration['collation'], source=declaration['collation_source']))
        return {
            'kind': 'primary-key-index' if index['origin'] == 'pk' else 'unique-constraint-index' if index['origin'] == 'u' else 'index',
            'name': index['name'], 'unique': bool(index['unique']),
            'partial': bool(index['partial']), 'origin': index['origin'],
            'key_columns': keys, 'auxiliary_columns': auxiliary,
            'collation_source': 'PRAGMA index_xinfo.coll', 'sql': index['sql'],
            'eligible': None if unknown else False if reasons else True,
            'reasons': reasons, 'not_analyzed_reasons': unknown,
        }

    def relation(self, child, entries):
        entries = sorted(entries, key=lambda e: e['seq'])
        first = entries[0]
        parent = first['table']
        result = {
            'child_table': child, 'child_columns': [e['from'] for e in entries],
            'fk_id': first['id'], 'parent_table': parent,
            'parent_columns': [e['to'] for e in entries],
            'foreign_key_list': entries,
            'status': 'not-analyzed', 'reasons': [], 'candidates': [],
            'primary_key': None, 'parent_column': None, 'parent_sql': None,
            'child_sql': self.objects[fold(child)]['sql'],
        }
        def stop(code, message, status='not-analyzed'):
            result['status'] = status
            result['reasons'].append(reason(code, message))
            return result
        if len(entries) != 1:
            return stop('composite-foreign-key', 'Composite foreign keys are outside this release')
        if first['to'] is None:
            return stop('implicit-parent-key', 'Implicit primary-key references are outside this release')
        if not self.ordinary(child):
            return stop('unsupported-child-table', 'Child must be an ordinary main table')
        _, child_error = self.ddl(child)
        if child_error:
            return stop('unsupported-child-ddl', child_error)
        if fold(parent) not in self.objects:
            return stop('missing-parent-table', 'Referenced parent table does not exist in main', 'no-eligible-parent-key')
        parent = self.objects[fold(parent)]['name']
        result['resolved_parent_table'] = parent
        result['parent_sql'] = self.objects[fold(parent)]['sql']
        if not self.ordinary(parent):
            return stop('unsupported-parent-table', 'Views, virtual and shadow tables are outside this release')
        columns = self.columns(parent)
        result['parent_table_xinfo'] = columns
        pk = sorted([c for c in columns if c['pk']], key=lambda c: c['pk'])
        column = next((c for c in columns if fold(c['name']) == fold(first['to'])), None)
        indexes = self.indexes(parent)
        pk_indexes = [i for i in indexes if i['origin'] == 'pk']
        integer_pk = len(pk) == 1 and fold(pk[0]['type']) == 'integer' and not pk_indexes
        applicable = column is not None and len(pk) == 1 and pk[0]['cid'] == column['cid']
        result['primary_key'] = {'columns': pk, 'applicable': applicable,
                                 'storage': 'integer-rowid' if integer_pk else 'index' if pk_indexes else None,
                                 'backing_indexes': [i['name'] for i in pk_indexes],
                                 'source': 'PRAGMA table_xinfo.pk/type and PRAGMA index_list.origin'}
        if column is None:
            result['candidates'] = [self.candidate(i, {'cid': None, 'name': first['to']}, None) for i in indexes]
            for candidate in result['candidates']:
                candidate['eligible'] = False
                candidate['reasons'].append(reason('missing-parent-column', 'Referenced parent column does not exist'))
            return stop('missing-parent-column', 'Referenced name is not a declared parent column; implicit rowid aliases are not parent columns', 'no-eligible-parent-key')
        parsed, ddl_error = self.ddl(parent)
        declaration = parsed.get(fold(column['name'])) if parsed else None
        result['parent_column'] = dict(column, declaration=declaration)
        result['candidates'] = [self.candidate(i, column, declaration) for i in indexes]
        if integer_pk:
            reasons = [] if applicable else [reason('key-columns-mismatch', 'INTEGER PRIMARY KEY is a different column', expected=[column['name']], actual=[pk[0]['name']])]
            result['candidates'].insert(0, {
                'kind': 'integer-primary-key', 'name': None,
                'unique': True, 'partial': False, 'origin': 'pk',
                'key_columns': pk, 'auxiliary_columns': [],
                'eligible': applicable, 'reasons': reasons, 'not_analyzed_reasons': [],
                'source': 'PRAGMA table_xinfo and absence of a pk index in index_list',
            })
        if ddl_error:
            return stop('unsupported-parent-ddl', ddl_error)
        if not declaration['supported_collation']:
            return stop('unsupported-column-collation', 'Custom parent column collation cannot be analyzed')
        if column['hidden']:
            return stop('generated-parent-column', 'Generated or hidden parent columns are outside this release')
        if any(c['eligible'] is True for c in result['candidates']):
            result['status'] = 'eligible-parent-key'
        elif any(c['eligible'] is None for c in result['candidates']):
            return stop('uncertain-candidate', 'At least one candidate could not be fully analyzed')
        else:
            return stop('no-eligible-candidate', 'No primary key or index satisfies this parent-key requirement', 'no-eligible-parent-key')
        return result

    def inspect(self):
        relations = []
        for obj in self.schema:
            if obj['type'] != 'table':
                continue
            groups = {}
            for row in self.pragma('foreign_key_list', obj['name']):
                groups.setdefault(row['id'], []).append(row)
            for key in sorted(groups):
                relation = self.relation(obj['name'], groups[key])
                self.budget.evidence(relation)
                relations.append(relation)
        counts = Counter(r['status'] for r in relations)
        coverage = {
            'total_relations': len(relations),
            'analyzed_relations': len(relations) - counts['not-analyzed'],
            'not_analyzed': counts['not-analyzed'],
            'eligible': counts['eligible-parent-key'],
            'incompatible': counts['no-eligible-parent-key'],
            'complete': counts['not-analyzed'] == 0,
            'schema_objects': len(self.schema),
        }
        exit_code = 2 if not coverage['complete'] else 1 if coverage['incompatible'] else 0
        return {'schema_version': 1, 'sqlite_version': sqlite3.sqlite_version,
                'coverage': coverage, 'relations': relations, 'exit_code': exit_code,
                'scope': 'Explicit single-column foreign keys between ordinary main tables; parent-key schema eligibility only'}


def check(path, max_bytes=100 * 1024 * 1024, max_objects=10000, timeout=5.0):
    if max_bytes < 1 or max_objects < 1 or not math.isfinite(timeout) or timeout <= 0:
        raise InputError('Limits and timeout must be positive and finite')
    deadline = Deadline(timeout)
    try:
        original = Path(path).absolute()
        if original.is_symlink():
            raise InputError('Snapshot must not be a symlink')
        path = original.resolve(strict=True)
        if not stat.S_ISREG(path.stat().st_mode):
            raise InputError('Snapshot must be an existing regular file')
        sidecars_absent(path)
        before = fingerprint(path)
        if before[2] > max_bytes:
            raise InputError('Snapshot byte limit exceeded')
        with path.open('rb') as source, tempfile.TemporaryDirectory(prefix='fk-parent-key-') as temp:
            header = source.read(100)
            if len(header) < 100 or header[:16] != b'SQLite format 3\x00':
                raise InputError('Input is not a SQLite database with a complete header')
            if header[18:20] != b'\x01\x01':
                raise InputError('Snapshot must use rollback journal format, not WAL format')
            source.seek(0)
            private = Path(temp) / 'snapshot.sqlite'
            with private.open('wb') as target:
                digest = stream_digest(source, deadline, max_bytes, target)
            if fingerprint(path) != before:
                raise InputError('Concurrent snapshot change detected during copy')
            with closing(sqlite3.connect(private.as_uri() + '?mode=ro&immutable=1', uri=True, timeout=min(timeout, 1))) as db:
                db.row_factory = sqlite3.Row
                db.execute('PRAGMA query_only=ON')
                db.execute('PRAGMA trusted_schema=OFF')
                db.set_progress_handler(lambda: int(deadline.expired()), 1000)
                if sqlite3.sqlite_version_info < (3, 37, 0):
                    raise InputError('SQLite 3.37.0 or newer is required for table_list')
                report = Inspector(db, Budget(max_objects, deadline, max_bytes)).inspect()
            sidecars_absent(path)
            source.seek(0)
            after_digest = stream_digest(source, deadline, max_bytes)
            if digest != after_digest or fingerprint(path) != before:
                raise InputError('Concurrent snapshot change detected after analysis')
            deadline.check()
            report['snapshot'] = {'name': path.name, 'bytes': before[2], 'sha256': digest,
                                  'verification': 'unchanged file identity, size, timestamps and SHA-256; sidecars absent before and after'}
            return report
    except (OSError, sqlite3.Error) as exc:
        if deadline.expired():
            raise InputError('Analysis timeout exceeded') from exc
        raise InputError(str(exc)) from exc
