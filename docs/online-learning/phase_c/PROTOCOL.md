# Phase C protocol — fixed before inspecting final-test scores

Scope: offline only. `online_config.json`, production coefficients, collector,
raw `data/parking.csv`, website, deployment workflow and Pages settings stay as-is.

* Freeze the latest main CSV in an ignored local artifact; record its commit and
  SHA256. Validation receives a separate source ending2026-10-28. Final evaluation
  ends2026-11-11; no later labels can mature earlier test forecasts.
* Warm-up starts with the earliest observation. Fixed M3 coefficients cannot be
  used before their configured2026-09-24 availability. All forecasts use completed
  local days and the shared unconditional finite clipping policy.
* Select one shared hyperparameter configuration on validation origins/targets
  2026-10-15–28: forgetting half-life7/14/28 days × prior strength50/200/800 ×
  alpha upper bound1.5/2/3 (27 candidates). Other settings remain Phase B defaults.
* Primary selection objective: equal-weight mean of the four horizon MAEs on all
  matched normalized validation pairs. Ties within1e-9 choose the configuration
  closest to Phase B defaults, then lexicographic half-life/prior/bound. No
  configuration is selected using final-test, clean-only or warning scores.
* Save selection, source/feature/protocol hashes, candidate table and parameters
  before final-test scoring. Test2026-10-29–11-11 once with the frozen selection.
  Online parameters may continue to learn from already-matured test outcomes;
  hyperparameters remain frozen. This is prequential walk-forward evaluation.
* Primary comparison: new-policy fixed-alpha M3 vs new-policy online-alpha M3 on
  identical origins and matched outcomes. Secondary: former-policy fixed M3 on
  its available cohort and the common cohort. Exact incremental vs batch
  baseline equivalence is an implementation check, not another accuracy gain.
* Report every horizon's n, MAE, RMSE, bias(predicted-minus-actual), exact-full
  precision/recall/F1(prediction<=0), and warning precision/recall/F1(existing
  cutoffs25/25/50/100 against actual<=0). Undefined ratios are null.
* Cohorts: all pairs; clean pairs where both raw origin and raw matched outcome
  are within[0,1300]; clipping-involved pairs otherwise. Clean-pair results still
  use the same models trained on all normalized history; they do not imply the
  historical training series contained no clipping. Report origin/target clipping
  separately and weekday/time-of-day breakdowns, especially weekday06–10.
* Use paired day-block bootstrap with fixed seed20261112 and2000 resamples to
  describe uncertainty of test MAE differences. Days, not five-minute rows, are
  resampled; overlapping horizons are not independent observations. With only14
  test days and few full-capacity episodes, do not claim broad generalization.
* Record actual runtime/state size and stress checkpoint restart, missed Actions
  runs/catch-up, corrupt checkpoint recovery and unrecoverable-state fixed-M3
  fallback. Compare resulting learned state with uninterrupted replay.
* Candidate sweeps may reuse causally generated fixed baseline/origin features
  and delayed label events: they do not depend on alpha. Verify the cached replay
  against the actual Phase B optimizer/forecast implementation. No target baseline
  may be recomputed when its label arrives.
* No automated or implicit promotion. A recommendation for shadow operation is a
  report conclusion only; activation still requires separate explicit approval.
