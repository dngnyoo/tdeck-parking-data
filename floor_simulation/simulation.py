"""Assumption-conditional reconstruction; no fitted/calibrated floor model."""
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.special import ndtr, ndtri

ROOT = Path(__file__).resolve().parent

def read_data(path, config, policy="exclude"):
    raw = pd.read_csv(path)
    df = raw.copy()
    df["time"] = pd.to_datetime(df.timestamp, utc=True).dt.tz_convert("America/New_York")
    df = df.sort_values("time", kind="stable").reset_index(drop=True)
    df["duplicate"] = df.time.duplicated(keep=False)
    df["dt_min"] = df.time.diff().dt.total_seconds() / 60
    df["raw_N"] = config["capacity"] - df.free_spaces
    df["impossible"] = ~df.free_spaces.between(0, config["capacity"])
    df["gap"] = df.dt_min > config["gap_minutes"]
    df["jump"] = (df.raw_N.diff().abs() > config["jump_threshold"]) & (df.dt_min <= config["jump_window_minutes"])
    df["valid"] = ~df.duplicate & (~df.impossible if policy == "exclude" else True)
    df["N"] = df.raw_N.clip(0, config["capacity"]).astype(int)
    df["reset"] = df.gap | df.jump | ~df.valid.shift(1, fill_value=False)
    df.loc[0, "reset"] = True
    smooth, velocity, acceleration = [], [], []
    prev_s = prev_v = 0.
    for row in df.itertuples():
        if row.reset or not row.valid:
            s, v, a = float(row.N), 0., 0.
        else:
            dt = float(row.dt_min)
            s = prev_s + (-np.expm1(-dt / config["smoothing_minutes"])) * (row.N - prev_s)
            v = (s - prev_s) / dt
            a = (v - prev_v) / dt
        smooth.append(s); velocity.append(v); acceleration.append(a)
        prev_s, prev_v = s, v
    df["smoothed_N"], df["velocity"], df["acceleration"] = smooth, velocity, acceleration
    return df

def intensity(row, model, config, scale="medium"):
    h = row.time.hour + row.time.minute / 60
    if model == "A":
        rate = (0.1 if h < 6 or h >= 22 else 0.5 if h < 8 else 1.8 if h < 12 else 1.2 if h < 17 else 0.6)
        if row.time.weekday() >= 5: rate *= .3
        return rate * config["turnover_scales"][scale]
    return config["derivative_a_vehicles_per_minute"] + config["derivative_b"] * abs(row.velocity) + (config["derivative_c_minutes"] * abs(row.acceleration) if model == "C" else 0)

def duration(rng, shape, config, name, ages=None):
    p = config["duration"][name]
    sigma = np.sqrt(np.log1p(p["cv"] ** 2))
    mu = np.log(p["mean_minutes"]) - sigma ** 2 / 2
    if ages is None: return rng.lognormal(mu, sigma, shape)
    # Conditional duration given survival to current age; stable survival tail.
    lower = ndtr((np.log(np.maximum(ages, 1e-12)) - mu) / sigma)
    u = lower + (1 - lower) * rng.random(shape)
    return np.exp(mu + sigma * ndtri(np.clip(u, 1e-12, 1-1e-12)))

def run_simulation(df, config, scenario, reps, seed, snapshot_times=()):
    caps = np.array(config["layouts"][scenario.get("layout", "A_equal8")], dtype=int)
    assert (caps > 0).all() and caps.sum() == config["capacity"]
    rng = np.random.default_rng(seed)
    slots = np.repeat(np.arange(len(caps)), caps)
    beta = config["preference"][scenario.get("preference", "moderate")]
    weights = np.exp(-beta * slots)
    occupied = np.zeros((reps, caps.sum()), bool)
    deadlines = np.full(occupied.shape, np.inf)
    arrivals = np.full(occupied.shape, np.nan)
    ids = np.arange(reps)[:, None]
    records, flow_records, snapshots = [], [], {}
    metrics = dict(intervals=0, initializations=0, arrivals=0, departures=0, early_departures=0, delayed_due=0, clipped_turnover=0, conservation_checks=0)
    prev_N = None
    def assign(count, now, initial=False):
        # Exponential race: weighted sampling without replacement over vacant spaces.
        k = int(np.max(count))
        if not k: return
        use_weights = np.ones_like(weights) if initial and scenario.get("initial_preference") == "uniform" else weights
        score = -np.log(np.maximum(rng.random(occupied.shape), 1e-15)) / use_weights
        score[occupied] = np.inf
        chosen = np.argpartition(score, k-1, axis=1)[:, :k]
        order = np.argsort(np.take_along_axis(score, chosen, axis=1), axis=1)
        chosen = np.take_along_axis(chosen, order, axis=1)
        mask = np.arange(k)[None, :] < count[:, None]
        rr = np.broadcast_to(ids, chosen.shape)[mask]; cc = chosen[mask]
        ages = rng.uniform(0, config["initial_age_hours"][scenario.get("age", "mixed")] * 60, len(rr)) if initial else np.zeros(len(rr))
        durations = duration(rng, len(rr), config, scenario.get("duration", "standard"), ages if initial else None)
        occupied[rr, cc] = True
        arrivals[rr, cc] = now - ages
        deadlines[rr, cc] = now - ages + durations
    for row in df.itertuples():
        if not row.valid:
            prev_N = None
            continue
        now = row.time.timestamp() / 60
        early = clipped = due_remaining = 0
        if prev_N is None or row.reset:
            occupied[:] = False; deadlines[:] = np.inf; arrivals[:] = np.nan
            assign(np.full(reps, row.N), now, True)
            incoming = outgoing = np.zeros(reps, int)
            metrics["initializations"] += 1
        else:
            delta = row.N - prev_N
            if scenario["model"] == "D":
                due = (deadlines <= now).sum(axis=1)
                outgoing = np.maximum(due, max(0, -delta))
            else:
                proposed = rng.poisson(intensity(row, scenario["model"], config, scenario.get("scale", "medium")) * row.dt_min, reps)
                bound = prev_N - max(0, -delta)
                turnover = np.minimum(proposed, bound)
                clipped = int((proposed - turnover).sum())
                outgoing = max(0, -delta) + turnover
            incoming = delta + outgoing
            k = int(outgoing.max())
            if k:
                chosen = np.argpartition(deadlines, k-1, axis=1)[:, :k]
                order = np.argsort(np.take_along_axis(deadlines, chosen, axis=1), axis=1)
                chosen = np.take_along_axis(chosen, order, axis=1)
                mask = np.arange(k)[None, :] < outgoing[:, None]
                rr = np.broadcast_to(ids, chosen.shape)[mask]; cc = chosen[mask]
                early = int((deadlines[rr, cc] > now).sum())
                occupied[rr, cc] = False; deadlines[rr, cc] = np.inf; arrivals[rr, cc] = np.nan
            due_remaining = int((deadlines <= now).sum())
            assign(incoming, now)
            metrics["intervals"] += 1
        assert (occupied.sum(axis=1) == row.N).all(), "aggregate conservation failed"
        floor_N = np.add.reduceat(occupied.astype(np.int16), np.r_[0, np.cumsum(caps)[:-1]], axis=1)
        free = caps - floor_N
        assert (free >= 0).all() and (free <= caps).all()
        assert (free.sum(axis=1) == config["capacity"] - row.N).all()
        metrics["conservation_checks"] += reps
        for key, value in [("arrivals", incoming.sum()), ("departures", outgoing.sum()), ("early_departures", early), ("delayed_due", due_remaining), ("clipped_turnover", clipped)]: metrics[key] += int(value)
        q = np.quantile(free, [.025, .975], axis=0)
        prob = (free >= 1).mean(axis=0)
        for f in range(len(caps)):
            records.append(dict(timestamp=row.timestamp, floor=f+1, observed_free=int(row.free_spaces), feasible_free=int(config["capacity"]-row.N), capacity=int(caps[f]), mean_free=float(free[:,f].mean()), sd_free=float(free[:,f].std()), q025=float(q[0,f]), q975=float(q[1,f]), probability_space=float(prob[f]), probability_mcse=float(np.sqrt(prob[f]*(1-prob[f])/reps)), reset=bool(prev_N is None or row.reset)))
        flow_records.append(dict(timestamp=row.timestamp, incoming_mean=float(incoming.mean()), outgoing_mean=float(outgoing.mean()), early_departures_mean=early/reps, delayed_due_mean=due_remaining/reps, clipped_turnover_mean=clipped/reps))
        if row.timestamp in snapshot_times: snapshots[row.timestamp] = free.copy()
        prev_N = row.N
    return pd.DataFrame(records), pd.DataFrame(flow_records), metrics, snapshots

def audit(df, path):
    intervals = df.dt_min.dropna()*60
    return dict(sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest(), rows=len(df), start=df.timestamp.iloc[0], end=df.timestamp.iloc[-1], negative=int((df.free_spaces<0).sum()), zeros=int((df.free_spaces==0).sum()), impossible=int(df.impossible.sum()), duplicate_rows=int(df.duplicate.sum()), min_free=int(df.free_spaces.min()), max_free=int(df.free_spaces.max()), gaps_gt15min=int(df.gap.sum()), intervals_gt10min=int((intervals>600).sum()), median_interval_seconds=float(intervals.median()), max_gap_seconds=float(intervals.max()), jumps=int(df.jump.sum()), invalid_rows=int((~df.valid).sum()), nominal_capacities=[int(x) for x in df.total_spaces.unique()])
