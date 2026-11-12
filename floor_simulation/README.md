# T Deck hypothetical floor reconstruction

Independent research only. Nothing here is connected to collection, prediction workflows, website, or GitHub Pages. All level labels/capacities are hypothetical. Probabilities are conditional on assumptions, not empirically calibrated parking probabilities.

## Reproduce

Python 3.12+; install the research dependencies and run from repository root:

```sh
python -m pip install -r floor_simulation/requirements.txt
python -m unittest discover -s floor_simulation -p 'test_*.py'
python floor_simulation/experiment.py
python floor_simulation/replay.py --model C --replications 100 --output floor_simulation/results/full_history
python floor_simulation/replay.py --model A --date 2026-09-27 --replications 1000 --output floor_simulation/results/weekend_A
python floor_simulation/replay.py --model C --date 2026-10-13 --policy clip --replications 250 --output floor_simulation/results/negative_clip
python floor_simulation/initial_stress.py
python floor_simulation/analyze.py
```

`experiment.py --quick --output /path/to/smoke` runs a small smoke experiment, not the reported research. `replay.py --help` exposes model, layout, duration, initial age, preference, seed, date, capacity policy and output controls. Add alternative layouts to `config.json`; every capacity must be a positive integer and sum to 1300. No fourth layout is asserted without new evidence.

The executed snapshot is repository commit `79886754e40a54a1bbb276c6bac0afbce16f7f6d`, CSV SHA256 recorded in `results/audit.json`. To reproduce exactly after the live collector adds data, use the CSV from that commit and pass its saved path with `--data`. Random seeds and dependencies are recorded; numeric/random results may differ across dependency versions. Run times are hardware dependent.

## Observations and assumptions

GSU's [official deck table](https://parking.gsu.edu/parking/parking-deck-addresses/) lists T Deck at 43 Auburn Avenue with **1431** spaces (checked 2026-10-13). CSV/API nominal capacity is **1300** throughout this snapshot. The 131-space difference is unresolved; this experiment never substitutes 1431. [Mapping information](https://www.latlong.net/poi/t-deck-484416) lists building levels **8**; this is not evidence of eight independently accessible parking levels. Ramp geometry, numbering, restrictions, and per-level capacities are unknown.

Raw free spaces are unchanged. `raw_N=1300-free_spaces` includes impossible values. Primary policy excludes out-of-range values and both occurrences of duplicated timestamps, restarts after excluded observations, gaps over 15 minutes, or occupancy jumps over 200 vehicles within 10 minutes. Flags are heuristic, not diagnoses of bad sensors. The optional `clip` policy explicitly reconciles impossible observations to a saturated/empty garage and preserves raw values; it is a sensitivity scenario only. Valid zero-free observations remain saturated.

All intervals use elapsed minutes. Incoming/outgoing counts obey `I=max(0,dN)+R`, `O=max(0,-dN)+R`; observed net change does not identify `R`. Departures happen before arrivals at each observation endpoint. Multiple within-interval cycles are omitted; turnover is capped to the previously occupied population, and clipping is counted. Arrivals are stamped at the endpoint, an approximation at roughly five-minute resolution.

Each hypothetical parking space stores an occupant, arrival time, and sampled departure deadline. Drivers select available *spaces* using exponential-race sampling without replacement, weights `exp(-beta*level_index)`. This is a per-space preference; a larger level has more candidates. Beta 0.15/0.65/1.5 gives weak/moderate/strong preference. Departures select the earliest sampled deadlines across all levels, never a lower-level priority.

Durations are lognormal: mean/CV 240min/0.55, 120min/0.65, 360min/0.55. Initial vehicle ages are uniform 0–1/4/8 hours. Initial total duration is sampled conditional on survival to that age, so residual time is nonnegative and age influences exits. Initial occupancy equals the first feasible observed total, assigned with the same preference or a separate uniform-initial sensitivity. Vehicles persist across midnight in full-history replay; selected independent-day experiments reinitialize at their first observation, never as an empty garage.

* A: weekday 00–06 &22–24:0.1, 06–08:0.5, 08–12:1.8, 12–17:1.2, 17–22:0.6 vehicles/min simultaneous turnover; weekends x0.3; low/medium/high x0.3/1/3.
* B: `lambda=0.15+0.35*abs(velocity)` in vehicles/min.
* C: B plus `3min*abs(acceleration)`; B is the second-derivative-zero ablation. Velocity vehicles/min, acceleration vehicles/min². Causal exponential occupancy smoothing has a 15-minute time constant and resets at discontinuities.
* D: all vehicles with elapsed sampled deadlines leave. If the observed occupancy drop requires more exits, additional earliest future deadlines are forced early. Arrivals reconcile the observed net change: `I=dN+O`. Deadlines are not otherwise overridden.

A/B/C specify exit counts from hypothetical turnover, then rank individual deadlines; this may force early exits or leave overdue vehicles. Early exit counts and overdue vehicle-observations are recorded. Overdue counts across intervals are **not** unique vehicles. D due departures are interval-censored to observation times; forced early exits are counted. These constraints make this a reconstruction model, not a validated parking-duration model.

## Design and outputs

Four primary models each use 1000 replicas on Sep 29 and Oct 13 (two independent observed days, chronological within each day). The first is a busy weekday with an exact morning/afternoon total match; the second includes negative readings and a large discontinuity. Eleven one-factor sensitivity scenarios use 250 replicas on Sep 29. Shared seeds reduce some variation but branching simulations do not remain perfectly coupled; small differences can be Monte Carlo noise. Full-history C uses 100 replicas, weekend A uses 1000, and negative-clipping C uses 250. This covers history without the cost of every layout × model × preference × duration × initial age × date at 1000 replicas. No parameter is fit to floor truth or tuned to make results look plausible.

The midnight initial state on Sep29 has only six vehicles, so most initial-state differences disappear by daytime. The informative expansion `initial_stress.py` starts at09:30 with800 observed vehicles and compares D with mixed/young/old ages and uniform initial slot assignment, each1000 replicas, through16:00. It reports differences both including initialization and after30 minutes. Its day/time selection and seed are explicit in the script; it does not select parameters based on desired outcomes.

`results/*_floors.csv`: time, raw/feasible total, hypothetical level/capacity, mean/SD/2.5th/97.5th percentile free spaces, probability of >=1 space, probability Monte Carlo SE, restart flag. Percentiles describe latent simulation spread, not a confidence interval for true availability. Probability MCSE at 1000 replicas is at most 1.58 percentage points; at250, 3.16 points. Zero/all successes still have sampling uncertainty (a point estimate of zero does not prove impossibility).

`*_flows.csv`: assumed incoming/outgoing means, forced early exits, delayed due vehicle-observations, clipped turnover. `experiment.json`/replay `metadata.json`: configuration, seeds, runtime, counts. `observations_audit.csv`: full unchanged measurements plus flags/causal derivatives. `matched_pair_results.csv` and NPZ: exact matched-total states. `scenario_comparison.csv`: common-time/level differences from C; seven/eight-level comparisons pair only L1–L7 and do not assert physical level equivalence. `summary.json`: research export explicitly labeled hypothetical, not a production prediction. Figures and [Korean report](REPORT_KO.md) explain actual executed findings.

Conservation/capacity assertions run for every replication at every saved valid observation. Unit tests additionally cover reproducibility, stochastic variance, negative handling, gaps/irregular intervals, causal derivatives, duplicate exclusion, and initial conditions. Matching aggregate occupancy is a consistency check, never validation of a floor estimate. Calendar/holidays, access restrictions, actual floor usage and driver choice are not observed or modeled. More aggregate observations alone cannot resolve these unknowns.
