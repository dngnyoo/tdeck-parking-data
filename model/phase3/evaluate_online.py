"""Chronological comparison harness. No parameter selection or promotion.

Phase C can invoke this with alternative past-validated parameters. The batch
and incremental online variants deliberately share the same as-of learned alpha:
their difference isolates baseline implementation rather than another model.
"""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import csv
import hashlib
import json
import math
from pathlib import Path
import time
from zoneinfo import ZoneInfo

from bootstrap_online import ROOT
from calendar_features import load_calendar
from export_prediction import baseline, recent_history_ok, instant, shift
from observations import read_csv, normalize_free, POLICY
from online_model import OnlineModel, stamp, canonical, initial_state


def scores(records, cutoff):
    if not records:
        return dict(n=0,mae=None,rmse=None,bias=None,precision=None,recall=None,f1=None,tp=0,fp=0,fn=0)
    errors=[r['prediction']-r['actual'] for r in records]
    tp=sum(r['prediction']<=cutoff and r['actual']<=0 for r in records)
    fp=sum(r['prediction']<=cutoff and r['actual']>0 for r in records)
    fn=sum(r['prediction']>cutoff and r['actual']<=0 for r in records)
    return dict(n=len(records),mae=sum(abs(x) for x in errors)/len(errors),
                rmse=math.sqrt(sum(x*x for x in errors)/len(errors)),bias=sum(errors)/len(errors),
                precision=tp/(tp+fp) if tp+fp else None,recall=tp/(tp+fn) if tp+fn else None,
                f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None,tp=tp,fp=fp,fn=fn)


def write_csv(path, rows):
    if not rows:
        Path(path).write_text('',encoding='utf-8')
        return
    with Path(path).open('w',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
        writer.writeheader();writer.writerows(rows)


def evaluate(path, cfg, options, start, end, stride=1):
    tz=ZoneInfo(cfg['timezone'])
    raw,quality,_=read_csv(path,tz)
    # Restrict input itself to evaluation end: a future suffix cannot affect
    # either optimizer updates or availability of target labels.
    raw=[r for r in raw if r['time'].date().isoformat()<=end]
    model=OnlineModel(cfg,options,load_calendar())
    records,previous=[],[]
    legacy_previous=[]
    history,legacy_history=[],[]
    previous_day=None
    elapsed_update=0.0
    max_parity=0.0
    eligible=0
    for row in raw:
        origin=row['time'];day=origin.date().isoformat()
        if day!=previous_day:
            history=list(previous)
            legacy_history=list(legacy_previous)
            previous_day=day
        normalized={**row,'free':normalize_free(row['free'])}
        legacy_valid=(-100<=row['free']<=1400 and abs(row['capacity']-1300)<=100)
        group=dict(timestamp=stamp(origin),free=[row['free']],capacity=[row['capacity']])
        tick=time.perf_counter();model.observe_group(group);elapsed_update+=time.perf_counter()-tick
        previous.append(normalized)
        if legacy_valid:legacy_previous.append(normalized)
        if day<start or not model.state['last_forecasts']:
            continue
        eligible+=1
        if (eligible-1)%stride:
            continue
        b_now=baseline(history,origin,cfg)
        current=normalized['free']
        legacy_eligible=(legacy_valid and len({r['time'].date() for r in legacy_history})>=cfg['minimum_history_days']
                         and recent_history_ok(legacy_previous,origin,cfg))
        legacy_now=baseline(legacy_history,origin,cfg) if legacy_eligible else None
        for h,p in zip(cfg['horizons'],model.state['last_forecasts']):
            target=shift(origin,h['minutes']*60)
            base=baseline(history,target,cfg)
            alpha=model.state['optimizers'][str(h['minutes'])]['alpha']
            batch_online=normalize_free(base+alpha*(current-b_now))
            max_parity=max(max_parity,abs(batch_online-p['prediction']))
            predictions={'clip_fixed_batch':normalize_free(base+h['alpha']*(current-b_now)),
                         'clip_online_batch':batch_online,'clip_online_incremental':p['prediction']}
            if legacy_eligible:
                predictions['legacy_fixed']=normalize_free(baseline(legacy_history,target,cfg)+h['alpha']*(current-legacy_now))
            for name,value in predictions.items():
                records.append(dict(model=name,origin=origin.isoformat(),target=target.isoformat(),
                     horizon=h['minutes'],prediction=value,alpha=alpha if 'online' in name else h['alpha'],
                     weekday=origin.weekday(),time_of_day=('00-06' if origin.hour<6 else '06-10' if origin.hour<10 else '10-14' if origin.hour<14 else '14-18' if origin.hour<18 else '18-24')))
    # Outcome matching only reveals normalized labels once their matching
    # window has closed. Raw values are retained in the evaluation artifact.
    times=[instant(r['time']).timestamp() for r in raw]
    import bisect
    matched=[]
    tolerance=options['outcome_tolerance_seconds']
    for r in records:
        target=datetime.fromisoformat(r['target']).timestamp()
        if not times or target+tolerance>times[-1]:continue
        lo=bisect.bisect_left(times,target-tolerance);hi=bisect.bisect_right(times,target+tolerance)
        candidates=raw[lo:hi]
        if not candidates:continue
        outcome=min(candidates,key=lambda x:(abs(instant(x['time']).timestamp()-target),instant(x['time'])))
        # Legacy's original quality policy defines its own outcome coverage.
        if r['model']=='legacy_fixed' and not(-100<=outcome['free']<=1400 and abs(outcome['capacity']-1300)<=100):continue
        matched.append({**r,'actual':normalize_free(outcome['free']),'raw_actual':outcome['free'],
                        'outcome_timestamp':outcome['time'].isoformat(),'match_error_seconds':instant(outcome['time']).timestamp()-target})
    cohorts=defaultdict(set)
    for r in matched:cohorts[(r['origin'],r['horizon'])].add(r['model'])
    all_models={'legacy_fixed','clip_fixed_batch','clip_online_batch','clip_online_incremental'}
    for r in matched:r['common_origin']=cohorts[(r['origin'],r['horizon'])]==all_models
    metrics=[]
    grouped=defaultdict(list)
    for r in matched:
        for cohort in ['available']+(['common'] if r['common_origin'] else []):
            for breakdown,value in [('all','all'),('weekday',str(r['weekday'])),('time_of_day',r['time_of_day'])]:
                grouped[(cohort,r['model'],r['horizon'],breakdown,value)].append(r)
            if r['weekday']<5 and r['time_of_day']=='06-10':
                grouped[(cohort,r['model'],r['horizon'],'busy_weekday_morning','06-10')].append(r)
    for (cohort,name,h,breakdown,value),rows in sorted(grouped.items()):
        warning=next(x['warning_cutoff'] for x in cfg['horizons'] if x['minutes']==h)
        for kind,cutoff in [('full',0),('warning',warning)]:
            metrics.append(dict(cohort=cohort,model=name,horizon=h,breakdown=breakdown,group=value,
                                warning_type=kind,cutoff=cutoff,**scores(rows,cutoff)))
    metadata=dict(phase='Phase C harness; execution does not authorize promotion',normalization_policy=POLICY,
                  source_sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest(),start=start,end=end,stride=stride,
                  options=options,quality=quality,parsed_unique_observations=len(raw),
                  incremental_update_seconds=elapsed_update,mean_update_ms=1000*elapsed_update/max(1,len(raw)),
                  state_bytes=len(canonical(model.state).encode()),max_batch_incremental_prediction_difference=max_parity,
                  pending_predictions=len(model.state['pending']),optimizers=model.state['optimizers'],
                  notes=['No automatic hyperparameter selection or model promotion.',
                         'Online variants share as-of alpha to isolate exact baseline implementation.',
                         'Common and available cohorts separate coverage changes from prediction changes.',
                         'Quality diagnostics describe the complete supplied CSV; replay is truncated to end.',
                         'Five-minute samples and overlapping horizons are not independent events.'])
    return matched,metrics,metadata


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--csv',default='data/parking.csv');p.add_argument('--out',required=True)
    p.add_argument('--start',required=True);p.add_argument('--end',required=True)
    p.add_argument('--stride',type=int,default=1,help='Score every Nth eligible origin; learning still processes all origins')
    p.add_argument('--config',default=str(ROOT/'config.json'));p.add_argument('--online-config',default=str(ROOT/'online_config.json'))
    p.add_argument('--alpha-max',type=float);p.add_argument('--half-life',type=float);p.add_argument('--prior-strength',type=float)
    args=p.parse_args()
    if args.stride<1 or args.start>args.end:p.error('Invalid stride or date interval')
    cfg=json.loads(Path(args.config).read_text());options=json.loads(Path(args.online_config).read_text())
    for source,key in [('alpha_max','alpha_max'),('half_life','optimizer_half_life_days'),('prior_strength','prior_strength')]:
        if getattr(args,source) is not None:options[key]=getattr(args,source)
    started=time.perf_counter();records,metrics,metadata=evaluate(args.csv,cfg,options,args.start,args.end,args.stride)
    metadata['total_seconds']=time.perf_counter()-started
    output=Path(args.out);output.mkdir(parents=True,exist_ok=True)
    write_csv(output/'predictions.csv',records);write_csv(output/'metrics.csv',metrics)
    (output/'metadata.json').write_text(json.dumps(metadata,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps(metadata,indent=2))


if __name__=='__main__':main()
