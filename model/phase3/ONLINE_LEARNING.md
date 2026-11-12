# Persistent incremental M3 — Phase B

Implementation only: `online_config.json` ships with **mode off**. No state branch
is created, no online writer runs, and the frontend continues to use fixed-alpha
`predictions`. Phase C validation and Phase D activation require separate review.
The candidate settings are unvalidated; their implementation is not evidence of
improved accuracy. Collection code, collection workflow and raw CSV are unchanged.

Phase C offline validation is now documented in
[`docs/online-learning/phase_c/REPORT_KO.md`](../../docs/online-learning/phase_c/REPORT_KO.md).
The validation-selected prior strength50 is stored only in research results;
production configuration remains off with its original Phase B settings. The
test found improved120-minute MAE but worse15/30/60-minute MAE, so this is not
approval for general promotion. See the report for clipping cohorts, warning
metrics, day-block uncertainty, baseline parity and real-data recovery stress.

## Observation contract

The shared `observations.py` policy is `finite_clip_1300_v1`. Every finite free
value is clipped to [0,1300], regardless of magnitude. Positive finite reported
capacity is metadata: 1306 or 9999 are not rejection reasons and do not cause
proportional rescaling. Raw display values and the CSV remain unchanged.

Malformed/offset-free timestamps, nonnumeric/NaN/infinite free values, and
nonpositive/nonfinite capacities are structurally invalid. A known later timestamp
with invalid numeric values blocks forecasts rather than substituting an older
reading. An unparseable timestamp cannot be ordered; it is counted as invalid.

Counts `clipped_to_zero` and `clipped_to_capacity` refer to raw valid input rows,
including duplicates, not already-boundary values. `reported_capacity_mismatch`
is diagnostic only. Duplicate UTC timestamps are grouped by raw median **before**
clipping. Equal wall clock times during DST fall-back remain different instants.
The median implementation also avoids overflow for extreme finite values.

The same normalization feeds bootstrap, online baselines, origin features,
outcome matching, alpha updates and evaluation. Metrics use clipped actuals;
`raw_actual` remains in exported evaluation records. Calendar-shadow forecast
outputs are now bounded too. A separate legacy evaluation arm reproduces the
previous tolerance-based rejection policy to distinguish the normalization change
from the learning change. Its outcome coverage is reported separately.

## Exact baseline, not a running mean

For each completed local day d, query minute m, and relevant calendar category,
compute a daily mean mu and latest selected observation UTC instant l. Store sums
of `mu * 2**((l-R)/half_life)` and `2**((l-R)/half_life)` using a common UTC
reference R. Their ratio equals the original date-weighted baseline because the
query-instant factor cancels. Advancing R rescales both sums equally. Half-life
is exactly seven elapsed days, including DST.

The original circular +/-15-minute window, same-weekday preference, all-weekday
near-time fallback and global fallback are preserved. Separate normal/confirmed
GSU exception statistics preserve the existing calendar-shadow selection and its
minimum exception-date support. Calendar or numerical configuration changes alter
the checkpoint fingerprint and require an explicit compatible rebuild.

Only the unfinished day retains timestamp groups needed for exact duplicate
medians. Its values cannot enter the baseline until the replay origin advances
to the next local date. Daily finalization is not a full historical retraining.
An aggregate checkpoint cannot serve an earlier origin: historical queries must
replay from an earlier checkpoint. Target horizons use UTC elapsed time.

## Online alpha and delayed outcomes

Each issued internal forecast freezes `x=normalized_current-B(origin)` and
`baseline_target=B(target)`. When a target match closes, learn the one-dimensional
regression `actual-baseline_target = alpha*x`. Features are normalized by1300.
The estimator is:

```
Sxx <- decay*Sxx + x*x
Sxz <- decay*Sxz + x*z
prior = prior_strength * (prior_feature_scale_spaces/1300)**2
alpha = clip((prior*initial_alpha + Sxz)/(prior + Sxx), alpha_min, alpha_max)
```

The prior remains fixed around validated M3 coefficients1/1/1/.75. Fewer than20
matched pairs retain the initial alpha. A zero feature adds no slope information.
Candidate prior strength200, residual scale100 spaces (~7.7% capacity), forgetting
half-life14 days and bounds[0,2] are explicit **unvalidated starting candidates**.
The prior is equivalent to200 hypothetical squared features of that scale, not
200 statistically independent observations. Bounds permit positive persistence up
to twice the current deviation; negative reversal is outside this model family.
Sensitivity must compare prior strength50/200/800, half-life7/14/28 and upper
bounds1.5/2/3 using past validation only. No arbitrary SGD learning rate is used.

Each horizon matches the nearest valid observation within+/-90 seconds, tying to
the earlier UTC observation. The window closes only after its upper edge is
observed. Ninety seconds preserves existing matching tolerance and is below half
the nominal five-minute cadence. Missing outcomes expire without fabricated
labels. Delayed Actions runs replay all new observations sequentially; a long gap
settles retained candidates before pruning the three-hour ring. Pending IDs cannot
be trained twice. Baseline, optimizer, pending list and ingestion checkpoint are
saved as one transaction. Previously issued features are never recomputed.

Catch-up forecasts are hypothetical training/replay records, not claims of actual
historical website publication. Current state produces a separate
`online_shadow_predictions` field. Existing `predictions` always use fixed alpha;
existing `shadow_predictions` remain the independent calendar candidate. No
automatic promotion code exists.

## Ingestion, recovery and concurrency

Routine ingestion verifies the previously consumed byte prefix, then CSV-parses
only the new complete-line tail. Identical input is a no-op. A partial final line
waits for its newline. Prefix hashing/source provenance still reads O(file bytes);
the normal path does not parse or fit the whole history. This integrity I/O is
deliberate, not a claim of O(1) total file access.

Checkpoint offsets/hashes refer to the `utf8_lf_v1` representation (CRLF becomes
LF; a leading UTF-8 BOM is ignored), so Windows and Linux Git checkouts can resume
the same checkpoint. This representation is never written over the CSV. Public
`source_sha256` still authenticates actual source-file bytes and can legitimately
differ across line-ending conventions. Other historical content edits fail prefix
verification as before. `last_successful_update` is the deterministic observation
as-of time, not a nondeterministic wall-clock job timestamp.

New tail records are UTC-sorted. Late/duplicate records in the open day update
that day's grouped medians; sealed training pairs and issued origin features stay
unchanged. Thus retrospective duplicate corrections are not equivalent to a
counterfactual forecast made with information that had not arrived yet. Bootstrap
and incremental replay are equivalent for the same chronological event sequence.

Closed-day late arrivals are quarantined and the entire ingestion transaction
fails without saving its checkpoint. A rewritten/truncated consumed prefix also
fails. This prevents silently serving an inexact baseline: fixed batch M3 can
continue, and an operator explicitly rebuilds the online checkpoint. Future
sensor timestamps beyond the existing120-second tolerance similarly prevent a
routine state commit and are retried later; the CSV itself is never changed.

State schema1 is described in `online_state.schema.json`. Canonical JSON carries
a checksum; runtime validation checks policy/configuration fingerprint, finite
numbers, statistics, horizons, coefficient bounds and unique pending IDs. Unknown
schemas and old rejection policies are never guessed/migrated. A future migration
must be an explicit versioned deterministic converter with fixture tests; otherwise
run bootstrap. Activation mode is excluded from the numerical fingerprint.

Local files use an exclusive writer lock and same-directory temp file, fsync and
atomic replacement; `.previous` is a last-good backup. A lock left by a killed
local process must be removed only after confirming that process is gone. Locks
are not committed to GitHub. A missing/unrecoverable checkpoint does not trigger
automatic bootstrap.

The canonical remote is the **data-only orphan `model-state` branch**, not main
or expiring caches/artifacts. It contains only `model/phase3/online_state.json`.
Routine restore pins the branch head; a corrupt current file can recover a
compatible checkpoint from up to six recent file commits. Save uses GitHub's
contents API with the originally read blob SHA. A conflicting writer causes failure,
not overwrite or retry with stale learned data. The next workflow restores the
newest state and retries from its checkpoint. Identical bytes do not create commits.
The remote helper refuses to write any branch other than `model-state`.

## Workflow and approval boundary

The existing triggers, concurrency group, `cancel-in-progress:false`, Pages jobs
and environment remain. The build job has `contents:write` only to support the
future isolated state writer; Pages/deploy permissions otherwise remain as before.
Mode is off, so no state network writes occur. Restore/update/save are all guarded
and nonfatal. Only successfully persisted state can be exported; failure uses the
fixed M3 batch exporter with the new clipping policy. No new environment,
approval rule, database, PAT or workflow dispatch is introduced. Branch filtering
also prevents state commits from triggering the main collector/model push paths.
Each restore/update/save step has a one-minute timeout within the existing
five-minute build budget, so an optional learning network stall can fall back.

Do not interpret canceled queued runs as lost training: catch-up ingestion consumes
every new tail row on the next successful run. Initial bootstrap is never executed
inside the routine workflow. A previous pending Pages/environment problem cannot
be diagnosed from YAML alone; this change does not alter deployment protection.

## Local commands (repository root)

Python3.12+, standard library only; Windows may need `python -m pip install tzdata`.
All examples below are local; they do not activate a workflow or publish state.

```sh
python -m unittest discover -s model/phase3/tests -v
node --test model/phase3/tests/state.test.mjs
python model/phase3/bootstrap_online.py --state .online-artifacts/bootstrap.json
python model/phase3/update_online.py --state .online-artifacts/bootstrap.json --offline
python model/phase3/verify_phase_b.py --state .online-artifacts/bootstrap.json --out .online-artifacts/verification.json
```

`bootstrap_online.py` refuses an existing output unless explicitly given
`--replace`. `update_online.py` without `--offline` does nothing while mode is off.
Artifacts and checkpoints are gitignored. Keep a frozen CSV snapshot and record
source revision/hash for reproducibility.

## Phase C harness (not an approval to run/activate production)

```sh
# Past validation; rerun with --prior-strength, --half-life, --alpha-max.
python model/phase3/evaluate_online.py --start 2026-10-15 --end 2026-10-28 --out .online-artifacts/validation
# Only after fixing settings based on validation, inspect the later test once.
python model/phase3/evaluate_online.py --start 2026-10-29 --end 2026-11-11 --out .online-artifacts/test
```

Arms: old-policy legacy fixed M3; new-policy fixed-alpha M3; new-policy batch
baseline+online alpha; exact incremental baseline+same online alpha. The last two
should agree numerically. Their comparison isolates implementation efficiency;
learning benefit is evaluated against new-policy fixed alpha. `available` and
`common` cohorts separate changed coverage from paired predictive performance.

Outputs include per-origin predictions, raw/clipped matched labels, matching error,
MAE/RMSE/bias, full-zero and existing warning-cutoff precision/recall/F1, weekdays,
time-of-day bins, weekday06–10 mornings, update runtime, state size and parity error.
Undefined precision/recall are null, not invented zeros. Overlapping samples are
not independent events. `--stride` subsamples scored origins only; learning still
processes all origins. No tuning, promotion or production state writing is done.

## Phase D activation and rollback (future explicit approval)

After Phase C review, approval is required before changing mode to shadow and
initializing the state branch with the approved offline bootstrap. The command
`state_remote.py initialize --state ...` refuses mode off and never overwrites an
existing branch. It uses `GH_TOKEN` from the environment, never logs it. Normal
restore/save thereafter use the workflow's token. Do not execute initialization
during Phase B. Moving online alpha into public operational predictions requires
another reviewed change; mode shadow itself never does that.

Rollback: set mode off in a reviewed commit. The collector and CSV are untouched;
fixed M3 with clipping remains operational, and the state branch can remain for
audit without deletion. To roll back clipping too, explicitly revert the policy
change separately—never load a new-policy state into the old model. Recovery or
rollback does not require force push or Pages environment changes.
