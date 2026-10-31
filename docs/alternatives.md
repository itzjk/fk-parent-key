# Alternatives checked before implementation

Checked 2026-10-31 with the synthetic four-case reproduction in
`examples/four_cases.py`. This is a narrow comparison, not an exhaustive search
or evidence of customer demand. No production databases were used.

| Tool | Observation on A / B / C / D | Parent candidate explanation |
| --- | --- | --- |
| SQLite 3.51.0 `PRAGMA foreign_key_check` | empty / mismatch / mismatch / empty | Detects B and C already; does not explain the discarded index |
| Direct pragmas | `index_list` shows four UNIQUE indexes; C is partial. `index_xinfo` shows BINARY / BINARY / BINARY / NOCASE | Enough evidence after manually locating the column declaration |
| sqlite-utils 3.38 | Running the pragma returns the same empty/error pattern; `PRAGMA index_xinfo(ux)` exposes raw metadata | No correlated candidate reasons in these commands |
| SQLFluff 4.1.0 | `lint --dialect sqlite --format json CASE.sql` reports LT01, CP02 and PRS in all four schemas | No parent eligibility explanation in this run |
| sqlsift 0.3.0 | `check --dialect sqlite --schema CASE.sql query.sql` accepts `INSERT INTO child(code) VALUES(NULL)` in all four cases | No parent eligibility explanation in this run |
| duck-sqllsp | README and full rule reference reviewed; rules sql304, sql560 and sql605 concern self-reference, arity and SET NULL | No equivalent found in the documented rules; runtime not tested, Rust toolchain unavailable |

Sources: [SQLite required indexes](https://www.sqlite.org/foreignkeys.html#required_and_suggested_database_indexes),
[pragma reference](https://www.sqlite.org/pragma.html),
[sqlite-utils CLI](https://sqlite-utils.datasette.io/en/stable/cli.html),
[SQLFluff rules](https://docs.sqlfluff.com/en/stable/reference/rules/),
[sqlsift](https://github.com/yukikotani231/sqlsift),
[duck-sqllsp README](https://github.com/gentleeduck/duck-sqllsp),
[duck-sqllsp rules](https://github.com/gentleeduck/duck-sqllsp/blob/master/dsl-analysis/docs/rules.md).

## The small-script baseline

Run `python3 docs/baseline.py snapshot.sqlite` to dump the necessary metadata
for a small trusted fixture. It does not read business rows. On B it exposes
NOCASE in `parent_sql` and BINARY in `xinfo`, but requires the reader to parse
the declaration and correlate them. It neither classifies relations nor tracks
coverage, handles missing parents, or enforces snapshot safety limits.

The package earns its scope only by doing that correlation automatically,
preserving every rejection, and testing it against the engine. If manual
metadata is enough for your workflow, use the script or sqlite-utils. This
project adds no migration runner, repair command or SQL lint rules.

## Reproduce the comparison

Generate `.work/cases` with `python3 examples/four_cases.py .work/cases`.
The native oracle is independent of the checker:
`python3 examples/four_cases.py` runs all four in-memory cases, including
`integrity_check`, `foreign_key_check` and an INSERT. The recorded result is
[native-oracle.json](native-oracle.json).

Example commands after installing the comparison tools separately:

```sh
sqlite-utils .work/cases/B.sqlite 'PRAGMA foreign_key_check'
sqlite-utils .work/cases/B.sqlite 'PRAGMA index_xinfo(ux)'
sqlfluff lint --dialect sqlite --format json .work/cases/B.sql
sqlsift check --dialect sqlite --schema .work/cases/B.sql .work/cases/query.sql
```

`query.sql` contains `INSERT INTO child(code) VALUES(NULL);`. These commands
are comparison instructions, not runtime dependencies of fk-parent-key.
