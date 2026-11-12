"""Full final-test forecast audit against original batch baseline, with memoization.

Memoization is exact: with a fixed origin-date history cutoff the batch baseline
depends only on target weekday and minute. The target-instant factor cancels.
"""
import argparse
from datetime import datetime
from pathlib import Path
import time
from zoneinfo import ZoneInfo

from export_prediction import baseline, instant
from observations import read_csv, normalize_free
from phase_c import load_cache, replay, settings, json_file


def audit(csv_path,cache_path,selection_path,out):
    import json
    cfg,defaults,_=settings()
    selected=json.loads(Path(selection_path).read_text(encoding='utf-8'))['selected_options']
    cache=load_cache(cache_path)
    rows,optim,_=replay(cache,selected,'2026-10-29','2026-11-11')
    raw,_,_=read_csv(csv_path,ZoneInfo(cfg['timezone']))
    raw=[r for r in raw if r['time'].date().isoformat()<='2026-11-11']
    by_origin={}
    for r in rows:by_origin.setdefault(r['origin'],[]).append(r)
    previous=[];history=[];day=None;memo={};queries=0;checked=0;max_feature=0.;max_fixed=0.;max_online=0.
    fixed_prediction_seconds=0.;batch_seconds=0.;tick=time.perf_counter()
    for raw_row in raw:
        origin=raw_row['time'];current_day=origin.date().isoformat()
        if current_day!=day:
            history=list(previous);memo={};day=current_day
        if origin.isoformat() in by_origin:
            def batch(target):
                nonlocal queries,batch_seconds
                key=(target.weekday(),target.hour*60+target.minute)
                if key not in memo:
                    before=time.perf_counter();memo[key]=baseline(history,target,cfg)
                    batch_seconds+=time.perf_counter()-before;queries+=1
                return memo[key]
            b_now=batch(origin)
            for r in by_origin[origin.isoformat()]:
                b_target=batch(datetime.fromisoformat(r['target']))
                x=normalize_free(raw_row['free'])-b_now
                max_feature=max(max_feature,abs(x-r['x']),abs(b_target-r['baseline_target']))
                alpha0=next(h['alpha'] for h in cfg['horizons'] if h['minutes']==r['h'])
                max_fixed=max(max_fixed,abs(normalize_free(b_target+alpha0*x)-r['fixed']))
                max_online=max(max_online,abs(normalize_free(b_target+r['alpha']*x)-r['online']))
                checked+=1
        previous.append({**raw_row,'free':normalize_free(raw_row['free'])})
    if max(max_feature,max_fixed,max_online)>1e-8:raise AssertionError('Full parity audit failed')
    result=dict(checked_final_test_forecasts=checked,checked_origins=len(by_origin),
                exact_batch_queries=queries,memoization='per origin-date cutoff, target weekday/minute',
                max_baseline_or_origin_feature_error=max_feature,max_fixed_prediction_error=max_fixed,
                max_online_prediction_error=max_online,seconds=time.perf_counter()-tick,batch_query_seconds=batch_seconds)
    json_file(out,result);print(result,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--csv',required=True);p.add_argument('--cache',required=True)
    p.add_argument('--selection',required=True);p.add_argument('--out',required=True);a=p.parse_args()
    audit(a.csv,a.cache,a.selection,a.out)
