from contextlib import redirect_stdout
from copy import deepcopy
import csv
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from calendar_features import load_calendar
from online_model import canonical
from phase_c import ROOT, prepare, replay, select, load_cache, metric_rows


class PhaseCTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT)
        self.folder=Path(self.tmp.name);self.path=self.folder/'source.csv';self.cache=self.folder/'cache.json.gz'
        self.cfg=json.loads((ROOT/'config.json').read_text());self.cfg['minimum_history_days']=1
        self.opts=json.loads((ROOT/'online_config.json').read_text());self.opts['minimum_training_pairs']=2
        tz=ZoneInfo(self.cfg['timezone']);rows=[]
        for d in range(5):
            for i in range(49):
                t=datetime(2026,10,24+d,8,tzinfo=tz)+timedelta(minutes=5*i)
                free=500-7*i+d*11
                if d==3 and i==24:free=-9999
                rows.append([t.isoformat(),free,1306,0])
        with self.path.open('w',newline='') as f:
            writer=csv.writer(f);writer.writerow(['timestamp','free_spaces','total_spaces','occupancy']);writer.writerows(rows)
        with patch('phase_c.settings',return_value=(self.cfg,self.opts,load_calendar())),redirect_stdout(io.StringIO()):
            self.data=prepare(self.path,'2026-10-28',self.cache)

    def tearDown(self):self.tmp.cleanup()

    def test_cached_optimizer_matches_actual_engine_and_keeps_labels_causal(self):
        rows,optim,error=replay(self.data,self.opts,'2026-10-15','2026-10-28')
        self.assertEqual(error,0)
        for h,state in optim.items():
            real=self.data['metadata']['reference_optimizers'][str(h)]
            self.assertEqual(state['alpha'],real['alpha']);self.assertEqual(state['pairs'],real['pairs'])
        self.assertTrue(rows)
        for row in rows:
            self.assertLess(datetime.fromisoformat(row['origin']),datetime.fromisoformat(row['target']))
            self.assertGreaterEqual(datetime.fromisoformat(row['available_at']).timestamp(),datetime.fromisoformat(row['target']).timestamp()+90)

    def test_future_learning_labels_cannot_change_earlier_predictions(self):
        before,_,_=replay(self.data,self.opts,'2026-10-15','2026-10-28')
        changed=deepcopy(self.data)
        boundary=datetime(2026,10,28,tzinfo=ZoneInfo(self.cfg['timezone'])).timestamp()
        for event in changed['events']:
            if event['kind']=='learn' and event['now']>=boundary:event['z']+=500
        after,_,_=replay(changed,self.opts,'2026-10-15','2026-10-28')
        left=[(r['id'],r['online']) for r in before if r['origin'][:10]<'2026-10-28']
        right=[(r['id'],r['online']) for r in after if r['origin'][:10]<'2026-10-28']
        self.assertEqual(left,right)

    def test_final_cache_cannot_be_used_to_select_settings(self):
        data=deepcopy(self.data);data['metadata']['end']='2026-11-11'
        with patch('phase_c.load_cache',return_value=data):
            with self.assertRaises(ValueError):select(self.cache,self.folder/'not_created')
        self.assertFalse((self.folder/'not_created').exists())

    def test_clean_pairs_exclude_clipped_origin_and_outcome(self):
        rows,_,_=replay(self.data,self.opts,'2026-10-15','2026-10-28')
        self.assertTrue(any(not r['clean'] for r in rows))
        self.assertTrue(all(r['clean']==(0<=r['raw_origin']<=1300 and 0<=r['raw_actual']<=1300) for r in rows))
        metrics=metric_rows(rows,self.cfg)
        self.assertTrue(any(r['cohort']=='clean' for r in metrics))
        self.assertTrue(any(r['cohort']=='clipping_involved' for r in metrics))


if __name__=='__main__':unittest.main()
