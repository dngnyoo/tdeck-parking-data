# Phase C reproduction

All commands run from repository root. Nothing here changes production
configuration or publishes model state. Use Python3.12+ (Windows may need
`python -m pip install tzdata`). Optional figures require matplotlib; this is an
offline analysis dependency, not a dependency of the Actions predictor.

The frozen data commit is `06237c8c2737c19631d8b3c1455925cf23d962b7`.
Selection was committed as `afcdf38da` before final scoring. Code/results are on
the `codex/tdeck-online-learning` review branch. Original `data/parking.csv` is
never overwritten by these commands. The new folder is gitignored.

## 1. Extract the frozen source and a validation-only prefix

This Python command works in PowerShell and avoids shell redirection changing
the CSV encoding:

```sh
python -c "from pathlib import Path; import subprocess; p=Path('.online-artifacts/phase-c-reproduce'); p.mkdir(parents=True,exist_ok=True); data=subprocess.check_output(['git','show','06237c8c2737c19631d8b3c1455925cf23d962b7:data/parking.csv']); (p/'source.csv').write_bytes(data); lines=data.splitlines(keepends=True); (p/'validation.csv').write_bytes(lines[0]+b''.join(r for r in lines[1:] if r[:10]<=b'2026-10-28'))"
```

The extracted source has20,259 rows. Only20,083 through Nov11 are replayed;176
later rows are excluded. Source SHA256 is in `test_manifest.json`; checkpoint
canonicalization handles CRLF/LF without rewriting CSV files.

## 2. Validation and selection first

```sh
python model/phase3/phase_c.py prepare --csv .online-artifacts/phase-c-reproduce/validation.csv --end 2026-10-28 --cache .online-artifacts/phase-c-reproduce/validation.json.gz
python model/phase3/phase_c.py select --cache .online-artifacts/phase-c-reproduce/validation.json.gz --out .online-artifacts/phase-c-reproduce/results
```

This evaluates the preregistered27 configurations on Oct15–28 only. It should
select14-day forgetting, prior strength50, alpha upper bound2. It does not update
`online_config.json`. Save this selection before examining final-test metrics.

## 3. Fixed-selection final test

```sh
python model/phase3/phase_c.py prepare --csv .online-artifacts/phase-c-reproduce/source.csv --end 2026-11-11 --cache .online-artifacts/phase-c-reproduce/test.json.gz
python model/phase3/phase_c.py test --cache .online-artifacts/phase-c-reproduce/test.json.gz --selection .online-artifacts/phase-c-reproduce/results/selection.json --out .online-artifacts/phase-c-reproduce/results
```

As in the original run, all learning events precede the forecasts they can
influence. Matched labels are revealed only after their90-second window closes.
Hyperparameters remain fixed throughout the test, while alpha learns causally.
Full and clean cohorts use identical trained models, with clean defined as both
raw origin and raw matched outcome inside[0,1300].

## 4. Full baseline parity and real-data fault/restart stress

```sh
python model/phase3/phase_c_parity.py --csv .online-artifacts/phase-c-reproduce/source.csv --cache .online-artifacts/phase-c-reproduce/test.json.gz --selection .online-artifacts/phase-c-reproduce/results/selection.json --out .online-artifacts/phase-c-reproduce/results/full_baseline_parity.json
python model/phase3/phase_c_stability.py --csv .online-artifacts/phase-c-reproduce/source.csv --selection .online-artifacts/phase-c-reproduce/results/selection.json --scratch .online-artifacts/phase-c-reproduce/faults --out .online-artifacts/phase-c-reproduce/results/stability.json
```

These local scenarios simulate missed executions,28 restarts, damaged state and
fallback. They do not create/modify a GitHub state branch or deploy Pages. The
entire final-test forecast set is audited against the original batch baseline.

## 5. Korean report, figures, and tests

```sh
python model/phase3/phase_c_report.py --out .online-artifacts/phase-c-reproduce/results --csv .online-artifacts/phase-c-reproduce/source.csv --source-commit 06237c8c2737c19631d8b3c1455925cf23d962b7
python -m unittest discover -s model/phase3/tests -v
node --test model/phase3/tests/state.test.mjs
```

Use `--no-figures` for the report if matplotlib is unavailable. Outputs include
candidate/validation/test/daily metrics, per-forecast pairs, paired day-bootstrap
intervals(seed20261112,2000 draws), input/cohort audit, state/parity diagnostics,
summary JSON, five PNG figures, and the Korean report. Numeric metrics should
reproduce to floating-point tolerance. Run times, timestamps, compressed cache
file hashes and line-ending-dependent raw file hashes can differ. The normalized
protocol hash ignores BOM/CRLF; the forecast/label data and selected settings are
deterministic. Test scores must not be used to reselect hyperparameters.

Production mode remains off. A shadow recommendation is not activation approval.
