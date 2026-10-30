"""Proposed native OpenClaw command payload. No LLM and no Telegram credentials.

Default mode is dry-run JSON. --emit is for the existing Cron announce runner;
it emits report text or NO_REPLY, NEVER sends Telegram itself.
"""
import argparse
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from .freshness import check, fetch_check, number, timestamp
from .gate import Gate


def report(payload, decision):
    observed = timestamp(decision['observed_at'])
    local = observed.astimezone(ZoneInfo('America/New_York'))
    current = payload['current']
    lines = ['T Deck 주차 현황 (뉴욕 현지)',
             f"현재 관측: {current['free_spaces']:g} / {current['total_spaces']:g}면 남음",
             f"관측 시각: {local:%Y-%m-%d %H:%M %Z}",
             f"관측 경과: {decision['age_seconds']:.0f}초 / 허용 한도: {decision['max_age_seconds']:g}초"]
    try:
        rows = [p for p in payload['predictions'] if p['horizon_minutes'] == 60]
        if len(rows) != 1:
            raise ValueError('ambiguous_forecast')
        prediction = rows[0]
        target = timestamp(prediction['target_time'])
        if (not number(prediction['free_spaces']) or not number(prediction['warning_cutoff'])
                or target != observed + timedelta(minutes=60)):
            raise ValueError('invalid_forecast')
        arrival = target.astimezone(ZoneInfo('America/New_York'))
        lines.append(f"60분 예측: {prediction['free_spaces']:g}면 (추정치), {arrival:%H:%M %Z}")
    except (KeyError, TypeError, ValueError, OverflowError):
        lines.append('60분 예측: 검증 가능한 예측 없음')
    lines.append('https://dngnyoo.github.io/tdeck-parking-data/')
    return '\n'.join(lines)


def execute(payload, database, event_scope, *, clock=None):
    output = []

    def handoff(text):
        output.append(text)
        # Receipt means local stdout handoff only, NOT confirmed Telegram delivery.
        return 'native-cron-output'

    result = Gate(database).run(payload, event_scope=event_scope,
        eligible=lambda p: True, model=lambda d: report(payload, d), send=handoff, clock=clock)
    return result, output[0] if output else 'NO_REPLY'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--emit', action='store_true')
    parser.add_argument('--database', type=Path)
    parser.add_argument('--event-scope')
    args = parser.parse_args()
    result, payload = fetch_check()
    if not args.emit:
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    if args.database is None or not args.event_scope:
        # Invalid deployment must never output a normal report.
        print('NO_REPLY')
        return 1
    if payload is None:
        print('NO_REPLY')
        return 1
    try:
        result, output = execute(payload, str(args.database), args.event_scope)
    except Exception:
        print('NO_REPLY')
        return 1
    print(output)
    return 1 if result['status'] in ('invalid', 'unavailable') or result['outcome'] == 'delivery_unknown' else 0


if __name__ == '__main__':
    raise SystemExit(main())
