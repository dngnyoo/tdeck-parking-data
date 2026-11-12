"""Validated, checksummed state transactions; no automatic historical rebuild."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

from observations import POLICY, CHECKPOINT_ENCODING
from online_model import SCHEMA, canonical, fingerprint


class StateError(ValueError):
    pass


def validate(state, cfg, options, calendar):
    try:
        if state['schema_version'] != SCHEMA or state['normalization_policy'] != POLICY:
            raise StateError('Unsupported schema/normalization policy; explicit migration or bootstrap required')
        if state['fingerprint'] != fingerprint(cfg, options, calendar):
            raise StateError('Configuration/calendar changed; explicit bootstrap required')
        def finite_tree(value):
            if isinstance(value, float) and not math.isfinite(value):
                raise StateError('Nonfinite state')
            if isinstance(value, dict):
                for item in value.values(): finite_tree(item)
            if isinstance(value, list):
                for item in value: finite_tree(item)
        finite_tree(state)
        if state['checkpoint']['offset'] < 0 or state['checkpoint']['encoding'] != CHECKPOINT_ENCODING:
            raise StateError('Invalid ingestion checkpoint')
        for values in state['baseline']['buckets'].values():
            if len(values) != 2 or values[1] < 0 or values[0] < -1e-9 or values[0] > 1300*values[1] + 1e-6:
                raise StateError('Invalid baseline statistics')
        expected = {str(h['minutes']) for h in cfg['horizons']}
        if set(state['optimizers']) != expected:
            raise StateError('Unexpected horizons')
        for item in state['optimizers'].values():
            if not options['alpha_min'] <= item['alpha'] <= options['alpha_max'] or item['sxx'] < 0 or item['pairs'] < 0:
                raise StateError('Invalid optimizer')
        ids = [p['id'] for p in state['pending']]
        if len(set(ids)) != len(ids):
            raise StateError('Duplicate pending forecasts')
        if any(p['horizon'] not in {h['minutes'] for h in cfg['horizons']} or not 0 <= p['prediction'] <= 1300 for p in state['pending']):
            raise StateError('Invalid pending forecast')
        return state
    except (KeyError, TypeError, OverflowError) as exc:
        raise StateError('Incomplete or invalid state structure') from exc


def seal(state):
    result = deepcopy(state)
    result.pop('checksum', None)
    result['checksum'] = hashlib.sha256(canonical(result).encode()).hexdigest()
    return result


def decode(data, cfg, options, calendar):
    try:
        result = json.loads(data)
        checksum = result.pop('checksum')
        if hashlib.sha256(canonical(result).encode()).hexdigest() != checksum:
            raise StateError('Checksum mismatch')
        return validate(result, cfg, options, calendar)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise StateError(str(exc)) from exc


def load_state(path, cfg, options, calendar, recover=True):
    path = Path(path)
    failures = []
    for candidate in [path, path.with_suffix(path.suffix+'.previous')] if recover else [path]:
        try:
            return decode(candidate.read_text(encoding='utf-8'), cfg, options, calendar), candidate != path
        except (OSError, StateError) as exc:
            failures.append(str(exc))
    raise StateError('No valid checkpoint: '+'; '.join(failures))


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name+'.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def save_state(path, state, cfg, options, calendar):
    validate(state, cfg, options, calendar)
    path = Path(path)
    if path.exists():
        try:
            old = path.read_text(encoding='utf-8')
            decode(old, cfg, options, calendar)
            atomic_write(path.with_suffix(path.suffix+'.previous'), old)
        except (OSError, StateError):
            pass  # Never replace a known-good backup with corrupt bytes.
    atomic_write(path, canonical(seal(state))+'\n')


@contextmanager
def state_lock(path):
    """Fail closed on another writer; never steal another process's lock."""
    lock = Path(str(path)+'.lock')
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise StateError('State writer lock is held') from exc
    try:
        os.write(descriptor, str(os.getpid()).encode())
        os.close(descriptor)
        yield
    finally:
        lock.unlink(missing_ok=True)
