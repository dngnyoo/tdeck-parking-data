"""Offline real-data checkpoint/fault stress for frozen Phase C settings."""
import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time

from bootstrap_online import ROOT
from calendar_features import load_calendar
from export_prediction import build_payload
from online_ingest import ingest
from online_model import OnlineModel, canonical
from online_state import save_state, load_state, StateError
from phase_c import settings, json_file, digest


def stress(csv_path,selection_path,scratch,out):
    cfg,defaults,calendar=settings()
    selected=json.loads(Path(selection_path).read_text(encoding='utf-8'))['selected_options']
    scratch=Path(scratch);scratch.mkdir(parents=True,exist_ok=True)
    source=Path(csv_path).read_bytes();raw_hash=digest(source)
    lines=source.splitlines(keepends=True)
    header=lines[0]
    rows=[r for r in lines[1:] if r[:10]<=b'2026-11-11']
    prefix=[r for r in rows if r[:10]<=b'2026-10-28']
    full=header+b''.join(rows)
    live=scratch/'collector_snapshot.csv';live.write_bytes(header+b''.join(prefix))
    initial=OnlineModel(cfg,selected,calendar)
    started=time.perf_counter();bootstrap=ingest(initial,live)
    initial_seconds=time.perf_counter()-started
    initial_state=deepcopy(initial.state)
    initial_path=scratch/'initial.json';save_state(initial_path,initial_state,cfg,selected,calendar)
    scenarios=[]

    # One long skipped-Actions period catches up all new observations in order.
    live.write_bytes(full)
    continuous=OnlineModel(cfg,selected,calendar,deepcopy(initial_state))
    tick=time.perf_counter();new=ingest(continuous,live);seconds=time.perf_counter()-tick
    expected=canonical(continuous.state);expected_sha=digest(expected.encode())
    scenarios.append(dict(scenario='all_test_rows_in_one_catchup',new_rows=new['new_rows'],
                          seconds=seconds,state_sha256=expected_sha,equals_uninterrupted=True))

    # Restore at every 12-hour batch boundary; no forecasting/training rows lost.
    frequent=OnlineModel(cfg,selected,calendar,deepcopy(initial_state))
    batch_path=scratch/'restart.json';save_state(batch_path,frequent.state,cfg,selected,calendar)
    boundaries=[]
    for i,row in enumerate(rows):
        if row[:10]<=b'2026-10-28':continue
        key=(row[:10],int(row[11:13])//12)
        if i==len(rows)-1 or key!=(rows[i+1][:10],int(rows[i+1][11:13])//12):boundaries.append(i+1)
    tick=time.perf_counter();new_rows=0
    for stop in boundaries:
        live.write_bytes(header+b''.join(rows[:stop]))
        restored,_=load_state(batch_path,cfg,selected,calendar)
        frequent=OnlineModel(cfg,selected,calendar,restored)
        result=ingest(frequent,live);new_rows+=result['new_rows']
        save_state(batch_path,frequent.state,cfg,selected,calendar)
    if canonical(frequent.state)!=expected:raise AssertionError('Restart/catchup changed learned state')
    scenarios.append(dict(scenario='restart_every_12_hours',batches=len(boundaries),new_rows=new_rows,
                          seconds=time.perf_counter()-tick,state_sha256=digest(canonical(frequent.state).encode()),equals_uninterrupted=True))

    # Partial success, then corrupt current checkpoint: resume older good backup.
    recover_path=scratch/'recovery.json';save_state(recover_path,initial_state,cfg,selected,calendar)
    middle=len(prefix)+(len(rows)-len(prefix))//2
    live.write_bytes(header+b''.join(rows[:middle]))
    partial=OnlineModel(cfg,selected,calendar,deepcopy(initial_state));ingest(partial,live)
    save_state(recover_path,partial.state,cfg,selected,calendar)
    recover_path.write_text('{corrupted-current-state',encoding='utf-8')
    recovered,used_backup=load_state(recover_path,cfg,selected,calendar)
    live.write_bytes(full);engine=OnlineModel(cfg,selected,calendar,recovered)
    tick=time.perf_counter();result=ingest(engine,live);save_state(recover_path,engine.state,cfg,selected,calendar)
    if not used_backup or canonical(engine.state)!=expected:raise AssertionError('Corruption recovery diverged')
    scenarios.append(dict(scenario='corrupt_current_restore_previous',new_rows=result['new_rows'],seconds=time.perf_counter()-tick,
                          recovered_previous=used_backup,state_sha256=digest(canonical(engine.state).encode()),equals_uninterrupted=True))

    # Healthy same-input reruns cannot learn a pair again or change state bytes.
    times=[]
    for _ in range(5):
        tick=time.perf_counter();restored,_=load_state(recover_path,cfg,selected,calendar)
        engine=OnlineModel(cfg,selected,calendar,restored);result=ingest(engine,live)
        if result['new_rows'] or canonical(engine.state)!=expected:raise AssertionError('Idempotence failed')
        times.append(time.perf_counter()-tick)
    scenarios.append(dict(scenario='unchanged_snapshot_five_restarts',new_rows=0,
                          median_seconds=statistics.median(times),equals_uninterrupted=True))

    # Unrecoverable state must still permit the fixed M3 exporter to succeed.
    recover_path.write_text('{bad',encoding='utf-8')
    recover_path.with_suffix('.json.previous').write_text('{bad-backup',encoding='utf-8')
    try:load_state(recover_path,cfg,selected,calendar)
    except StateError:pass
    else:raise AssertionError('Broken state accepted')
    local_options=scratch/'offline_shadow_options.json'
    json_file(local_options,{**selected,'mode':'shadow'})
    last_timestamp=rows[-1].decode().split(',')[0]
    payload_path=scratch/'fallback.json'
    tick=time.perf_counter()
    process=subprocess.run([sys.executable,str(ROOT/'export_prediction.py'),'--csv',str(live),'--state',str(recover_path),
                            '--online-config',str(local_options),'--now',last_timestamp,'--out',str(payload_path)],capture_output=True,text=True)
    if process.returncode:raise AssertionError(process.stderr)
    actual=json.loads(payload_path.read_text(encoding='utf-8'))
    fixed=build_payload(live,cfg,datetime.fromisoformat(last_timestamp))
    if actual['predictions']!=fixed['predictions'] or actual['online']['mode']!='fallback':raise AssertionError('Fallback not fixed M3')
    scenarios.append(dict(scenario='both_checkpoints_corrupt_fixed_M3_fallback',returncode=process.returncode,
                          status=actual['status'],forecast_count=len(actual['predictions']),equals_fixed_M3=True,
                          seconds=time.perf_counter()-tick))

    # No recovery path may rewrite the raw source or acknowledge a changed prefix.
    changed=bytearray(full)
    changed[len(header)+1]=ord('9') if changed[len(header)+1]!=ord('9') else ord('8')
    live.write_bytes(changed)
    engine=OnlineModel(cfg,selected,calendar,deepcopy(continuous.state))
    before=canonical(engine.state)
    try:ingest(engine,live)
    except StateError:pass
    else:raise AssertionError('Rewritten historical prefix accepted')
    if canonical(engine.state)!=before:raise AssertionError('Failed prefix check mutated state')
    scenarios.append(dict(scenario='historical_prefix_corruption',rejected=True,state_unchanged=True))
    if digest(Path(csv_path).read_bytes())!=raw_hash:raise AssertionError('Original source was modified')
    report=dict(source_sha256=raw_hash,selection_sha256=digest(Path(selection_path).read_bytes()),
                initial_rows=bootstrap['new_rows'],test_rows=len(rows)-len(prefix),initialization_seconds=initial_seconds,
                final_state_bytes=len(expected.encode()),reference_state_sha256=expected_sha,
                final_optimizers=continuous.state['optimizers'],scenarios=scenarios,
                source_unchanged=True,production_mode=settings()[1]['mode'],
                note='Local real-data offline stress. No GitHub state branch, settings, or Pages deployment was changed.')
    json_file(out,report);print(json.dumps(report,indent=2),flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--csv',required=True);p.add_argument('--selection',required=True)
    p.add_argument('--scratch',required=True);p.add_argument('--out',required=True)
    args=p.parse_args();stress(args.csv,args.selection,args.scratch,args.out)


if __name__=='__main__':main()
