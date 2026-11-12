"""Exact completed-day sufficient statistics and delayed online M3 correction.

No dependencies beyond Python's standard library. State contains one open local
day, a three-hour observation ring, fixed-size statistics, and pending forecasts.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
import statistics
from zoneinfo import ZoneInfo

from calendar_features import classify
from observations import POLICY, CHECKPOINT_ENCODING, normalize_free, quality
from export_prediction import instant, shift, recent_history_ok

SCHEMA = 1


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(cfg, options, calendar):
    # Activation is independent of learned parameters: enabling shadow cannot
    # invalidate a validated checkpoint. Every numerical policy is fingerprinted.
    numerical = {k: v for k, v in options.items() if k not in {"mode", "state_branch", "parameter_status"}}
    return hashlib.sha256(canonical([cfg, numerical, calendar, POLICY]).encode()).hexdigest()


def initial_state(cfg, options, calendar):
    if cfg["capacity"] != 1300 or not (0 <= options["alpha_min"] <= options["alpha_max"]):
        raise ValueError("Invalid capacity or coefficient bounds")
    if options['ingestion_encoding'] != CHECKPOINT_ENCODING:
        raise ValueError('Unsupported checkpoint encoding')
    for key in ['outcome_tolerance_seconds','optimizer_half_life_days','prior_strength','prior_feature_scale_spaces','minimum_training_pairs']:
        if not math.isfinite(options[key]) or options[key] <= 0:
            raise ValueError('Invalid positive optimizer setting: '+key)
    if any(not options['alpha_min'] <= h['alpha'] <= options['alpha_max'] for h in cfg['horizons']):
        raise ValueError('Bounds must include validated fallback alpha values')
    return dict(schema_version=SCHEMA, normalization_policy=POLICY,
                model_version=options["model_version"], fingerprint=fingerprint(cfg, options, calendar),
                baseline=dict(reference=None, buckets={}, date_counts=[0, 0]),
                active_date=None, active={}, recent=[], last_event=None, latest_invalid=None,
                pending=[], last_forecasts=[], optimizers={str(h["minutes"]): dict(
                    alpha=h["alpha"], sxx=0.0, sxz=0.0, pairs=0, expired=0,
                    last_update=None, clipped_updates=0) for h in cfg["horizons"]},
                quality=quality(), diagnostics=dict(duplicates=0, late_open_day=0,
                    quarantined_closed_day=0, skipped_origins=0, matured_pairs=0),
                checkpoint=dict(offset=0, prefix_sha256=None, encoding=CHECKPOINT_ENCODING), last_successful_update=None)


def stamp(t):
    return instant(t).isoformat()


def from_stamp(t, tz):
    return datetime.fromisoformat(t).astimezone(tz)


class OnlineModel:
    def __init__(self, cfg, options, calendar, state=None):
        self.cfg, self.options, self.calendar = cfg, options, calendar
        self.tz = ZoneInfo(cfg["timezone"])
        self.state = state if state is not None else initial_state(cfg, options, calendar)
        if self.state["fingerprint"] != fingerprint(cfg, options, calendar):
            raise ValueError("State policy/configuration mismatch; explicit bootstrap required")

    def _finalize_day(self):
        from observations import summarize
        state = self.state
        rows = [summarize(v, self.tz) for v in state["active"].values()]
        if not rows:
            return
        date = rows[0]["time"].date()
        category = int(classify(date, self.calendar)["gsu_no_class_confirmed"] is True)
        base = state["baseline"]
        reference = max(instant(r["time"]).timestamp() for r in rows)
        half = self.cfg["baseline_half_life_days"] * 86400
        if base["reference"] is not None:
            decay = 2 ** (-(reference - base["reference"]) / half)
            for values in base["buckets"].values():
                values[0] *= decay
                values[1] *= decay
        base["reference"] = reference
        base["date_counts"][category] += 1
        # Every observation contributes to the 31 minute-centred windows that
        # include it, including the same-date circular window around midnight.
        windows = {}
        for row in rows:
            m = row["time"].hour * 60 + row["time"].minute
            value, seconds = normalize_free(row["free"]), instant(row["time"]).timestamp()
            for offset in range(-self.cfg["baseline_window_minutes"], self.cfg["baseline_window_minutes"] + 1):
                slot = (m + offset) % 1440
                part = windows.setdefault(slot, [0.0, 0, -math.inf])
                part[0] += value
                part[1] += 1
                part[2] = max(part[2], seconds)
        def add(key, mean, latest):
            weight = 2 ** ((latest - reference) / half)
            bucket = base["buckets"].setdefault(key, [0.0, 0.0])
            bucket[0] += weight * mean
            bucket[1] += weight
        for minute, (total, count, latest) in windows.items():
            for weekday in [str(date.weekday()), "all"]:
                add(f"{category}/{weekday}/{minute}", total / count, latest)
        add(f"{category}/all/global", statistics.fmean(normalize_free(r["free"]) for r in rows), reference)
        state["active"] = {}

    def baseline(self, target, calendar_shadow=False):
        """Only supports the current replay origin's completed-day history."""
        if self.state["active_date"] is None:
            raise ValueError("No completed-day support")
        if self.state['last_event'] is not None and instant(target) < datetime.fromisoformat(self.state['last_event']):
            raise ValueError('Past query requires an earlier replay checkpoint')
        # A current state must never be used to forecast an earlier origin.
        base = self.state["baseline"]
        categories, policy = [0, 1], "unchanged"
        if calendar_shadow:
            kind = classify(target.date(), self.calendar)
            if kind["calendar_known"] and kind["day_type"] != "weekend":
                if kind["gsu_no_class_confirmed"]:
                    if base["date_counts"][1] >= self.calendar["minimum_exception_dates"]:
                        categories, policy = [1], "exception_history"
                    else:
                        policy = "fallback_insufficient_exception_dates"
                elif base["date_counts"][0]:
                    categories, policy = [0], "exclude_gsu_exceptions"
                else:
                    policy = "fallback_empty"
        minute = target.hour * 60 + target.minute
        for weekday, slot in [(str(target.weekday()), str(minute)), ("all", str(minute)), ("all", "global")]:
            pieces = [base["buckets"].get(f"{c}/{weekday}/{slot}", [0, 0]) for c in categories]
            weight = sum(p[1] for p in pieces)
            if weight > 0:
                return sum(p[0] for p in pieces) / weight, policy
        raise ValueError("No historical baseline support")

    def _recent(self):
        return [dict(time=from_stamp(r["timestamp"], self.tz), free=normalize_free(r["free"]),
                     capacity=r["capacity"]) for r in self.state["recent"]]

    def _mature(self, now):
        pending = []
        tolerance = self.options["outcome_tolerance_seconds"]
        recent = self._recent()
        for forecast in self.state["pending"]:
            target = datetime.fromisoformat(forecast["target"])
            if instant(now).timestamp() < target.timestamp() + tolerance:
                pending.append(forecast)
                continue
            candidates = [r for r in recent if abs(instant(r["time"]).timestamp() - target.timestamp()) <= tolerance]
            optimizer = self.state["optimizers"][str(forecast["horizon"])]
            if not candidates:
                optimizer["expired"] += 1
                continue
            outcome = min(candidates, key=lambda r: (abs(instant(r["time"]).timestamp()-target.timestamp()), instant(r["time"])))
            x = forecast["x"] / 1300
            z = (outcome["free"] - forecast["baseline_target"]) / 1300
            if optimizer["last_update"] is not None:
                dt = instant(now).timestamp() - datetime.fromisoformat(optimizer["last_update"]).timestamp()
                decay = 2 ** (-dt / (self.options["optimizer_half_life_days"] * 86400))
                optimizer["sxx"] *= decay
                optimizer["sxz"] *= decay
            optimizer["sxx"] += x*x
            optimizer["sxz"] += x*z
            optimizer["pairs"] += 1
            optimizer["last_update"] = stamp(now)
            prior = self.options["prior_strength"] * (self.options["prior_feature_scale_spaces"]/1300)**2
            initial = next(h["alpha"] for h in self.cfg["horizons"] if h["minutes"] == forecast["horizon"])
            candidate = (prior * initial + optimizer["sxz"]) / max(1e-12, prior + optimizer["sxx"])
            bounded = max(self.options["alpha_min"], min(self.options["alpha_max"], candidate))
            optimizer["clipped_updates"] += int(candidate != bounded)
            optimizer["alpha"] = bounded if optimizer["pairs"] >= self.options["minimum_training_pairs"] else initial
            self.state["diagnostics"]["matured_pairs"] += 1
        self.state["pending"] = pending

    def forecasts(self, origin):
        if stamp(origin) != self.state["last_event"] or origin.date().isoformat() != self.state["active_date"]:
            raise ValueError("Historical/future origin requires a separate chronological replay")
        if sum(self.state["baseline"]["date_counts"]) < self.cfg["minimum_history_days"]:
            return []
        if instant(origin) < instant(datetime.fromisoformat(self.cfg["parameters_available_from"])):
            return []
        if not recent_history_ok(self._recent(), origin, self.cfg):
            return []
        current = next(r for r in self.state["recent"] if r["timestamp"] == stamp(origin))
        b_now, _ = self.baseline(origin)
        x = normalize_free(current["free"]) - b_now
        output = []
        for h in self.cfg["horizons"]:
            target = shift(origin, h["minutes"]*60)
            b_target, _ = self.baseline(target)
            alpha = self.state["optimizers"][str(h["minutes"])]["alpha"]
            output.append(dict(id=f"{self.options['model_version']}:{stamp(origin)}:{h['minutes']}",
                               origin=stamp(origin), target=stamp(target), horizon=h["minutes"],
                               x=x, baseline_target=b_target, alpha=alpha,
                               prediction=normalize_free(b_target + alpha*x)))
        return output

    def observe_group(self, group):
        """Process a structurally valid, same-UTC-instant group of raw values.

        Revisions to an open day update tomorrow's baseline. Forecast features
        already issued and sealed optimizer pairs are intentionally immutable.
        """
        from observations import summarize
        row = summarize(group, self.tz)
        now, state = row["time"], self.state
        day, key = now.date().isoformat(), stamp(now)
        if state["active_date"] is not None and day < state["active_date"]:
            state["diagnostics"]["quarantined_closed_day"] += len(group["free"])
            return
        advancing = state["last_event"] is None or key > state["last_event"]
        if state["active_date"] is not None and day > state["active_date"]:
            self._finalize_day()
        state["active_date"] = day
        if key in state["active"]:
            stored = state["active"][key]
            stored["free"].extend(group["free"])
            stored["capacity"].extend(group["capacity"])
            state["diagnostics"]["duplicates"] += len(group["free"])
            row = summarize(stored, self.tz)
        else:
            state["active"][key] = group
            state["diagnostics"]["duplicates"] += len(group["free"])-1
        if not advancing:
            state["diagnostics"]["late_open_day"] += len(group["free"])
        watermark = now if advancing else from_stamp(state["last_event"], self.tz)
        cutoff = instant(watermark).timestamp() - 3*3600
        state["recent"] = [r for r in state["recent"] if r["timestamp"] != key]
        if instant(now).timestamp() >= cutoff:
            state["recent"].append(dict(timestamp=key, free=row["free"], capacity=row["capacity"]))
            state["recent"].sort(key=lambda r: r["timestamp"])
        if not advancing:
            return
        state["last_event"] = key
        # Settle retained candidates before pruning after a long collection gap.
        self._mature(now)
        state['recent'] = [r for r in state['recent'] if datetime.fromisoformat(r['timestamp']).timestamp() >= cutoff]
        forecasts = self.forecasts(now)
        state["last_forecasts"] = forecasts
        state["pending"].extend(forecasts)
        if not forecasts:
            state["diagnostics"]["skipped_origins"] += 1
        state["last_successful_update"] = key
