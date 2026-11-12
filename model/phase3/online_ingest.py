"""Append-only CSV consumption, byte checkpointed and prefix authenticated.

The prefix is hashed (O(file bytes)) to detect arbitrary historical edits, but
only the new tail is CSV-parsed and incorporated. No full-history fitting.
"""
import csv
from datetime import datetime, timezone
import hashlib
import io
from pathlib import Path

from observations import parse_record, note_valid, note_invalid, canonical_csv_bytes, CHECKPOINT_ENCODING
from online_model import stamp
from online_state import StateError


def ingest(model, path, as_of=None):
    data = canonical_csv_bytes(Path(path).read_bytes())
    checkpoint = model.state['checkpoint']
    offset = checkpoint['offset']
    if offset > len(data):
        raise StateError('CSV was truncated; explicit repair/bootstrap required')
    if offset and hashlib.sha256(data[:offset]).hexdigest() != checkpoint['prefix_sha256']:
        raise StateError('Previously consumed CSV changed; explicit repair/bootstrap required')
    end = data.rfind(b'\n')+1
    if end <= offset:
        return dict(new_rows=0, new_bytes=0)
    header_end = data.find(b'\n')+1
    header = next(csv.reader([data[:header_end].decode('utf-8-sig').strip()]))
    if not {'timestamp', 'free_spaces', 'total_spaces'}.issubset(header):
        raise StateError('Required CSV columns are missing')
    start = offset or header_end
    # A partially written final line remains unconsumed until a newline arrives.
    stream = io.StringIO(data[start:end].decode('utf-8'))
    grouped = {}
    count = 0
    for raw in csv.DictReader(stream, fieldnames=header):
        count += 1
        model.state['quality']['input_rows'] += 1
        try:
            row = parse_record(raw, model.tz)
        except ValueError as exc:
            note_invalid(model.state['quality'], str(exc))
            try:
                t = datetime.fromisoformat(raw['timestamp'])
                if t.tzinfo is not None:
                    key = stamp(t)
                    model.state['latest_invalid'] = max(key, model.state['latest_invalid'] or key)
            except (ValueError, TypeError, KeyError):
                pass
            continue
        note_valid(model.state['quality'], row)
        if as_of is not None and row['time'].timestamp() > as_of.timestamp()+model.cfg['future_clock_tolerance_seconds']:
            raise StateError('Future sensor timestamp; checkpoint left unchanged for retry')
        key = stamp(row['time'])
        group = grouped.setdefault(key, dict(timestamp=key, free=[], capacity=[]))
        group['free'].append(row['free'])
        group['capacity'].append(row['capacity'])
    for key in sorted(grouped):
        quarantined = model.state['diagnostics']['quarantined_closed_day']
        model.observe_group(grouped[key])
        if model.state['diagnostics']['quarantined_closed_day'] > quarantined:
            raise StateError('Closed-day late observation quarantined; explicit bootstrap required; transaction not saved')
    model.state['checkpoint'] = dict(offset=end, prefix_sha256=hashlib.sha256(data[:end]).hexdigest(),encoding=CHECKPOINT_ENCODING)
    return dict(new_rows=count, new_bytes=end-offset)
