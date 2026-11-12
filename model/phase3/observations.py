"""One normalization policy for batch, bootstrap, incremental learning and labels."""
import csv
from datetime import datetime, timezone
import math
from pathlib import Path
import statistics

CAPACITY = 1300
POLICY = "finite_clip_1300_v1"
CHECKPOINT_ENCODING = 'utf8_lf_v1'


def canonical_csv_bytes(data):
    """Checkpoint representation only; never write this over the source CSV.

    Git checkouts can use CRLF on Windows and LF on Linux. Ignore that benign
    difference (and a leading UTF-8 BOM), while authenticating all CSV contents.
    """
    return data.removeprefix(b'\xef\xbb\xbf').replace(b'\r\n',b'\n')


def normalize_free(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("nonfinite_free")
    return min(float(CAPACITY), max(0.0, value))


def parse_record(row, tz):
    """Validate before grouping duplicates; reported positive capacity is metadata."""
    try:
        t = datetime.fromisoformat(row["timestamp"])
    except (TypeError, KeyError, ValueError) as exc:
        raise ValueError("invalid_timestamp") from exc
    if t.tzinfo is None or t.utcoffset() is None:
        raise ValueError("missing_timezone")
    try:
        free = float(row["free_spaces"])
        capacity = float(row["total_spaces"])
    except (TypeError, KeyError, ValueError) as exc:
        raise ValueError("invalid_number") from exc
    normalize_free(free)
    if not math.isfinite(capacity) or capacity <= 0:
        raise ValueError("invalid_reported_capacity")
    return dict(time=t.astimezone(tz), free=free, capacity=capacity)


def quality():
    return dict(normalization_policy=POLICY, input_rows=0, invalid_rows=0,
                clipped_to_zero=0, clipped_to_capacity=0,
                reported_capacity_mismatch=0, invalid_reasons={})


def note_valid(q, row):
    q["clipped_to_zero"] += int(row["free"] < 0)
    q["clipped_to_capacity"] += int(row["free"] > CAPACITY)
    q["reported_capacity_mismatch"] += int(row["capacity"] != CAPACITY)


def note_invalid(q, reason):
    q["invalid_rows"] += 1
    reasons = q["invalid_reasons"]
    reasons[reason] = reasons.get(reason, 0) + 1


def summarize(values, tz):
    t = datetime.fromisoformat(values["timestamp"]).astimezone(tz)
    def median(items):
        ordered = sorted(items)
        middle = len(ordered)//2
        if len(ordered)%2:
            return ordered[middle]
        left, right = ordered[middle-1], ordered[middle]
        return left+(right-left)/2 if (left>=0)==(right>=0) else left/2+right/2
    return dict(time=t, free=median(values["free"]), capacity=median(values["capacity"]))


def read_csv(path, tz):
    grouped = {}
    q = quality()
    latest_invalid = None
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not {"timestamp", "free_spaces", "total_spaces"}.issubset(reader.fieldnames or []):
            raise ValueError("Required CSV columns are missing")
        for raw in reader:
            q["input_rows"] += 1
            try:
                row = parse_record(raw, tz)
            except ValueError as exc:
                note_invalid(q, str(exc))
                try:
                    t = datetime.fromisoformat(raw["timestamp"])
                    if t.tzinfo is not None:
                        t = t.astimezone(timezone.utc)
                        latest_invalid = max(latest_invalid, t) if latest_invalid else t
                except (ValueError, TypeError, KeyError):
                    pass
                continue
            note_valid(q, row)
            key = row["time"].astimezone(timezone.utc).isoformat()
            group = grouped.setdefault(key, dict(timestamp=key, free=[], capacity=[]))
            group["free"].append(row["free"])
            group["capacity"].append(row["capacity"])
    readings = [summarize(grouped[k], tz) for k in sorted(grouped)]
    q["duplicate_rows"] = sum(len(g["free"]) - 1 for g in grouped.values())
    q["clipped_rows"] = q["clipped_to_zero"] + q["clipped_to_capacity"]
    q["rejected_rows"] = q["invalid_rows"]
    return readings, q, latest_invalid
