import base64
from copy import deepcopy
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import subprocess
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from calendar_features import load_calendar, baseline_history
from export_prediction import baseline, build_payload, normalize_observations, shift
from observations import normalize_free, parse_record, read_csv, summarize
from online_model import OnlineModel, canonical
from online_ingest import ingest
from online_export import cached_payload
from online_state import StateError, load_state, save_state, state_lock, seal, decode
from state_remote import GitHubState, RemoteError

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT/'config.json').read_text())
OPTIONS = json.loads((ROOT/'online_config.json').read_text())
CAL = load_calendar()
TZ = ZoneInfo(CFG['timezone'])


class OnlineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT)
        self.path = Path(self.tmp.name)/'parking.csv'
        self.state_path = Path(self.tmp.name)/'state.json'
        self.cfg = {**CFG, 'minimum_history_days':2, 'parameters_available_from':'2026-01-01T00:00:00-05:00'}
        self.options = {**OPTIONS, 'minimum_training_pairs':3}
        self.rows = []
        for day in range(6):
            start = datetime(2026,10,28+day if day < 4 else 31,8,tzinfo=TZ) if day < 4 else datetime(2026,11,day-3,8,tzinfo=TZ)
            for i in range(61):
                t = shift(start,i*300)
                free = 500-4*i+day*17+30*math.sin(i/7)
                self.rows.append([t.isoformat(),free,1306,0])
        self.write(self.rows)

    def tearDown(self): self.tmp.cleanup()

    def write(self, rows, append=False):
        with self.path.open('a' if append else 'w',newline='',encoding='utf-8') as f:
            writer=csv.writer(f)
            if not append: writer.writerow(['timestamp','free_spaces','total_spaces','occupancy'])
            writer.writerows(rows)

    def model(self): return OnlineModel(self.cfg,self.options,CAL)

    def test_clipping_all_finite_values_and_capacities(self):
        for value in [-1e308,-148,-1,0,1300,1301,1e308]:
            for cap in [1,1300,1306,1e308]:
                row=parse_record(dict(timestamp=self.rows[-1][0],free_spaces=value,total_spaces=cap),TZ)
                self.assertEqual(normalize_free(row['free']),max(0,min(1300,value)))
        for value in [float('nan'),float('inf'),-float('inf')]:
            with self.assertRaises(ValueError):normalize_free(value)
        for cap in [0,-1,float('inf'),float('nan')]:
            with self.assertRaises(ValueError):parse_record(dict(timestamp=self.rows[-1][0],free_spaces=10,total_spaces=cap),TZ)
        for timestamp in ['bad','2026-01-01T12:00:00']:
            with self.assertRaises(ValueError):parse_record(dict(timestamp=timestamp,free_spaces=10,total_spaces=1300),TZ)
        self.write([[self.rows[-1][0],1e308,1e308,0]]*2)
        readings,_,_=read_csv(self.path,TZ)
        self.assertTrue(math.isfinite(readings[0]['free']))
        self.assertEqual(normalize_free(readings[0]['free']),1300)

    def test_baseline_parity_including_calendar_shadow_and_fallback(self):
        self.rows[1][1] = -10000; self.rows[70][1] = 9000
        self.write(self.rows)
        model=self.model();ingest(model,self.path)
        readings,_,_=read_csv(self.path,TZ)
        normalized,_,_=normalize_observations(readings,self.cfg)
        origin=readings[-1]['time']
        history=[r for r in normalized if r['time'].date()<origin.date()]
        for horizon in [0,15,30,60,120,600]:
            target=shift(origin,horizon*60)
            self.assertAlmostEqual(model.baseline(target)[0],baseline(history,target,self.cfg),places=9)
            chosen,policy=baseline_history(history,target,CAL)
            candidate,actual_policy=model.baseline(target,True)
            self.assertAlmostEqual(candidate,baseline(chosen,target,self.cfg),places=9)
            self.assertEqual(actual_policy,policy)

    def test_date_means_not_observation_weighted_means(self):
        rows=[['2026-09-01T11:50:00-04:00',100,1300,0],['2026-09-01T12:00:00-04:00',100,1300,0],
              ['2026-09-01T12:10:00-04:00',100,1300,0],['2026-09-08T12:00:00-04:00',1000,1300,0],
              ['2026-09-15T12:00:00-04:00',500,1300,0]]
        self.write(rows);engine=self.model();ingest(engine,self.path)
        readings,_,_=read_csv(self.path,TZ)
        target=readings[-1]['time']
        expected=baseline(readings[:-1],target,self.cfg)
        self.assertGreater(expected,600)
        self.assertAlmostEqual(engine.baseline(target)[0],expected,places=10)

    def test_incremental_replay_idempotence_and_restart(self):
        whole=self.model();ingest(whole,self.path)
        split=self.model();self.write(self.rows[:151]);ingest(split,self.path)
        save_state(self.state_path,split.state,self.cfg,self.options,CAL)
        restored,_=load_state(self.state_path,self.cfg,self.options,CAL)
        split=OnlineModel(self.cfg,self.options,CAL,restored)
        self.write(self.rows[151:],True);ingest(split,self.path)
        self.assertEqual(canonical(whole.state),canonical(split.state))
        before=canonical(split.state)
        self.assertEqual(ingest(split,self.path)['new_rows'],0)
        self.assertEqual(before,canonical(split.state))

    def test_windows_linux_newlines_and_bom_preserve_checkpoint(self):
        model=self.model();ingest(model,self.path)
        before=canonical(model.state)
        raw=self.path.read_bytes()
        self.path.write_bytes(raw.replace(b'\r\n',b'\n'))
        self.assertEqual(ingest(model,self.path)['new_rows'],0)
        self.assertEqual(before,canonical(model.state))
        self.path.write_bytes(b'\xef\xbb\xbf'+self.path.read_bytes())
        self.assertEqual(ingest(model,self.path)['new_rows'],0)
        self.assertEqual(before,canonical(model.state))

    def test_duplicate_median_then_clip_and_late_open_day(self):
        self.write(self.rows[:100])
        model=self.model();ingest(model,self.path)
        duplicate=[self.rows[99][0],-5000,9999,0]
        self.write([duplicate],True);ingest(model,self.path)
        key=model.state['last_event']
        grouped=model.state['active'][key]
        expected=normalize_free(summarize(grouped,TZ)['free'])
        raw=next(r for r in model.state['recent'] if r['timestamp']==key)
        self.assertEqual(normalize_free(raw['free']),expected)
        self.assertEqual(model.state['diagnostics']['duplicates'],1)
        self.assertEqual(model.state['quality']['clipped_to_zero'],1)
        # Historical closed-day revisions are diagnosed, not silently applied.
        before=deepcopy(model.state['baseline'])
        self.write([self.rows[0]],True)
        with self.assertRaises(StateError):ingest(model,self.path)
        self.assertEqual(before,model.state['baseline'])
        self.assertEqual(model.state['diagnostics']['quarantined_closed_day'],1)

    def test_out_of_order_new_tail_sorted(self):
        whole=self.model();ingest(whole,self.path)
        self.write(self.rows[:100]);split=self.model();ingest(split,self.path)
        self.write(list(reversed(self.rows[100:])),True);ingest(split,self.path)
        # Checkpoint hashes reflect byte order; learned state does not.
        split.state['checkpoint']=whole.state['checkpoint']
        self.assertEqual(canonical(split.state),canonical(whole.state))

    def test_csv_rewrite_truncation_and_partial_line(self):
        model=self.model();ingest(model,self.path)
        self.write(self.rows[:-1])
        with self.assertRaises(StateError):ingest(model,self.path)
        self.write(self.rows);data=self.path.read_bytes();self.path.write_bytes(data.replace(b'1306',b'1307',1))
        with self.assertRaises(StateError):ingest(model,self.path)
        self.write(self.rows)
        tail=b'2026-11-02T14:05:00-05:00,300,1300,0'
        with self.path.open('ab') as f:f.write(tail)
        self.assertEqual(ingest(model,self.path)['new_rows'],0)
        with self.path.open('ab') as f:f.write(b'\n')
        self.assertEqual(ingest(model,self.path)['new_rows'],1)

    def test_future_records_and_past_queries_fail_closed(self):
        model=self.model()
        with self.assertRaises(StateError):ingest(model,self.path,datetime(2026,10,1,tzinfo=timezone.utc))
        self.assertEqual(model.state['checkpoint']['offset'],0)
        model=self.model();ingest(model,self.path)
        origin=datetime.fromisoformat(model.state['last_event'])
        with self.assertRaises(ValueError):model.forecasts(shift(origin,-300))
        with self.assertRaises(ValueError):model.baseline(shift(origin,-300))

    def test_pending_delayed_outcome_gap_expiration_no_duplicate_training(self):
        self.write(self.rows[:150]);model=self.model();ingest(model,self.path)
        pending=deepcopy(model.state['pending'])
        self.assertTrue(pending)
        origin=datetime.fromisoformat(model.state['last_event'])
        # Reach a target exactly, but do not seal until target + tolerance.
        target=datetime.fromisoformat(pending[-4]['target'])
        self.write([[target.isoformat(),-10000,1306,0]],True);ingest(model,self.path)
        self.assertTrue(any(p['id']==pending[-4]['id'] for p in model.state['pending']))
        # A long gap must settle the retained target before ring pruning.
        before=model.state['optimizers']['15']['pairs']
        later=shift(target,5*3600)
        self.write([[later.isoformat(),500,1300,0]],True);ingest(model,self.path)
        self.assertGreater(model.state['optimizers']['15']['pairs'],before)
        self.assertGreater(sum(o['expired'] for o in model.state['optimizers'].values()),0)
        state=canonical(model.state);ingest(model,self.path);self.assertEqual(state,canonical(model.state))

    def test_outcome_window_closes_and_ties_choose_earlier_clipped_label(self):
        engine=self.model();target=datetime(2026,11,2,12,tzinfo=TZ)
        engine.state['pending']=[dict(id='tie',target=target.astimezone(timezone.utc).isoformat(),horizon=15,x=1000,baseline_target=0)]
        engine.state['recent']=[dict(timestamp=shift(target,-90).astimezone(timezone.utc).isoformat(),free=-10000,capacity=1306),
                                dict(timestamp=shift(target,90).astimezone(timezone.utc).isoformat(),free=900,capacity=1306)]
        engine._mature(shift(target,89))
        self.assertEqual(engine.state['optimizers']['15']['pairs'],0)
        engine._mature(shift(target,90))
        self.assertEqual(engine.state['optimizers']['15']['pairs'],1)
        self.assertEqual(engine.state['optimizers']['15']['sxz'],0)

    def test_no_temporal_leakage_in_prefix_or_frozen_features(self):
        self.write(self.rows[:180]);a=self.model();ingest(a,self.path)
        pending=deepcopy(a.state['pending']);prefix=canonical(a.state)
        self.write(self.rows[:180]);b=self.model();ingest(b,self.path)
        self.assertEqual(prefix,canonical(b.state))
        self.write(self.rows[180:],True);ingest(a,self.path)
        # Features in the previously issued records are immutable dictionaries.
        self.assertEqual(pending,deepcopy(b.state['pending']))
        self.assertTrue(all(datetime.fromisoformat(p['origin']) < datetime.fromisoformat(p['target']) for p in pending))
        # Parse the entire future suffix up front, then perturb that suffix.
        # The actual ingestion path must still issue identical prefix forecasts.
        cut=self.rows[179][0]
        def prefix_forecasts(rows):
            self.write(rows);engine=self.model();captured=[]
            observe=engine.observe_group
            def recording(group):
                observe(group)
                if group['timestamp']<=datetime.fromisoformat(cut).astimezone(timezone.utc).isoformat():
                    captured.extend(deepcopy(engine.state['last_forecasts']))
            with patch.object(engine,'observe_group',side_effect=recording):ingest(engine,self.path)
            return captured
        changed=deepcopy(self.rows)
        for r in changed[180:]:r[1]=-1e308
        self.assertEqual(prefix_forecasts(self.rows),prefix_forecasts(changed))

    def test_extreme_labels_bound_alpha_and_invalid_options(self):
        opts={**self.options,'minimum_training_pairs':1,'prior_strength':1e-8}
        engine=OnlineModel(self.cfg,opts,CAL)
        target=datetime(2026,11,2,12,tzinfo=TZ)
        key=target.astimezone(timezone.utc).isoformat()
        engine.state['recent']=[dict(timestamp=key,free=1e308,capacity=1)]
        engine.state['pending']=[dict(id='probe',target=key,horizon=15,x=1,baseline_target=0)]
        engine._mature(shift(target,91))
        self.assertEqual(engine.state['optimizers']['15']['alpha'],2)
        self.assertEqual(engine.state['optimizers']['15']['clipped_updates'],1)
        with self.assertRaises(ValueError):OnlineModel(self.cfg,{**opts,'optimizer_half_life_days':0},CAL)

    def test_atomic_recovery_schema_policy_and_lock(self):
        model=self.model();ingest(model,self.path)
        save_state(self.state_path,model.state,self.cfg,self.options,CAL)
        save_state(self.state_path,model.state,self.cfg,self.options,CAL)
        self.state_path.write_text('{broken',encoding='utf-8')
        restored,recovered=load_state(self.state_path,self.cfg,self.options,CAL)
        self.assertTrue(recovered);self.assertEqual(restored,model.state)
        with state_lock(self.state_path):
            with self.assertRaises(StateError):
                with state_lock(self.state_path):pass
            remote=GitHubState('dngnyoo/tdeck-parking-data','model-state','unused')
            with patch.object(remote,'api') as api:
                with self.assertRaises(StateError):remote.restore(self.state_path,self.cfg,self.options,CAL)
                api.assert_not_called()
        for field,value in [('schema_version',999),('normalization_policy','old_reject_policy')]:
            changed=deepcopy(model.state);changed[field]=value
            with self.assertRaises(StateError):decode(canonical(seal(changed)),self.cfg,self.options,CAL)
        with self.assertRaises(StateError):load_state(Path(self.tmp.name)/'missing',self.cfg,self.options,CAL)
        with patch('online_state.os.replace',side_effect=OSError('simulated crash')):
            with self.assertRaises(OSError):save_state(Path(self.tmp.name)/'crash.json',model.state,self.cfg,self.options,CAL)
        self.assertFalse((Path(self.tmp.name)/'crash.json').exists())

    def test_prediction_stability_and_cached_export_compatibility(self):
        model=self.model();ingest(model,self.path)
        save_state(self.state_path,model.state,self.cfg,self.options,CAL)
        origin=datetime.fromisoformat(model.state['last_event'])
        batch=build_payload(self.path,self.cfg,origin)
        cached=cached_payload(self.path,self.state_path,self.cfg,self.options,origin)
        self.assertEqual(cached['status'],batch['status'])
        for left,right in zip(cached['predictions'],batch['predictions']):
            self.assertAlmostEqual(left['free_spaces'],right['free_spaces'],places=8)
        for left,right in zip(cached['shadow_predictions'],batch['shadow_predictions']):
            self.assertAlmostEqual(left['free_spaces'],right['free_spaces'],places=8)
        self.assertEqual(len(cached['online_shadow_predictions']),4)
        for item in model.state['optimizers'].values():
            self.assertTrue(0 <= item['alpha'] <= 2)
            self.assertTrue(math.isfinite(item['sxx']))
        self.assertTrue(all(0<=p['prediction']<=1300 for p in model.state['last_forecasts']))
        stale=cached_payload(self.path,self.state_path,self.cfg,self.options,shift(origin,1201))
        self.assertEqual(stale['status'],'stale');self.assertFalse(stale['online_shadow_predictions'])

    def test_dst_same_clock_is_distinct_and_circular_window(self):
        rows=[['2026-10-31T23:55:00-04:00',100,1300,0],['2026-11-01T01:30:00-04:00',-9999,1300,0],
              ['2026-11-01T01:30:00-05:00',9999,1306,0],['2026-11-02T00:00:00-05:00',400,1300,0]]
        self.write(rows);model=self.model();ingest(model,self.path)
        self.assertEqual(sum(model.state['baseline']['date_counts']),2)
        history,_,_=read_csv(self.path,TZ);history,_,_=normalize_observations(history[:-1],self.cfg)
        origin=datetime.fromisoformat(rows[-1][0])
        for h in [0,90,120]:
            self.assertAlmostEqual(model.baseline(shift(origin,h*60))[0],baseline(history,shift(origin,h*60),self.cfg))

    def test_remote_conflicting_save_has_no_overwrite_retry(self):
        model=self.model();ingest(model,self.path);save_state(self.state_path,model.state,self.cfg,self.options,CAL)
        Path(str(self.state_path)+'.remote').write_text(json.dumps(dict(sha='expected-old-sha',content='old')))
        remote=GitHubState('dngnyoo/tdeck-parking-data','model-state','not-a-real-token')
        with patch.object(remote,'api',side_effect=RemoteError('HTTP 409')) as api:
            with self.assertRaises(RemoteError):remote.save(self.state_path,self.cfg,self.options,CAL)
            self.assertEqual(api.call_count,1)
            self.assertEqual(api.call_args.args[2]['sha'],'expected-old-sha')
        with self.assertRaises(ValueError):GitHubState('dngnyoo/tdeck-parking-data','main','unused')

    def test_remote_corrupt_head_recovers_compatible_history(self):
        model=self.model();ingest(model,self.path)
        valid=canonical(seal(model.state))
        remote=GitHubState('dngnyoo/tdeck-parking-data','model-state','unused')
        def api(method,path,body=None):
            if '/git/ref/' in path:return {'object':{'sha':'head'}}
            return [{'sha':'head'},{'sha':'prior'}]
        def content(ref):return ('{broken','current-blob') if ref=='head' else (valid,'prior-blob')
        with patch.object(remote,'api',side_effect=api),patch.object(remote,'content',side_effect=content):
            self.assertTrue(remote.restore(self.state_path,self.cfg,self.options,CAL))
        restored,_=load_state(self.state_path,self.cfg,self.options,CAL)
        self.assertEqual(restored,model.state)
        self.assertEqual(json.loads(Path(str(self.state_path)+'.remote').read_text())['sha'],'current-blob')

    def test_disabled_cli_does_not_create_state_and_corrupt_export_falls_back(self):
        untouched=Path(self.tmp.name)/'never-created.json'
        run=subprocess.run([sys.executable,str(ROOT/'update_online.py'),'--state',str(untouched)],capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertFalse(untouched.exists())
        self.state_path.write_text('{broken')
        online=Path(self.tmp.name)/'shadow-options.json';online.write_text(json.dumps({**OPTIONS,'mode':'shadow'}))
        config=Path(self.tmp.name)/'test-config.json';config.write_text(json.dumps(self.cfg))
        output=Path(self.tmp.name)/'payload.json'
        origin=self.rows[-1][0]
        run=subprocess.run([sys.executable,str(ROOT/'export_prediction.py'),'--csv',str(self.path),
            '--state',str(self.state_path),'--online-config',str(online),'--config',str(config),'--now',origin,'--out',str(output)],capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stderr)
        payload=json.loads(output.read_text(encoding='utf-8'))
        self.assertEqual(payload['status'],'ok')
        self.assertEqual(len(payload['predictions']),4)
        self.assertEqual(payload['online']['mode'],'fallback')
        self.assertEqual(payload['model'],'M3')

    def test_evaluation_harness_uses_clipped_labels_and_separates_coverage(self):
        from evaluate_online import evaluate
        self.rows[-8][1]=-9999;self.rows[-7][1]=9999;self.write(self.rows)
        rows,metrics,meta=evaluate(self.path,self.cfg,self.options,'2026-11-02','2026-11-02',stride=4)
        self.assertTrue(rows)
        self.assertEqual({r['model'] for r in rows},{'legacy_fixed','clip_fixed_batch','clip_online_batch','clip_online_incremental'})
        self.assertTrue(all(0<=r['actual']<=1300 and 0<=r['prediction']<=1300 for r in rows))
        self.assertLess(meta['max_batch_incremental_prediction_difference'],1e-8)
        self.assertTrue(any(r['cohort']=='common' for r in metrics))
        self.assertTrue(any(r['warning_type']=='warning' for r in metrics))


if __name__=='__main__': unittest.main()
