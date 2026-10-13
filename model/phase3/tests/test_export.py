import csv
from datetime import datetime,timedelta
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from export_prediction import build_payload,baseline,read_observations,shift,elapsed

ROOT=Path(__file__).resolve().parents[1]
CFG=json.loads((ROOT/'config.json').read_text())
TZ=ZoneInfo(CFG['timezone'])

class PredictorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT);self.path=Path(self.tmp.name)/'parking.csv'
        self.origin=datetime(2026,10,13,12,tzinfo=TZ)
        self.rows=[]
        for days in range(21,0,-1):
            for minutes in range(10*60,15*60+1,5):
                day=(self.origin-timedelta(days=days)).replace(hour=0,minute=0)
                t=day+timedelta(minutes=minutes)
                self.rows.append([t.isoformat(),200-(minutes-600)/2,1300,0])
        for lag in range(30,-1,-5):self.rows.append([(self.origin-timedelta(minutes=lag)).isoformat(),-10+lag,1300,100])
        self.save()
    def tearDown(self):self.tmp.cleanup()
    def save(self):
        with self.path.open('w',newline='') as f:
            w=csv.writer(f);w.writerow(['timestamp','free_spaces','total_spaces','occupancy']);w.writerows(self.rows)
    def test_negative_and_read_only(self):
        before=self.path.read_bytes();p=build_payload(self.path,CFG,self.origin)
        self.assertEqual(p['status'],'ok');self.assertEqual(p['current']['free_spaces'],-10)
        self.assertTrue(any(x['free_spaces']<0 for x in p['predictions']))
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(p['source_sha256'],hashlib.sha256(before).hexdigest())
    def test_stale_at_twenty_minutes(self):
        self.assertEqual(build_payload(self.path,CFG,self.origin+timedelta(seconds=1200))['status'],'ok')
        p=build_payload(self.path,CFG,self.origin+timedelta(seconds=1201))
        self.assertEqual(p['status'],'stale');self.assertEqual(p['predictions'],[])
    def test_future_sensor(self):
        p=build_payload(self.path,CFG,self.origin-timedelta(seconds=121));self.assertEqual(p['reason'],'future_timestamp');self.assertFalse(p['predictions'])
    def test_gap(self):
        self.rows=self.rows[:-7]+self.rows[-1:];self.save()
        self.assertEqual(build_payload(self.path,CFG,self.origin)['reason'],'insufficient_recent_history')
    def test_pre_selection(self):
        cfg={**CFG,'parameters_available_from':(self.origin+timedelta(days=1)).isoformat()}
        self.assertEqual(build_payload(self.path,cfg,self.origin)['reason'],'parameters_not_available')
    def test_invalid_and_duplicate(self):
        self.rows.append(self.rows[-1]);self.rows.append(['bad',float('nan'),1300,0]);self.save()
        p=build_payload(self.path,CFG,self.origin);self.assertEqual(p['quality']['invalid_rows'],1);self.assertEqual(p['current']['free_spaces'],-10)
    def test_no_same_day_in_baseline(self):
        d,_=read_observations(self.path,TZ);history=[x for x in d if x['time'].date()<self.origin.date()]
        expected=baseline(history,self.origin+timedelta(minutes=30),CFG)
        self.assertAlmostEqual(expected,125)
        p=build_payload(self.path,CFG,self.origin)
        self.assertAlmostEqual(p['predictions'][1]['free_spaces'],-25)
    def test_capacity_anomaly(self):
        self.rows[-1][1]=1400;self.save()
        self.assertEqual(build_payload(self.path,CFG,self.origin)['reason'],'capacity_anomaly')
    def test_dst_elapsed_horizon(self):
        fall=datetime.fromisoformat('2026-11-01T01:50:00-04:00').astimezone(TZ)
        target=shift(fall,1800)
        self.assertEqual(target.isoformat(),'2026-11-01T01:20:00-05:00')
        self.assertEqual(elapsed(target,fall),1800)
        spring=datetime.fromisoformat('2027-03-14T01:50:00-05:00').astimezone(TZ)
        self.assertEqual(shift(spring,1800).isoformat(),'2027-03-14T03:20:00-04:00')
    def test_dst_repeated_clock_readings_are_distinct(self):
        self.rows=[['2026-11-01T01:30:00-04:00',50,1300,0],['2026-11-01T01:30:00-05:00',-5,1300,100]];self.save()
        readings,_=read_observations(self.path,TZ)
        self.assertEqual(len(readings),2);self.assertEqual(readings[-1]['free'],-5)
        self.assertEqual(elapsed(readings[-1]['time'],readings[0]['time']),3600)
    def test_dst_stale_sensor(self):
        self.rows.append(['2026-11-01T01:50:00-04:00',20,1300,0]);self.save()
        now=datetime.fromisoformat('2026-11-01T01:11:00-05:00')
        self.assertEqual(build_payload(self.path,CFG,now)['reason'],'stale_observation')

if __name__=='__main__':unittest.main()
