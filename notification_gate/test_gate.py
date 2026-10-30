from copy import deepcopy
from datetime import datetime, timedelta, timezone
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock
from concurrent.futures import ThreadPoolExecutor

from notification_gate.freshness import check, fetch_check
from notification_gate.gate import Gate

NOW = datetime(2026, 10, 30, 16, tzinfo=timezone.utc)


def payload(age=300):
    observed = (NOW - timedelta(seconds=age)).isoformat()
    return dict(schema_version=3, parking='T Deck', status='ok', freshness_seconds=1200,
                generated_at=observed, forecast_origin=observed,
                current=dict(timestamp=observed, free_spaces=100, total_spaces=1300))


class FreshnessTests(unittest.TestCase):
    def test_boundaries(self):
        for age, status in [(300, 'fresh'), (1140, 'fresh'), (1200, 'fresh'),
                            (1200.001, 'stale'), (1260, 'stale'), (-1, 'invalid')]:
            with self.subTest(age=age):
                result = check(payload(age), now=NOW)
                self.assertEqual(result['status'], status)
                self.assertEqual(result['allow_model'], status == 'fresh')
                self.assertEqual(result['allow_notification'], status == 'fresh')

    def test_limit_is_not_age(self):
        p = payload(300)
        p['freshness_seconds'] = 999999
        result = check(p, now=NOW)
        self.assertEqual(result['age_seconds'], 300)
        self.assertEqual(result['max_age_seconds'], 1200)
        p = payload(1260)
        p['freshness_seconds'] = 999999
        self.assertEqual(check(p, now=NOW)['status'], 'stale')

    def test_bad_or_missing_timezone(self):
        for value in [None, '', 'nonsense', '2026-10-30T15:55:00',
                      '2026-10-30T15:55:00+99:00']:
            p = payload()
            p['current']['timestamp'] = value
            with self.subTest(value=value):
                self.assertEqual(check(p, now=NOW)['status'], 'invalid')

    def test_equivalent_timezone_and_dst(self):
        p = payload()
        p['current']['timestamp'] = '2026-10-30T11:55:00-04:00'
        self.assertEqual(check(p, now=NOW)['age_seconds'], 300)
        clock = datetime.fromisoformat('2026-11-01T01:05:00-05:00')
        p.update(forecast_origin='2026-11-01T01:55:00-04:00', generated_at=clock.isoformat())
        p['current']['timestamp'] = p['forecast_origin']
        self.assertEqual(check(p, now=clock)['age_seconds'], 600)

    def test_conflicting_times(self):
        p = payload()
        p['forecast_origin'] = NOW.isoformat()
        self.assertEqual(check(p, now=NOW)['status'], 'invalid')

    def test_unavailable_source(self):
        p = payload()
        p['status'] = 'unavailable'
        self.assertEqual(check(p, now=NOW)['status'], 'unavailable')

    def test_invalid_counts_and_config(self):
        for value in [True, float('nan'), float('inf'), '100', 1301]:
            p = payload()
            p['current']['free_spaces'] = value
            self.assertEqual(check(p, now=NOW)['status'], 'invalid')
        self.assertEqual(check(payload(), now=NOW, config={'freshness_seconds': True})['status'], 'invalid')

    def test_api_failure(self):
        result, data = fetch_check(opener=Mock(side_effect=OSError('offline')))
        self.assertEqual(result['status'], 'unavailable')
        self.assertIsNone(data)
        self.assertFalse(result['allow_model'])

    def test_bad_json(self):
        opener = Mock()
        opener.return_value.__enter__ = Mock(return_value=Mock(read=Mock(return_value=b'broken')))
        opener.return_value.__exit__ = Mock(return_value=False)
        self.assertEqual(fetch_check(opener=opener)[0]['status'], 'unavailable')

    def test_generation_time_does_not_refresh_observation(self):
        p = payload(1260)
        p['generated_at'] = NOW.isoformat()
        self.assertEqual(check(p, now=NOW)['status'], 'stale')

    def test_same_timestamp_changed_reading_has_new_id(self):
        p = payload()
        first = check(p, now=NOW)['observation_id']
        p['current']['free_spaces'] = 99
        self.assertNotEqual(first, check(p, now=NOW)['observation_id'])


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.gate = Gate(str(Path(self.tmp.name) / 'events.sqlite'))
        self.model = Mock(return_value='test notification')
        self.send = Mock(return_value='provider-receipt-1')

    def tearDown(self):
        self.tmp.cleanup()

    def run_gate(self, data=None, **kwargs):
        args = dict(event_scope='existing-policy-window', eligible=lambda p: True,
                    model=self.model, send=self.send, clock=lambda: NOW)
        args.update(kwargs)
        return self.gate.run(payload() if data is None else data, **args)

    def test_nonfresh_never_calls_model_or_send(self):
        for p in [payload(1260), payload(-1), {}, dict(payload(), status='unavailable')]:
            self.assertEqual(self.run_gate(p)['outcome'], 'blocked')
        self.model.assert_not_called()
        self.send.assert_not_called()

    def test_condition_false(self):
        self.assertEqual(self.run_gate(eligible=lambda p: False)['outcome'], 'condition_not_met')
        self.model.assert_not_called()
        self.send.assert_not_called()

    def test_persistent_dedup(self):
        self.assertEqual(self.run_gate()['outcome'], 'handed_off')
        self.gate = Gate(self.gate.database)
        self.assertEqual(self.run_gate()['outcome'], 'duplicate_or_pending')
        self.send.assert_called_once()

    def test_changed_reading_same_timestamp(self):
        self.run_gate()
        p = payload()
        p['current']['free_spaces'] = 99
        self.assertEqual(self.run_gate(p)['outcome'], 'handed_off')
        self.assertEqual(self.send.call_count, 2)

    def test_redeployment_does_not_duplicate(self):
        self.run_gate()
        p = payload()
        p['generated_at'] = NOW.isoformat()
        p['source_revision'] = 'different-deployment'
        self.assertEqual(self.run_gate(p)['outcome'], 'duplicate_or_pending')

    def test_expires_while_model_runs(self):
        times = iter([NOW, NOW, NOW + timedelta(seconds=901)])
        self.assertEqual(self.run_gate(clock=lambda: next(times))['outcome'], 'expired_before_send')
        self.model.assert_called_once()
        self.send.assert_not_called()

    def test_failure_does_not_retry(self):
        self.send.side_effect = OSError('ambiguous delivery')
        self.assertEqual(self.run_gate()['outcome'], 'delivery_unknown')
        self.assertEqual(self.run_gate()['outcome'], 'duplicate_or_pending')
        self.send.assert_called_once()

    def test_model_failure(self):
        self.model.side_effect = RuntimeError('failed')
        self.assertEqual(self.run_gate()['outcome'], 'delivery_unknown')
        self.send.assert_not_called()

    def test_model_cannot_override_gate(self):
        self.model.return_value = {'status': 'fresh', 'allow_notification': True}
        self.assertEqual(self.run_gate()['outcome'], 'invalid_model_output')
        self.send.assert_not_called()

    def test_pending_crash_blocks_retry(self):
        self.model.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.run_gate()
        self.assertEqual(self.run_gate()['outcome'], 'duplicate_or_pending')
        self.send.assert_not_called()

    def test_concurrent_runs_only_handoff_once(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.run_gate()['outcome'], range(4)))
        self.assertEqual(results.count('handed_off'), 1)
        self.assertEqual(results.count('duplicate_or_pending'), 3)
        self.send.assert_called_once()


if __name__ == '__main__':
    unittest.main()
