"""Local implementation verification, not Phase C accuracy/parameter selection."""
import argparse
from copy import deepcopy
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import platform
import statistics
import time

from bootstrap_online import ROOT
from calendar_features import load_calendar
from export_prediction import build_payload, shift
from online_export import cached_payload
from online_ingest import ingest
from online_model import OnlineModel, canonical
from online_state import load_state, save_state
from observations import canonical_csv_bytes


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--csv',default='data/parking.csv');p.add_argument('--state',required=True);p.add_argument('--out',required=True)
    args=p.parse_args()
    cfg=json.loads((ROOT/'config.json').read_text());options=json.loads((ROOT/'online_config.json').read_text());calendar=load_calendar()
    source=Path(args.csv).read_bytes();before=hashlib.sha256(source).hexdigest()
    state,recovered=load_state(args.state,cfg,options,calendar)
    assert not recovered
    origin=datetime.fromisoformat(state['last_event'])
    batch=build_payload(args.csv,cfg,origin)
    cached=cached_payload(args.csv,args.state,cfg,options,origin)
    assert batch['status']==cached['status']
    parity=max((abs(a['free_spaces']-b['free_spaces']) for a,b in zip(batch['predictions'],cached['predictions'])),default=0.)
    shadow_parity=max((abs(a['free_spaces']-b['free_spaces']) for a,b in zip(batch['shadow_predictions'],cached['shadow_predictions'])),default=0.)
    assert len(batch['predictions'])==len(cached['predictions'])==4
    assert parity<1e-8 and shadow_parity<1e-8
    timings=[]
    for _ in range(5):
        tick=time.perf_counter();restored,_=load_state(args.state,cfg,options,calendar)
        model=OnlineModel(cfg,options,calendar,restored);old=canonical(model.state)
        result=ingest(model,args.csv)
        assert result['new_rows']==0 and canonical(model.state)==old
        timings.append(time.perf_counter()-tick)
    output=Path(args.out);output.parent.mkdir(parents=True,exist_ok=True)
    probe=output.parent/'synthetic_probe.csv'
    extra=io.StringIO(newline='');writer=csv.writer(extra)
    raw=state['recent'][-1]
    writer.writerow([shift(origin,300).isoformat(),raw['free']-1,1306,0])
    probe.write_bytes(source+extra.getvalue().encode())
    model=OnlineModel(cfg,options,calendar,deepcopy(state))
    tick=time.perf_counter();update=ingest(model,probe)
    probe_state=output.parent/'synthetic_probe_state.json'
    save_state(probe_state,model.state,cfg,options,calendar)
    update_seconds=time.perf_counter()-tick
    assert update['new_rows']==1
    fixed=canonical(model.state);ingest(model,probe);assert fixed==canonical(model.state)
    assert hashlib.sha256(Path(args.csv).read_bytes()).hexdigest()==before
    report=dict(phase='B implementation verification only; no accuracy or promotion claim',
                checked_at=datetime.now(timezone.utc).isoformat(),python=platform.python_version(),
                source_sha256=before,canonical_source_sha256=hashlib.sha256(canonical_csv_bytes(source)).hexdigest(),source_rows=state['quality']['input_rows'],
                state_bytes=Path(args.state).stat().st_size,baseline_buckets=len(state['baseline']['buckets']),
                pending_forecasts=len(state['pending']),clipping=state['quality'],
                fixed_M3_batch_cached_max_difference_spaces=parity,
                calendar_shadow_batch_cached_max_difference_spaces=shadow_parity,
                unchanged_input_median_restore_ingest_check_seconds=statistics.median(timings),
                synthetic_one_row_update_and_atomic_save_seconds=update_seconds,
                synthetic_one_row_ingestion=update,raw_csv_unchanged=True,
                optimizers=state['optimizers'],online_mode=options['mode'])
    output.write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
