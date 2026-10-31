# Independent new-user review

Reviewed 2026-10-31, starting with README.md before inspecting implementation.
Environment: Python 3.9.6, SQLite 3.51.0, macOS. No production data was used.

## Reproduction

The advertised GitHub repository is not published yet, so its remote pipx
installation could not be verified. Instead, I copied the working tree to
`.work/reviewer`, excluding Git metadata, working files and build artifacts,
created a fresh virtual environment, and followed the documented clone-install
alternative:

```sh
python3 -m venv .work/reviewer-venv
.work/reviewer-venv/bin/python -m pip install .work/reviewer
cd .work/reviewer
python3 -m unittest discover -s tests -v
python3 examples/four_cases.py .work/demo
python3 examples/four_cases.py
python3 examples/repair_copies.py .work/demo .work/fixed
```

The wheel built and installed successfully. After refreshing the copied source
and tests to include the final safety hardening, a fresh wheel installation and
all 38 tests passed. Invoking the
installed `fk-parent-key check FILE --json` executable on all eight generated
and repaired snapshots produced:

| Snapshot set | A | B | C | D |
| --- | --- | --- | --- | --- |
| Original exit code | 0 | 1 | 1 | 0 |
| Repaired-copy exit code | 0 | 0 | 0 | 0 |

The native oracle independently reproduced the documented mismatch errors for
B and C. All eight CLI checks left snapshot SHA-256 digests unchanged.

## Adversarial checks

An additional 36-case matrix combined all three supported declared collations,
all three index collations, and full UNIQUE, partial UNIQUE, non-unique and
expression indexes. Every checker verdict matched preparation/execution of a
NULL child INSERT with SQLite foreign keys enabled. Seven additional checks
refused tiny byte, metadata and deadline limits; each of the three empty
sidecars; and a symlink. The input digest remained unchanged.

I also reviewed the declaration tokenizer, candidate aggregation, identifier
quoting, read-only private-copy handling, budgets and exit precedence. I found
no confirmed parent-key classification or input-mutation defect within the
documented scope.
The test suite covers composite/implicit references, unsupported identifiers
and declarations, custom collations, missing objects, multiple candidates,
WITHOUT ROWID auxiliary index terms, and concurrent-change detection.

The review exposed an output-amplification gap in repeated parent evidence,
which was subsequently fixed. I reinstalled the final wheel and independently
confirmed that a 200-column parent with 200 single-column child references
now refuses `max_objects=1500`. A separate 25-reference fixture with an 8 KB
parent declaration refuses repeated SQL evidence beyond `max_bytes`. The
budget now charges repeated parent/primary-key records as well as index
candidates and their key/auxiliary records, and separately limits serialized
relation evidence bytes. All 36 independent native comparison cases still
matched after the fix.

## Remaining limits

Remote pipx installation, the public badge/link targets and hosted CI remain
unverified until publication. This review does not validate every Python/SQLite
combination, corrupted file variant or adversarial filesystem race. The
documented requirement for an already consistent snapshot remains necessary;
the checks are change detection, not an atomic capture of a live database.
Synthetic agreement does not demonstrate customer demand or production use.
