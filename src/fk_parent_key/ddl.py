from dataclasses import dataclass
import re


class UnsupportedDDL(ValueError):
    pass


def fold(value):
    return value.translate(str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'))


@dataclass(frozen=True)
class Token:
    text: str
    kind: str
    start: int
    end: int

    def is_word(self, word):
        return self.kind == 'word' and fold(self.text) == fold(word)


def identifier(token):
    if token.kind == 'identifier':
        return token.text[1:-1].replace('""', '"')
    if token.kind == 'word' and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', token.text):
        return token.text
    raise UnsupportedDDL('Identifier syntax requires an ASCII name or double quotes')


def tokens(sql, tick=lambda: None):
    if not sql or len(sql.encode('utf-8')) > 1024 * 1024:
        raise UnsupportedDDL('Missing or oversized CREATE TABLE SQL')
    result = []
    pos = 0
    while pos < len(sql):
        tick()
        start = pos
        if sql[pos].isspace():
            pos += 1
            continue
        if sql.startswith('--', pos):
            end = sql.find('\n', pos + 2)
            pos = len(sql) if end < 0 else end + 1
            continue
        if sql.startswith('/*', pos):
            end = sql.find('*/', pos + 2)
            if end < 0:
                raise UnsupportedDDL('Unclosed comment')
            pos = end + 2
            continue
        char = sql[pos]
        if char in '\'"`[':
            closing = ']' if char == '[' else char
            pos += 1
            while pos < len(sql):
                if sql[pos] == closing:
                    pos += 1
                    if char != '[' and pos < len(sql) and sql[pos] == closing:
                        pos += 1
                        continue
                    break
                pos += 1
            else:
                raise UnsupportedDDL('Unclosed quoted token')
            kind = {'"': 'identifier', "'": 'string'}.get(char, 'unsupported-identifier')
        elif char.isalpha() or char == '_':
            pos += 1
            while pos < len(sql) and (sql[pos].isalnum() or sql[pos] in '_$'):
                pos += 1
            kind = 'word'
        else:
            pos += 1
            kind = 'symbol'
        result.append(Token(sql[start:pos], kind, start, pos))
    return result


def top_level(parts):
    depth = 0
    for token in parts:
        if token.text == '(':
            depth += 1
            if depth > 100:
                raise UnsupportedDDL('DDL nesting exceeds 100')
        elif token.text == ')':
            depth -= 1
            if depth < 0:
                raise UnsupportedDDL('Unbalanced parentheses')
        elif depth == 0:
            yield token
    if depth:
        raise UnsupportedDDL('Unbalanced parentheses')


def declarations(sql, expected_table, tick=lambda: None):
    parts = tokens(sql, tick)
    if any(t.kind == 'unsupported-identifier' for t in parts):
        raise UnsupportedDDL('Backtick and bracket identifiers are outside this release')
    for i, token in enumerate(parts):
        if token.is_word('REFERENCES'):
            if i + 1 >= len(parts):
                raise UnsupportedDDL('Missing REFERENCES table')
            identifier(parts[i + 1])
            j = i + 2
            if j < len(parts) and parts[j].text == '(':
                j += 1
                while j < len(parts) and parts[j].text != ')':
                    identifier(parts[j])
                    j += 1
                    if j < len(parts) and parts[j].text == ',':
                        j += 1
    pos = 0
    for word in ['CREATE', 'TABLE']:
        if pos >= len(parts) or not parts[pos].is_word(word):
            raise UnsupportedDDL('Only ordinary CREATE TABLE declarations are supported')
        pos += 1
    if pos < len(parts) and parts[pos].is_word('IF'):
        if [fold(t.text) for t in parts[pos:pos + 3]] != ['if', 'not', 'exists']:
            raise UnsupportedDDL('Unsupported CREATE TABLE prefix')
        pos += 3
    if pos >= len(parts) or fold(identifier(parts[pos])) != fold(expected_table):
        raise UnsupportedDDL('CREATE TABLE name does not match metadata')
    pos += 1
    if pos >= len(parts) or parts[pos].text != '(':
        raise UnsupportedDDL('CREATE TABLE AS and qualified DDL are not supported')
    pos += 1
    depth = 0
    groups = [[]]
    while pos < len(parts):
        tick()
        token = parts[pos]
        pos += 1
        if token.text == ')' and depth == 0:
            break
        if token.text == ',' and depth == 0:
            groups.append([])
            continue
        groups[-1].append(token)
        if token.text == '(':
            depth += 1
            if depth > 100:
                raise UnsupportedDDL('DDL nesting exceeds 100')
        elif token.text == ')':
            depth -= 1
    else:
        raise UnsupportedDDL('Missing table closing parenthesis')
    suffix = [fold(t.text) for t in parts[pos:] if t.text != ';']
    if suffix not in [[], ['strict'], ['without', 'rowid'],
                      ['without', 'rowid', ',', 'strict'], ['strict', ',', 'without', 'rowid']]:
        raise UnsupportedDDL('Unsupported CREATE TABLE suffix')
    result = {}
    for group in groups:
        if not group:
            raise UnsupportedDDL('Empty column definition')
        if group[0].kind == 'word' and fold(group[0].text) in {'constraint', 'primary', 'unique', 'check', 'foreign'}:
            list(top_level(group))
            continue
        name = identifier(group[0])
        flat = list(top_level(group[1:]))
        collations = []
        for i, token in enumerate(flat):
            if token.is_word('COLLATE'):
                if i + 1 >= len(flat):
                    raise UnsupportedDDL('Missing collation name')
                collations.append(identifier(flat[i + 1]))
        if len(collations) > 1:
            raise UnsupportedDDL('Repeated column COLLATE clauses are ambiguous')
        collation = collations[0].upper() if collations else 'BINARY'
        result[fold(name)] = {
            'name': name,
            'collation': collation,
            'collation_source': 'column COLLATE clause in sqlite_schema.sql' if collations else 'no column COLLATE clause in sqlite_schema.sql; SQLite default BINARY',
            'declaration': sql[group[0].start:group[-1].end],
            'supported_collation': collation in {'BINARY', 'NOCASE', 'RTRIM'},
        }
    return result
