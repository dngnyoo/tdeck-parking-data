"""Dependency-free production M3 export; collector CSV is strictly read-only."""
import argparse
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
from zoneinfo import ZoneInfo
from calendar_features import load_calendar,classify,baseline_history

ROOT = Path(__file__).resolve().parent

def instant(t):
    return t.astimezone(timezone.utc)

def elapsed(a,b):
    return (instant(a)-instant(b)).total_seconds()

def shift(t,seconds):
    return (instant(t)+timedelta(seconds=seconds)).astimezone(t.tzinfo)

def read_observations(path, tz):
    grouped={};invalid=0
    with Path(path).open(encoding='utf-8-sig',newline='') as stream:
        reader=csv.DictReader(stream)
        if not {'timestamp','free_spaces','total_spaces'}.issubset(reader.fieldnames or []):
            raise ValueError('Required CSV columns are missing')
        for row in reader:
            try:
                t=datetime.fromisoformat(row['timestamp'])
                if t.tzinfo is None:raise ValueError('Timestamp needs timezone offset')
                y=float(row['free_spaces']);capacity=float(row['total_spaces'])
                if not math.isfinite(y) or not math.isfinite(capacity) or capacity<=0:raise ValueError('Nonfinite reading')
                grouped.setdefault(instant(t),[]).append((y,capacity))
            except (ValueError,TypeError,KeyError):invalid+=1
    readings=[dict(time=t.astimezone(tz),free=statistics.median(x[0] for x in values),capacity=statistics.median(x[1] for x in values)) for t,values in sorted(grouped.items())]
    return readings,invalid

def baseline(history,target,cfg):
    minute=target.hour*60+target.minute
    def distance(obs):
        delta=abs(obs['time'].hour*60+obs['time'].minute-minute)
        return min(delta,1440-delta)
    near=[x for x in history if distance(x)<=cfg['baseline_window_minutes']]
    same=[x for x in near if x['time'].weekday()==target.weekday()]
    selected=same or near or history
    if not selected:raise ValueError('No historical baseline support')
    by_date={}
    for row in selected:by_date.setdefault(row['time'].date(),[]).append(row)
    means=[];weights=[]
    for rows in by_date.values():
        latest=max((x['time'] for x in rows),key=instant)
        age=elapsed(target,latest)/86400
        means.append(statistics.fmean(x['free'] for x in rows));weights.append(2**(-age/cfg['baseline_half_life_days']))
    return sum(y*w for y,w in zip(means,weights))/sum(weights)

def recent_history_ok(readings,origin,cfg):
    for lag in [15,30]:
        desired=shift(origin,-lag*60)
        past=[x for x in readings if instant(x['time'])<instant(origin)]
        if not past:return False
        nearest=min(past,key=lambda x:(abs(elapsed(x['time'],desired)),instant(x['time'])))
        if abs(elapsed(nearest['time'],desired))>cfg['trend_match_tolerance_seconds']:return False
        interval=[x for x in readings if instant(nearest['time'])<=instant(x['time'])<=instant(origin)]
        if any(elapsed(b['time'],a['time'])>cfg['maximum_history_gap_seconds'] for a,b in zip(interval,interval[1:])):return False
    return True

def build_payload(csv_path,cfg,now=None,revision=None):
    tz=ZoneInfo(cfg['timezone']);now=(now or datetime.now(timezone.utc)).astimezone(tz)
    data=dict(schema_version=3,parking='T Deck',timezone=cfg['timezone'],generated_at=now.isoformat(),
              freshness_seconds=cfg['freshness_seconds'],future_clock_tolerance_seconds=cfg['future_clock_tolerance_seconds'],
              status='unavailable',reason='no_observations',current=None,history=[],predictions=[],
              model='M3',parameters_available_from=cfg['parameters_available_from'],baseline_history_policy=cfg['baseline_history_policy'],
              source_repository=cfg['source_repository'],source_revision=revision,
              source_sha256=hashlib.sha256(Path(csv_path).read_bytes()).hexdigest(),quality={})
    readings,invalid=read_observations(csv_path,tz);data['quality']['invalid_rows']=invalid
    if not readings:return data
    current=readings[-1];origin=current['time'];age=elapsed(now,origin)
    calendar=load_calendar()
    data['calendar']=classify(origin.date(),calendar)
    data['calendar']['prediction_policy']='shadow_only'
    data['shadow_predictions']=[]
    data['forecast_origin']=origin.isoformat();data['valid_until']=shift(origin,cfg['freshness_seconds']).isoformat()
    data['current']=dict(timestamp=origin.isoformat(),free_spaces=current['free'],total_spaces=current['capacity'])
    data['history']=[dict(timestamp=x['time'].isoformat(),free_spaces=x['free']) for x in readings if instant(shift(origin,-3*3600))<=instant(x['time'])<=instant(origin)]
    day=origin.replace(hour=0,minute=0,second=0,microsecond=0)
    history=[x for x in readings if instant(x['time'])<instant(day)]
    reason=None
    if age < -cfg['future_clock_tolerance_seconds']:reason='future_timestamp'
    elif age>cfg['freshness_seconds']:reason='stale_observation'
    elif current['capacity']!=cfg['capacity'] or current['free']>current['capacity']:reason='capacity_anomaly'
    elif instant(origin)<instant(datetime.fromisoformat(cfg['parameters_available_from'])):reason='parameters_not_available'
    elif len({x['time'].date() for x in history})<cfg['minimum_history_days']:reason='insufficient_calendar_history'
    elif not recent_history_ok(readings,origin,cfg):reason='insufficient_recent_history'
    if reason:
        data.update(status='stale' if reason=='stale_observation' else 'unavailable',reason=reason)
        return data
    expected_now=baseline(history,origin,cfg)
    shadow_history,now_policy=baseline_history(history,origin,calendar)
    shadow_now=baseline(shadow_history,origin,cfg)
    delta=current['free']-expected_now
    for item in cfg['horizons']:
        h=item['minutes'];target=shift(origin,h*60);value=baseline(history,target,cfg)+item['alpha']*delta
        data['predictions'].append(dict(horizon_minutes=h,target_time=target.isoformat(),free_spaces=value,
                                       full=value<=0,warning=value<=item['warning_cutoff'],warning_cutoff=item['warning_cutoff']))
        candidate_history,target_policy=baseline_history(history,target,calendar)
        candidate=baseline(candidate_history,target,cfg)+item['alpha']*(current['free']-shadow_now)
        data['shadow_predictions'].append(dict(horizon_minutes=h,target_time=target.isoformat(),free_spaces=candidate,
                                              now_calendar_policy=now_policy,target_calendar_policy=target_policy,
                                              note='Research candidate only; not displayed as operational forecast'))
    data.update(status='ok',reason=None)
    return data

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--csv',default='data/parking.csv');parser.add_argument('--config',default=str(ROOT/'config.json'));parser.add_argument('--out',default='site/data/prediction.json');parser.add_argument('--now');parser.add_argument('--revision');args=parser.parse_args()
    cfg=json.loads(Path(args.config).read_text(encoding='utf-8'))
    now=datetime.fromisoformat(args.now) if args.now else None
    if now is not None and now.tzinfo is None:raise ValueError('--now must include UTC offset')
    result=build_payload(args.csv,cfg,now,args.revision)
    path=Path(args.out);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8');temporary.replace(path)
    print('Published payload:',result['status'],result['reason'] or 'fresh', 'origin:',result.get('forecast_origin'))

if __name__=='__main__':main()
