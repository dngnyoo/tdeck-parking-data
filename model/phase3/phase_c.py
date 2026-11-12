"""Offline Phase C: causal feature/label cache, validation-only selection, test.

Never writes production configuration, CSV, remote state, or Pages settings.
"""
import argparse
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import gzip
import hashlib
import itertools
import json
import math
from pathlib import Path
import random
import time

from bootstrap_online import ROOT
from calendar_features import load_calendar
from evaluate_online import scores, write_csv
from export_prediction import baseline, instant, shift
from observations import read_csv, normalize_free, canonical_csv_bytes
from online_model import OnlineModel, canonical, stamp
from online_state import atomic_write

PROTOCOL = ROOT.parent.parent/'docs/online-learning/phase_c/PROTOCOL.md'


def digest(value):
    return hashlib.sha256(value).hexdigest()


def protocol_digest():
    return digest(canonical_csv_bytes(PROTOCOL.read_bytes()))


def settings():
    cfg=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
    options=json.loads((ROOT/'online_config.json').read_text(encoding='utf-8'))
    if options['mode']!='off':
        raise ValueError('Phase C requires online mode off')
    return cfg,options,load_calendar()


def json_file(path, value):
    atomic_write(path,json.dumps(value,indent=2,allow_nan=False)+'\n')


class TraceModel(OnlineModel):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.events=[]
        self.features={}

    def _mature(self,now):
        # Instrument the actual matching order before calling the real optimizer.
        tolerance=self.options['outcome_tolerance_seconds']
        for p in self.state['pending']:
            target=datetime.fromisoformat(p['target']).timestamp()
            if now.timestamp()<target+tolerance:continue
            candidates=[r for r in self.state['recent'] if abs(datetime.fromisoformat(r['timestamp']).timestamp()-target)<=tolerance]
            if not candidates:continue
            outcome=min(candidates,key=lambda r:(abs(datetime.fromisoformat(r['timestamp']).timestamp()-target),r['timestamp']))
            actual=normalize_free(outcome['free'])
            self.events.append(dict(kind='learn',id=p['id'],h=p['horizon'],now=now.timestamp(),
                                    x=p['x'],z=actual-p['baseline_target']))
            f=self.features[p['id']]
            f.update(actual=actual,raw_actual=outcome['free'],outcome_timestamp=outcome['timestamp'],
                     available_at=stamp(now),match_error_seconds=datetime.fromisoformat(outcome['timestamp']).timestamp()-target)
        super()._mature(now)


class LegacyFixed(OnlineModel):
    def _mature(self,now):
        # Only legacy fixed-alpha forecasts are needed, no legacy alpha fitting.
        self.state['pending']=[]


def prepare(csv_path,end,cache_path):
    tick=time.perf_counter()
    cfg,options,calendar=settings()
    raw,quality,_=read_csv(csv_path,__import__('zoneinfo').ZoneInfo(cfg['timezone']))
    raw=[r for r in raw if r['time'].date().isoformat()<=end]
    engine=TraceModel(cfg,options,calendar)
    legacy=LegacyFixed(cfg,options,calendar)
    history,legacy_history,previous,legacy_previous=[],[],[],[]
    day=None;audits=0;max_error=0.;update_seconds=0.
    for i,row in enumerate(raw):
        current_day=row['time'].date().isoformat()
        if current_day!=day:
            history=list(previous);legacy_history=list(legacy_previous);day=current_day
        origin=row['time'];key=stamp(origin)
        group=dict(timestamp=key,free=[row['free']],capacity=[row['capacity']])
        legacy_valid=-100<=row['free']<=1400 and abs(row['capacity']-1300)<=100
        if legacy_valid:legacy.observe_group(deepcopy(group))
        started=time.perf_counter();engine.observe_group(group);update_seconds+=time.perf_counter()-started
        legacy_predictions={p['horizon']:p['prediction'] for p in legacy.state['last_forecasts']} if legacy_valid and legacy.state['last_event']==key else {}
        for p in engine.state['last_forecasts']:
            h=p['horizon']
            info=next(x for x in cfg['horizons'] if x['minutes']==h)
            f=dict(id=p['id'],origin=origin.isoformat(),target=shift(origin,h*60).isoformat(),h=h,
                   raw_origin=row['free'],origin_clipped=not 0<=row['free']<=1300,
                   x=p['x'],baseline_target=p['baseline_target'],
                   fixed=normalize_free(p['baseline_target']+info['alpha']*p['x']),
                   legacy_fixed=legacy_predictions.get(h),reference_prediction=p['prediction'],
                   weekday=origin.weekday(),hour=origin.hour)
            engine.features[p['id']]=f
            engine.events.append(dict(kind='forecast',id=p['id'],h=h,x=p['x'],baseline_target=p['baseline_target']))
        if engine.state['last_forecasts'] and (i%1000==0 or (day=='2026-11-01' and origin.hour==1 and origin.minute in [0,5,50,55])):
            b_now=baseline(history,origin,cfg)
            for p in engine.state['last_forecasts']:
                target=shift(origin,p['horizon']*60)
                error=max(abs(baseline(history,target,cfg)-p['baseline_target']),
                          abs(normalize_free(row['free'])-b_now-p['x']))
                max_error=max(max_error,error);audits+=1
                if p['horizon'] in legacy_predictions:
                    expected=normalize_free(baseline(legacy_history,target,cfg)+next(x['alpha'] for x in cfg['horizons'] if x['minutes']==p['horizon'])*(normalize_free(row['free'])-baseline(legacy_history,origin,cfg)))
                    max_error=max(max_error,abs(expected-legacy_predictions[p['horizon']]))
            if max_error>1e-8:raise AssertionError('Batch/incremental feature mismatch')
        normalized={**row,'free':normalize_free(row['free'])}
        previous.append(normalized)
        if legacy_valid:legacy_previous.append(normalized)
    meta=dict(end=end,source_sha256=digest(Path(csv_path).read_bytes()),
              canonical_source_sha256=digest(canonical_csv_bytes(Path(csv_path).read_bytes())),
              protocol_sha256=protocol_digest(),cfg=cfg,options=options,
              rows=len(raw),start=raw[0]['time'].isoformat(),last=raw[-1]['time'].isoformat(),
              quality=quality,seconds=time.perf_counter()-tick,engine_update_seconds=update_seconds,
              state_bytes=len(canonical(engine.state).encode()),baseline_audits=audits,
              max_baseline_audit_difference=max_error,reference_optimizers=engine.state['optimizers'])
    result=dict(metadata=meta,events=engine.events,forecasts=engine.features)
    Path(cache_path).parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(cache_path,'wt',encoding='utf-8') as stream:json.dump(result,stream,separators=(',',':'),allow_nan=False)
    # Real engine checkpoints are useful for separate state-stability stress.
    json_file(str(cache_path)+'.metadata.json',meta)
    print(json.dumps(meta,indent=2),flush=True)
    return result


def load_cache(path):
    with gzip.open(path,'rt',encoding='utf-8') as stream:return json.load(stream)


def replay(cache,options,start,end):
    cfg=cache['metadata']['cfg']
    optim={h['minutes']:dict(alpha=h['alpha'],initial=h['alpha'],sxx=0.,sxz=0.,pairs=0,last=None) for h in cfg['horizons']}
    predictions={};max_reference_error=0.
    prior=options['prior_strength']*(options['prior_feature_scale_spaces']/1300)**2
    for event in cache['events']:
        opt=optim[event['h']]
        if event['kind']=='learn':
            if opt['last'] is not None:
                decay=2**(-(event['now']-opt['last'])/(options['optimizer_half_life_days']*86400))
                opt['sxx']*=decay;opt['sxz']*=decay
            x,z=event['x']/1300,event['z']/1300
            opt['sxx']+=x*x;opt['sxz']+=x*z;opt['pairs']+=1;opt['last']=event['now']
            candidate=(prior*opt['initial']+opt['sxz'])/max(1e-12,prior+opt['sxx'])
            opt['alpha']=max(options['alpha_min'],min(options['alpha_max'],candidate)) if opt['pairs']>=options['minimum_training_pairs'] else opt['initial']
        else:
            f=cache['forecasts'][event['id']]
            value=normalize_free(event['baseline_target']+opt['alpha']*event['x'])
            max_reference_error=max(max_reference_error,abs(value-f['reference_prediction']))
            if start<=f['origin'][:10]<=end and f['target'][:10]<=end and 'actual' in f:
                predictions[event['id']]=dict(online=value,alpha=opt['alpha'])
    rows=[]
    for key,p in predictions.items():
        f=cache['forecasts'][key]
        rows.append({**f,**p,'clean':not f['origin_clipped'] and 0<=f['raw_actual']<=1300,
                     'outcome_clipped':not 0<=f['raw_actual']<=1300})
    return rows,optim,max_reference_error


def metric_rows(rows,cfg,breakdowns=True):
    groups=defaultdict(list)
    for r in rows:
        cohorts=['all','clean' if r['clean'] else 'clipping_involved']
        if r['legacy_fixed'] is not None and -100<=r['raw_actual']<=1400:cohorts.append('legacy_common')
        for cohort in cohorts:
            for model in ['fixed','online']+(['legacy_fixed'] if cohort=='legacy_common' else []):
                sample=dict(prediction=r[model],actual=r['actual'])
                groups[(cohort,model,r['h'],'all','all')].append(sample)
                if breakdowns:
                    groups[(cohort,model,r['h'],'weekday',str(r['weekday']))].append(sample)
                    period='00-06' if r['hour']<6 else '06-10' if r['hour']<10 else '10-14' if r['hour']<14 else '14-18' if r['hour']<18 else '18-24'
                    groups[(cohort,model,r['h'],'time_of_day',period)].append(sample)
                    if r['weekday']<5 and 6<=r['hour']<10:groups[(cohort,model,r['h'],'busy_weekday_morning','06-10')].append(sample)
    result=[]
    for (cohort,model,h,breakdown,group),samples in sorted(groups.items()):
        cutoff=next(x['warning_cutoff'] for x in cfg['horizons'] if x['minutes']==h)
        for kind,threshold in [('full',0),('warning',cutoff)]:
            result.append(dict(cohort=cohort,model=model,horizon=h,breakdown=breakdown,group=group,
                               warning_type=kind,cutoff=threshold,**scores(samples,threshold)))
    return result


def objective(rows,cfg):
    by={h['minutes']:[] for h in cfg['horizons']}
    for r in rows:by[r['h']].append(abs(r['online']-r['actual']))
    if any(not x for x in by.values()):raise ValueError('Missing validation horizon support')
    return sum(sum(x)/len(x) for x in by.values())/len(by)


def select(cache_path,out):
    cache=load_cache(cache_path)
    if cache['metadata']['end']!='2026-10-28':raise ValueError('Selection requires validation-only cache')
    cfg,defaults,_=settings()
    rows,default_optim,error=replay(cache,defaults,'2026-10-15','2026-10-28')
    if error>1e-10:raise AssertionError('Cached optimizer does not match real implementation')
    candidates=[];best=None;best_rows=None;best_options=None
    for half,prior,upper in itertools.product([7,14,28],[50,200,800],[1.5,2.,3.]):
        options={**defaults,'optimizer_half_life_days':half,'prior_strength':prior,'alpha_max':upper}
        result,_,_=replay(cache,options,'2026-10-15','2026-10-28')
        score=objective(result,cfg)
        distance=abs(math.log2(half/14))+abs(math.log2(prior/200))+abs(upper-2)
        candidate=dict(half_life_days=half,prior_strength=prior,alpha_max=upper,mean_horizon_mae=score,
                       distance_to_default=distance,**{f'mae_{h}':sum(abs(r['online']-r['actual']) for r in result if r['h']==h)/sum(r['h']==h for r in result) for h in [15,30,60,120]})
        candidates.append(candidate)
        tie=(distance,half,prior,upper)
        if best is None or score<best[0]-1e-9 or (abs(score-best[0])<=1e-9 and tie<best[1]):
            best=(score,tie);best_rows=result;best_options=options
        print(f'validation half={half} prior={prior} bound={upper}: {score:.6f}',flush=True)
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    selection=dict(validation_start='2026-10-15',validation_end='2026-10-28',test_start='2026-10-29',test_end='2026-11-11',
                   selected_options=best_options,objective='equal-weight mean of four validation MAEs on all matched pairs',
                   validation_objective=best[0],candidate_count=len(candidates),protocol_sha256=protocol_digest(),
                   validation_cache_sha256=digest(Path(cache_path).read_bytes()),validation_source_sha256=cache['metadata']['source_sha256'],
                   cached_replay_vs_real_engine_max_error=error,selected_before_test_scoring=True,
                   frozen_at=datetime.now(timezone.utc).isoformat())
    json_file(out/'selection.json',selection)
    write_csv(out/'validation_candidates.csv',candidates)
    write_csv(out/'validation_metrics.csv',metric_rows(best_rows,cfg))
    write_csv(out/'validation_predictions.csv',best_rows)
    print(json.dumps(selection,indent=2),flush=True)


def paired_intervals(rows):
    rng=random.Random(20261112)
    days=[(datetime(2026,10,29)+timedelta(days=i)).date().isoformat() for i in range(14)]
    result=[]
    for cohort in ['all','clean']:
        for h in [15,30,60,120]:
            subset=[r for r in rows if r['h']==h and (cohort=='all' or r['clean'])]
            by={d:[0.,0] for d in days}
            for r in subset:
                item=by[r['origin'][:10]]
                item[0]+=abs(r['online']-r['actual'])-abs(r['fixed']-r['actual']);item[1]+=1
            samples=[]
            for _ in range(2000):
                picked=[by[rng.choice(days)] for _ in days]
                denominator=sum(p[1] for p in picked)
                if denominator:samples.append(sum(p[0] for p in picked)/denominator)
            samples.sort()
            def quantile(p):
                index=(len(samples)-1)*p;a=math.floor(index);b=math.ceil(index)
                return samples[a]*(b-index)+samples[b]*(index-a) if a!=b else samples[a]
            result.append(dict(cohort=cohort,horizon=h,n=len(subset),days_with_pairs=sum(p[1]>0 for p in by.values()),
                        delta_mae_online_minus_fixed=sum(p[0] for p in by.values())/max(1,len(subset)),
                        ci025=quantile(.025),ci975=quantile(.975),day_block_resamples=len(samples)))
    return result


def test(cache_path,selection_path,out):
    cache=load_cache(cache_path);selection=json.loads(Path(selection_path).read_text())
    if cache['metadata']['end']!='2026-11-11' or selection['validation_end']!='2026-10-28':raise ValueError('Invalid frozen evaluation periods')
    if selection['protocol_sha256']!=protocol_digest():raise ValueError('Protocol changed after selection')
    cfg,defaults,_=settings()
    _,_,parity=replay(cache,defaults,'2026-10-29','2026-11-11')
    if parity>1e-10:raise AssertionError('Real engine/cache mismatch')
    rows,optim,_=replay(cache,selection['selected_options'],'2026-10-29','2026-11-11')
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    write_csv(out/'test_predictions.csv',rows)
    write_csv(out/'test_metrics.csv',metric_rows(rows,cfg))
    write_csv(out/'paired_day_bootstrap.csv',paired_intervals(rows))
    json_file(out/'test_manifest.json',dict(selection_sha256=digest(Path(selection_path).read_bytes()),
              test_cache_sha256=digest(Path(cache_path).read_bytes()),protocol_sha256=protocol_digest(),
              source_metadata=cache['metadata'],selected_options=selection['selected_options'],final_optimizers=optim,
              matched_pairs=len(rows),cached_replay_vs_real_engine_max_error=parity,
              online_mode_unchanged=defaults['mode'],scored_at=datetime.now(timezone.utc).isoformat()))
    for m in metric_rows(rows,cfg,False):
        if m['cohort'] in ['all','clean'] and m['warning_type']=='full':print(m,flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('prepare');p.add_argument('--csv',required=True);p.add_argument('--end',required=True);p.add_argument('--cache',required=True)
    p=sub.add_parser('select');p.add_argument('--cache',required=True);p.add_argument('--out',required=True)
    p=sub.add_parser('test');p.add_argument('--cache',required=True);p.add_argument('--selection',required=True);p.add_argument('--out',required=True)
    args=parser.parse_args()
    if args.command=='prepare':prepare(args.csv,args.end,args.cache)
    elif args.command=='select':select(args.cache,args.out)
    else:test(args.cache,args.selection,args.out)


if __name__=='__main__':main()
