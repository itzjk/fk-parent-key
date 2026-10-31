# Validation

Verified 2026-10-31 with generated fixtures and no production data.

| Check | Observed result |
| --- | --- |
| `python3 -m unittest discover -s tests -v` | 38 tests passed on Python 3.9.6 / SQLite 3.51.0 |
| Same suite with Python 3.15.1 | 38 tests passed on SQLite 3.54.1 |
| Independent clean wheel installation | Passed; see [new-user review](review.md) |
| pipx install from a clean local copy | Installed fk-parent-key 0.1.0; console entry point runs |
| Installed CLI on A/B/C/D | Exit codes 0/1/1/0; exact collation and partial reasons verified |
| Repaired B/C on copies | Exit 0 for both; only B/C relation reports changed |
| Native oracle | Four schemas created; four integrity checks OK; B/C fail both pragma and NULL INSERT |
| Independent differential matrix | 36 combinations agreed with native SQLite |
| Four deliberately broken implementations | All four caused assertion failures |
| Snapshot safety | Hashes unchanged; missing input not created; sidecars, limits and detected concurrent changes refused |
| Repeated parent evidence and SQL | Both amplification regressions refused their limits |
| `vhs validate docs/demo.tape` | Passed |
| VHS recording | 17.44 seconds, 1420 x 650, 436 frames; both diagnostic screens visually inspected |
| Source compilation | Passed |

The deliberate faults were accepting every UNIQUE index, ignoring partial
indexes, substituting the index collation for the declared column collation,
and checking only the first candidate. Each failed the integration suite.

The demo used VHS built from upstream source with its Chromium launcher
configured for a headless browser in single-process mode. This accommodates
the recording environment; the tape invokes the installed CLI against freshly
generated SQLite files. It contains no fabricated terminal output. The stored
[terminal transcript](demo-output.txt) and [sample JSON report](sample-report.json)
provide inspectable counterparts to the GIF.

The GitHub installation command was attempted and returned `Repository not
found`, since the target repository is not published. Local pipx and wheel
installation validate packaging; they do not validate the future remote URL.
Hosted CI and public badge/link availability remain release checks. No push
or publication was performed.

These are synthetic schema and safety tests. They establish no demand,
production incident frequency or time savings. See [alternatives](alternatives.md)
for the pre-implementation comparison, including duck-sqllsp's documentary-only
coverage.
