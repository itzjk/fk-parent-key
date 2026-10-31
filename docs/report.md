# Report contract, version 1

`check FILE --json` prints a JSON object with `schema_version: 1`,
`sqlite_version`, `snapshot`, `coverage`, `relations`, `scope`, and `exit_code`.
Input errors instead contain `schema_version`, `error`, and `exit_code: 3`.
Output is deterministic for identical bytes and the same SQLite runtime.

## Relation statuses

- `eligible-parent-key`: at least one candidate proves the parent-key requirement.
- `no-eligible-parent-key`: a supported relation has a missing parent table or
  column, or no eligible candidate.
- `not-analyzed`: the relation is inventoried but outside supported coverage.

No status certifies business rows, connection enforcement, action semantics,
index performance or other schema requirements. In particular, an empty
`foreign_key_check` result is not a substitute for this scope: for an empty
child with a missing parent table, it may return no rows even though preparing
an INSERT fails with `no such table`.

Each relation includes child columns, parent columns as written in the FK,
`resolved_parent_table` when found, a snapshot-local `fk_id`, raw
`foreign_key_list` entries, stored child and parent SQL, parent `table_xinfo`
when available, parsed column evidence, primary-key evidence, candidates and
relationship reasons. An unavailable evidence field is null or absent.
Names use SQLite's ASCII case equivalence; Unicode case folding is not used.

Each index candidate contains its name, origin, stored SQL, unique and partial
flags, raw `index_xinfo` key and auxiliary rows, eligibility and reason lists.
The source of index flags is `PRAGMA index_list`; collations come from
`PRAGMA index_xinfo.coll`. INTEGER PRIMARY KEY appears as a separate candidate
with a null index name because there is no backing PK index. PKs backed by an
index undergo the same collation comparison: a table-level PK can explicitly
choose a collation different from the column, and that index is not eligible
for an explicit reference to the column.

`primary_key.applicable` means its one declared column matches the target;
it is not a verdict about the backing index collation. Integer rowid aliases
are recognized through `table_xinfo` and the absence of a PK index, including
SQLite's distinction between inline `INTEGER PRIMARY KEY DESC` and table-level
`PRIMARY KEY(code DESC)`. The implicit `rowid`, `_rowid_` and `oid` pseudocolumns
are never substituted for a declared column.

## Reasons

Candidate rejections are independent and can occur together:

| Code | Evidence |
| --- | --- |
| `not-unique` | `index_list.unique=0` |
| `partial-index` | `index_list.partial=1`, regardless of the predicate |
| `expression-key` | A key term has `index_xinfo.cid=-2` |
| `key-columns-mismatch` | Key terms do not exactly equal the single referenced column |
| `collation-mismatch` | Parent declaration and index term have different collations |
| `missing-parent-column` | The referenced name has no declared column |

A collation mismatch includes `expected`, `actual`, `expected_source`, and
`actual_source`. The parent source is a column COLLATE clause, or the confirmed
absence of one in `sqlite_schema.sql` with SQLite's BINARY default. The complete
column declaration and parent SQL are retained. CHECK, DEFAULT and generated
expressions are skipped at their parenthesis depth, as are other columns and
table constraints. Repeated column COLLATE clauses are considered ambiguous.

Custom column or index collations produce uncertainty rather than inferred
semantics. Candidate `eligible` is null for incomplete evaluation, with
`not_analyzed_reasons` such as `unsupported-index-collation`,
`unknown-column-collation`, or `unsupported-column-collation`. Independently
known rejection reasons are still retained. A separate fully analyzed eligible
candidate can prove the relation; an unsupported parent column declaration
keeps the relation not analyzed.

Relation reasons include `no-eligible-candidate`, `missing-parent-table`,
`missing-parent-column`, `composite-foreign-key`, `implicit-parent-key`,
`unsupported-child-table`, `unsupported-parent-table`, `unsupported-child-ddl`,
`unsupported-parent-ddl`, `unsupported-column-collation`,
`generated-parent-column`, and `uncertain-candidate`.

## Coverage and exit precedence

Coverage includes `total_relations`, `analyzed_relations`, `eligible`,
`incompatible`, `not_analyzed`, `complete`, and `schema_objects`. A composite
foreign key counts once, not once per column. Relations are sorted by child
table name and local FK ID. All indexes are retained in name order.

1. Invalid input, corrupted/unreadable schema or safety limit failure: exit 3,
   error report; no partial report is trusted.
2. Any not-analyzed relation: exit 2, retaining known results and counts.
3. Complete analysis with any incompatible relation: exit 1.
4. Complete analysis without incompatibilities, including no FKs: exit 0.

CLI argument syntax errors also use argparse's code 2 and its usage message;
they are not JSON relationship reports.

## Snapshot handling

The input must be an existing regular file, not a symlink. It must have a
SQLite header in rollback journal format. `-wal`, `-shm` and `-journal` files,
including empty sidecars, are rejected before and after inspection. The
checker does not delete or checkpoint them. It never opens an absent path
through SQLite and never loads extensions or user-defined collations.

The file is copied to a temporary private directory while computing SHA-256.
Metadata is queried with `mode=ro&immutable=1`, `query_only=ON` and
`trusted_schema=OFF`. The copy is removed after the connection closes. The
original file descriptor is hashed again; path identity, size, nanosecond
modification/change timestamps and digest must match. These are detection
checks, not a locking protocol or an atomic live-database capture. A writer
that creates and removes a sidecar entirely between checks may evade sidecar
detection. Supply an already consistent snapshot; do not aim this at a live
writer and expect the tool to make it consistent.

No SQL queries read business rows. SHA-256 and the private copy necessarily
read the raw file bytes, so this is not an assertion that business-data bytes
never enter memory or local temporary storage. No trial INSERTs,
`foreign_key_check`, integrity scans or repairs run against the input.

Defaults: 100 MiB input and relationship evidence, 10000 combined schema and metadata records, and a
5-second analysis deadline. Each `sqlite_schema`, `table_list`, `table_xinfo`,
`foreign_key_list`, `index_list` and `index_xinfo` result row consumes a record.
Metadata is cached between relations. Each emitted relation, repeated parent column/PK record, index candidate and its key/auxiliary records also consume the record budget. Serialized relation evidence, including repeated SQL, consumes a separate byte budget with the same --max-bytes value as the input limit. This bounds amplification when many relationships share a wide parent or long SQL. Limits abort the entire report. Each
CREATE TABLE declaration is capped at 1 MiB and 100 parenthesis levels;
unsupported declarations are reported per relation. The deadline is checked
between copy/hash chunks, SQL steps and tokenizer tokens, with SQLite's
progress handler bounding long VM work. This is a local-file analysis
deadline, not a hard OS I/O cancellation guarantee.
