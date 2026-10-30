from datetime import timedelta
import tempfile
from pathlib import Path
import unittest
import io
from contextlib import redirect_stdout
from unittest.mock import patch

from notification_gate.command import execute, report, main
from notification_gate.freshness import check
from notification_gate.test_gate import NOW, payload


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.db = str(Path(self.tmp.name) / 'state.sqlite')

    def tearDown(self):
        self.tmp.cleanup()

    def test_fresh_output_and_repeat_suppression(self):
        result, text = execute(payload(), self.db, 'existing-job', clock=lambda: NOW)
        self.assertEqual(result['outcome'], 'handed_off')
        self.assertIn('300초 / 허용 한도: 1200초', text)
        self.assertIn('100 / 1300', text)
        _, repeated = execute(payload(), self.db, 'existing-job', clock=lambda: NOW)
        self.assertEqual(repeated, 'NO_REPLY')

    def test_all_nonfresh_output_is_silent(self):
        for p in [payload(1260), payload(-1), {}, dict(payload(), status='unavailable')]:
            result, text = execute(p, self.db, 'existing-job', clock=lambda: NOW)
            self.assertEqual(text, 'NO_REPLY')
            self.assertFalse(result['allow_notification'])

    def test_valid_forecast(self):
        p = payload()
        p['predictions'] = [dict(horizon_minutes=60, free_spaces=80,
                                warning_cutoff=50, target_time=(NOW + timedelta(minutes=55)).isoformat())]
        text = report(p, check(p, now=NOW))
        self.assertIn('60분 예측: 80면 (추정치)', text)

    def test_invalid_forecast_keeps_only_verified_current(self):
        p = payload()
        p['predictions'] = [dict(horizon_minutes=60, free_spaces=80,
                                warning_cutoff=50, target_time=NOW.isoformat())]
        text = report(p, check(p, now=NOW))
        self.assertIn('60분 예측: 검증 가능한 예측 없음', text)
        self.assertNotIn('80면', text)

    def test_cli_unavailable_suppresses_parking_output(self):
        output = io.StringIO()
        with patch('sys.argv', ['gate', '--emit', '--database', self.db, '--event-scope', 'job']), \
             patch('notification_gate.command.fetch_check', return_value=({'status': 'unavailable'}, None)), \
             redirect_stdout(output):
            self.assertEqual(main(), 1)
        self.assertEqual(output.getvalue(), 'NO_REPLY\n')

    def test_cli_stale_suppresses_parking_output(self):
        p = payload(1260)
        output = io.StringIO()
        with patch('sys.argv', ['gate', '--emit', '--database', self.db, '--event-scope', 'job']), \
             patch('notification_gate.command.fetch_check', return_value=(check(p, now=NOW), p)), \
             patch('notification_gate.command.execute', side_effect=lambda p, db, scope: execute(p, db, scope, clock=lambda: NOW)), \
             redirect_stdout(output):
            self.assertEqual(main(), 0)
        self.assertEqual(output.getvalue(), 'NO_REPLY\n')


if __name__ == '__main__':
    unittest.main()
