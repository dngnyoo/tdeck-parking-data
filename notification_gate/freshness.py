"""Read-only T Deck Pages v3 checker. No model or notification side effects."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import urllib.request

CONFIG = Path(__file__).resolve().parents[1] / 'model/phase3/config.json'
SOURCE = 'https://dngnyoo.github.io/tdeck-parking-data/data/prediction.json'


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('missing_timestamp')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('timezone_required')
    return result.astimezone(timezone.utc)


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def check(payload, *, now=None, config=None):
    """Use local trusted config, never payload freshness_seconds as age or policy."""
    result = dict(status='invalid', reason='invalid_payload', age_seconds=None,
                  max_age_seconds=None, observed_at=None, checked_at=None,
                  allow_model=False, allow_notification=False, observation_id=None)
    try:
        cfg = config if config is not None else json.loads(CONFIG.read_text(encoding='utf-8'))
        limit = cfg['freshness_seconds']
        if not number(limit) or limit <= 0:
            raise ValueError('invalid_policy')
        result['max_age_seconds'] = limit
        clock = now if now is not None else datetime.now(timezone.utc)
        if clock.tzinfo is None or clock.utcoffset() is None:
            raise ValueError('invalid_clock')
        clock = clock.astimezone(timezone.utc)
        result['checked_at'] = clock.isoformat()
        if not isinstance(payload, dict) or payload.get('schema_version') != 3 or payload.get('parking') != 'T Deck':
            raise ValueError('invalid_schema')
        current = payload['current']
        observed = timestamp(current['timestamp'])
        if timestamp(payload['forecast_origin']) != observed:
            raise ValueError('conflicting_observation_times')
        generated = timestamp(payload['generated_at'])
        if generated < observed or generated > clock:
            raise ValueError('invalid_generation_time')
        free, capacity = current['free_spaces'], current['total_spaces']
        if not number(free) or not number(capacity) or capacity <= 0 or free > capacity:
            raise ValueError('invalid_counts')
        age = (clock - observed).total_seconds()
        result.update(age_seconds=age, observed_at=observed.isoformat())
        # Alert safety is deliberately stricter than the predictor's 120s tolerance.
        if age < 0:
            raise ValueError('future_observation')
        observation = dict(parking='T Deck', observed_at=observed.isoformat(),
                           free_spaces=float(free), total_spaces=float(capacity))
        result['observation_id'] = hashlib.sha256(
            json.dumps(observation, sort_keys=True, allow_nan=False).encode()).hexdigest()
        if age > limit:
            result.update(status='stale', reason='observation_expired')
        elif payload.get('status') != 'ok':
            result.update(status='unavailable', reason='source_not_ok')
        else:
            result.update(status='fresh', reason=None, allow_model=True, allow_notification=True)
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        # No parsing/config failure grants permission. Do not echo raw server data.
        result.update(status='invalid', reason='invalid_timestamp_payload_or_policy',
                      allow_model=False, allow_notification=False)
    return result


def fetch_check(url=SOURCE, *, config=None, opener=urllib.request.urlopen):
    try:
        request = urllib.request.Request(url, headers={'Cache-Control': 'no-cache'})
        with opener(request, timeout=20) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError('oversized_response')
        payload = json.loads(raw)
    except (OSError, ValueError, TimeoutError):
        return dict(status='unavailable', reason='fetch_or_json_failed', age_seconds=None,
                    max_age_seconds=None, observed_at=None,
                    checked_at=datetime.now(timezone.utc).isoformat(),
                    allow_model=False, allow_notification=False, observation_id=None), None
    return check(payload, config=config), payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', type=Path, help='Offline JSON fixture; never sends messages')
    args = parser.parse_args()
    if args.file:
        try:
            result = check(json.loads(args.file.read_text(encoding='utf-8')))
        except (OSError, ValueError):
            result = check(None)
    else:
        result, _ = fetch_check()
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result['status'] == 'fresh' else 2


if __name__ == '__main__':
    raise SystemExit(main())
