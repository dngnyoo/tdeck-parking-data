"""Record deployment evidence, never change models, data or workflow settings."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess


def main():
    p=argparse.ArgumentParser();p.add_argument('--csv',required=True);p.add_argument('--state',required=True)
    p.add_argument('--prediction',required=True);p.add_argument('--out',required=True);args=p.parse_args()
    payload=json.loads(Path(args.prediction).read_text(encoding='utf-8'))
    result=dict(run_id=os.environ.get('GITHUB_RUN_ID'),run_attempt=os.environ.get('GITHUB_RUN_ATTEMPT'),
                source_revision=payload['source_revision'],source_sha256=hashlib.sha256(Path(args.csv).read_bytes()).hexdigest(),
                mode=payload.get('online',{}).get('mode'),status=payload['status'],origin=payload.get('forecast_origin'),
                predictions=payload['predictions'],online=payload.get('online'))
    if result['mode']=='production':
        if payload['model']!='M3-online':raise ValueError('Wrong operational model')
        state=json.loads(Path(args.state).read_text(encoding='utf-8'))
        result.update(checkpoint=state['checkpoint'],checksum=state['checksum'],fingerprint=state['fingerprint'],
                      input_rows=state['quality']['input_rows'],optimizers=state['optimizers'])
        receipt=json.loads(Path(str(args.state)+'.remote').read_text(encoding='utf-8'))
        result['model_state_revision']=receipt['head']
        if payload['online']['state_revision']!=receipt['head']:raise ValueError('Uncommitted state in forecast')
        if payload['status']=='ok':
            forecasts={p['horizon']:p for p in state['last_forecasts']}
            for value in payload['predictions']:
                expected=forecasts[value['horizon_minutes']]
                if value['free_spaces']!=expected['prediction'] or value['alpha']!=expected['alpha']:
                    raise ValueError('Operational prediction is not the learned forecast')
    if result['source_sha256']!=payload['source_sha256']:raise ValueError('Source changed after export')
    subprocess.run(['git','diff','--exit-code','--','data/parking.csv','collect.py','.github/workflows/collect.yml'],check=True)
    out=Path(args.out);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps(result,indent=2),flush=True)


if __name__=='__main__':main()
