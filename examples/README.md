# Four independent databases

From a clone, run:

```sh
python3 examples/four_cases.py .work/demo
python3 examples/four_cases.py
```

The first command generates four databases and their DDL; it refuses existing
fixture database paths. The second runs an independent native oracle in four
in-memory databases. It never imports the checker.

| Case | Column | UNIQUE index | Result |
| --- | --- | --- | --- |
| A | BINARY | BINARY, full | eligible-parent-key |
| B | NOCASE | BINARY, full | no-eligible-parent-key: collation-mismatch |
| C | BINARY | BINARY, partial WHERE code IS NOT NULL | no-eligible-parent-key: partial-index |
| D | NOCASE | NOCASE, full | eligible-parent-key |

```sh
fk-parent-key check .work/demo/A.sqlite
fk-parent-key check .work/demo/B.sqlite
fk-parent-key check .work/demo/C.sqlite
fk-parent-key check .work/demo/D.sqlite
```

A and D exit 0; B and C exit 1. All four schemas were created successfully
with foreign keys enabled first. All four report `integrity_check: ok`.
B and C fail both `foreign_key_check` and `INSERT INTO child VALUES(NULL)`
with a mismatch. A and D accept the NULL INSERT and return an empty pragma
result. The NULL still requires SQLite to resolve the parent-key schema when
preparing the operation.

Repair a separate set of copies:

```sh
python3 examples/repair_copies.py .work/demo .work/fixed
fk-parent-key check .work/fixed/B.sqlite
fk-parent-key check .work/fixed/C.sqlite
```

B now uses a NOCASE index; C now uses a full index. Only those two relation
reports change. The integration suite also verifies that A and D remain
unchanged, and checks the repairs against SQLite's own pragma.

To record the terminal demo after a clone installation, run
`vhs docs/demo.tape` from the repository root. The tape generates its own
fixtures under a fresh temporary directory and invokes the installed CLI.
