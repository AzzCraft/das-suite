# Verify Requirements — DAS Suite

Run `bash scripts/verify --full` for the complete public release gate.
It validates the checked-in v1.4 closure schemas, catalog/profile/lock parity,
the public CLI and closure tests, links, source-boundary terms, and generated
cache residue. `--standalone` has the same coverage; `--workspace` additionally
requires the three sibling repositories to be at their allowed locked state.

Requirements: Python 3.10+, Bash 3.2+, Git 2.30+, and the exact packages in
`requirements.txt`.
